# CentralDispatcher - replaces the per-scenario in-process ExecutionWorker.
#
# One global consumer reads dispatch rows from the `messages` table and routes
# each to either a connected execution-agent-server (via WSDispatcher) or to
# local in-process execution. Reply rows are written in the same shape as the
# old ExecutionWorker, so SchedulingAgent.collect_replies is unchanged.
import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from logger import logger
from core.message_queue import TaskMessage
from database.repositories.message_repository import MessageRepository
from database.repositories.consumer_offset_repository import ConsumerOffsetRepository
from database.repositories.task_repository import TaskRepository
from database.models.message import Message


GLOBAL_CONSUMER_ID = "execution_worker:global"


def finalize_task(scenario_id: Optional[str], task_id: str, result: dict,
                  agent_name: str = None, agent_role: str = None,
                  execution_duration: float = None) -> None:
    """Mark task state + write the reply Message row.

    Shared by both the local and the WS-forward execution paths so the reply
    wire format stays identical to the legacy ExecutionWorker. Stamps the
    execution-agent identity + duration onto the task row (the scheduling
    path does this in agent_manager; the execution path does it here).
    """
    task_repo = TaskRepository()
    msg_repo = MessageRepository()
    try:
        if result.get("success"):
            task_repo.mark_as_completed(
                task_id, json.dumps(result, ensure_ascii=False),
                agent_name=agent_name, agent_role=agent_role,
                execution_duration=execution_duration,
            )
        else:
            task_repo.mark_as_failed(task_id, result.get("error", "Unknown"))
    except Exception as e:
        logger.error(f"[Dispatcher] Failed to update task state for {task_id}: {e}")

    try:
        msg_repo.create(Message(
            scenario_id=scenario_id,
            task_id=task_id,
            from_agent="execution",
            to_agent="scheduling",
            message_type="reply",
            content=json.dumps({
                "task_id": task_id,
                "success": result.get("success", False),
                "result": result,
            }, ensure_ascii=False),
            timestamp=datetime.now(),
        ))
    except Exception as pe:
        logger.error(f"[Dispatcher] Failed to persist reply message: {pe}")


def run_task_locally(scenario_id: Optional[str], msg: TaskMessage) -> None:
    """Execute a single task in-process (the legacy path).

    Body extracted verbatim from the deleted ExecutionWorker._execute_task.
    Used both for the no-server fallback and as a safety net.
    """
    from core.agents.execution_agent import ExecutionAgent

    task_repo = TaskRepository()
    start_time = time.time()

    agent = ExecutionAgent()
    agent_config = {
        "role": msg.context.get("role", "通用助手"),
        "system_prompt": msg.context.get("system_prompt", "你是一个有帮助的智能助手。"),
    }
    agent.initialize(agent_config)

    try:
        task_repo.mark_as_started(msg.task_id)

        ctx = msg.context.copy()
        ctx["goal"] = msg.context.get("question", msg.goal)
        ctx["task_id"] = msg.task_id

        logger.info(f"[Local] Starting task {msg.task_id} role={agent_config['role']}")
        result = agent.run(msg.task_id, ctx)
        elapsed = time.time() - start_time
        logger.info(f"[Local] Task {msg.task_id} done in {elapsed:.2f}s "
                    f"success={result.get('success')}")

        finalize_task(scenario_id, msg.task_id, result,
                      agent_name="ExecutionAgent",
                      agent_role=agent_config["role"],
                      execution_duration=round(elapsed, 3))

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[Local] Task {msg.task_id} error after {elapsed:.2f}s: {e}")
        finalize_task(scenario_id, msg.task_id, {
            "success": False, "output": "", "error": str(e),
        })
    finally:
        agent.cleanup()


class CentralDispatcher:
    """Global consumer of dispatch rows; routes to WS exec-servers or local."""

    def __init__(self, max_workers: int = None):
        self.max_workers = max_workers or int(os.getenv("LOCAL_EXEC_MAX_WORKERS", "3"))
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.executor: Optional[ThreadPoolExecutor] = None
        self.ws = None  # WSDispatcher reference; set via set_ws_dispatcher()

    def set_ws_dispatcher(self, ws) -> None:
        self.ws = ws

    def start(self) -> None:
        self._bootstrap_offset()
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="central-exec",
        )
        self.thread = threading.Thread(
            target=self._run, daemon=True, name="central-dispatcher"
        )
        self.thread.start()
        logger.info(f"CentralDispatcher started (local workers={self.max_workers}, "
                    f"global consumer={GLOBAL_CONSUMER_ID})")

    def _bootstrap_offset(self) -> None:
        """First run: set the global offset to MAX(messages.id) so old dispatch
        rows from prior runs are not replayed."""
        offset_repo = ConsumerOffsetRepository()
        existing = offset_repo.find_by_id(GLOBAL_CONSUMER_ID)
        if existing:
            return
        max_id = MessageRepository().max_message_id()
        offset_repo.update_offset(GLOBAL_CONSUMER_ID, max_id)
        logger.info(f"Bootstrapped global dispatch offset to {max_id}")

    def _run(self) -> None:
        msg_repo = MessageRepository()
        offset_repo = ConsumerOffsetRepository()

        while not self._stop.is_set():
            pending = msg_repo.find_pending_dispatch_global(GLOBAL_CONSUMER_ID, limit=10)

            if not pending:
                time.sleep(0.5)
                continue

            for rec in pending:
                try:
                    content = json.loads(rec.content) if rec.content else {}
                    task_msg = TaskMessage(
                        task_id=content.get("task_id", rec.task_id),
                        parent_task_id=content.get("parent_task_id", ""),
                        goal=content.get("goal", ""),
                        context=content.get("context", {}),
                    )
                except (json.JSONDecodeError, TypeError):
                    logger.error(f"Failed to parse dispatch message {rec.id}")
                    offset_repo.update_offset(GLOBAL_CONSUMER_ID, rec.id)
                    continue

                # Advance offset before processing (at-least-once semantics)
                offset_repo.update_offset(GLOBAL_CONSUMER_ID, rec.id)

                self.executor.submit(self._dispatch_one, rec.scenario_id, task_msg)

        if self.executor:
            self.executor.shutdown(wait=True)
        logger.info("CentralDispatcher stopped")

    def _dispatch_one(self, scenario_id: Optional[str], msg: TaskMessage) -> None:
        """Route one task: WS forward (if server selected + connected),
        defer (server selected but offline), or local execution (no server)."""
        server_id = msg.context.get("server_id")

        if not server_id:
            # No server selected -> current behavior: execute locally.
            run_task_locally(scenario_id, msg)
            return

        if not self.ws:
            # WS subsystem not running -> safety fallback to local.
            logger.warning(f"[Dispatcher] No WS subsystem; task {msg.task_id} "
                           f"with server_id={server_id} runs locally")
            run_task_locally(scenario_id, msg)
            return

        if self.ws.is_connected(server_id):
            # Fire-and-forget on the WS loop; forward_task owns the lifecycle
            # (mark started -> send -> await -> finalize, or defer on drop).
            self.ws.schedule_forward(server_id, scenario_id, msg)
        else:
            # Server offline -> park until it reconnects.
            self.ws.defer(server_id, scenario_id, msg)

    def stop(self) -> None:
        self._stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
        if self.executor:
            self.executor.shutdown(wait=False)


# Global singleton (started by the WS-server process)
central_dispatcher = CentralDispatcher()
