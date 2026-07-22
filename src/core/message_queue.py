# Message Queue - DB-backed message transport with per-BOT consumer offsets
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from logger import logger
from database.repositories.message_repository import MessageRepository
from database.repositories.consumer_offset_repository import ConsumerOffsetRepository
from database.models.message import Message


@dataclass
class TaskMessage:
    """Task dispatched from SchedulingAgent to ExecutionAgent"""
    task_id: str
    parent_task_id: str
    goal: str
    context: dict = field(default_factory=dict)


@dataclass
class ReplyMessage:
    """Result from ExecutionAgent back to SchedulingAgent"""
    task_id: str
    success: bool
    result: dict = field(default_factory=dict)


def _consumer_id_execution(scenario_id: str) -> str:
    return f"execution_worker:{scenario_id}"


def _consumer_id_scheduling(scenario_id: str) -> str:
    return f"scheduling_agent:{scenario_id}"


class ExecutionWorker:
    """
    Worker for executing tasks in a scenario.
    Polls DB for dispatch messages using consumer offset.
    """

    def __init__(self, scenario_id: str, max_workers: int = 3):
        self.scenario_id = scenario_id
        self.max_workers = max_workers
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.executor: Optional[ThreadPoolExecutor] = None
        self._consumer_id = _consumer_id_execution(scenario_id)

    def start(self):
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=f"exec-worker-{self.scenario_id[:8]}"
        )
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"exec-worker-{self.scenario_id[:8]}-main",
        )
        self.thread.start()
        logger.info(f"ExecutionWorker started for scenario {self.scenario_id} "
                    f"with {self.max_workers} parallel workers (DB-backed)")

    def _run(self):
        """Main loop that polls DB for dispatch messages"""
        msg_repo = MessageRepository()
        offset_repo = ConsumerOffsetRepository()

        while not self._stop.is_set():
            # Poll DB for pending dispatch messages
            pending = msg_repo.find_pending_messages(
                self._consumer_id, self.scenario_id, "dispatch", limit=10
            )

            if not pending:
                time.sleep(0.5)
                continue

            for msg_record in pending:
                # Parse TaskMessage from DB record
                try:
                    content = json.loads(msg_record.content) if msg_record.content else {}
                    task_msg = TaskMessage(
                        task_id=content.get("task_id", msg_record.task_id),
                        parent_task_id=content.get("parent_task_id", ""),
                        goal=content.get("goal", ""),
                        context=content.get("context", {}),
                    )
                except (json.JSONDecodeError, TypeError):
                    logger.error(f"Failed to parse dispatch message {msg_record.id}")
                    offset_repo.update_offset(self._consumer_id, msg_record.id)
                    continue

                # Update offset before processing (at-least-once semantics)
                offset_repo.update_offset(self._consumer_id, msg_record.id)

                # Submit to thread pool
                self.executor.submit(self._execute_task, task_msg)

        # Wait for all submitted tasks to complete
        if self.executor:
            self.executor.shutdown(wait=True)

        logger.info(f"ExecutionWorker stopped for scenario {self.scenario_id}")

    def _execute_task(self, msg: TaskMessage):
        """Execute a single task (runs in thread pool)"""
        from core.agents.execution_agent import ExecutionAgent
        from database.repositories.task_repository import TaskRepository

        task_repo = TaskRepository()
        msg_repo = MessageRepository()
        start_time = time.time()

        agent = ExecutionAgent()
        agent_config = {
            "role": msg.context.get("role", "通用助手"),
            "system_prompt": msg.context.get("system_prompt", "你是一个有帮助的智能助手。")
        }
        agent.initialize(agent_config)

        try:
            task_repo.mark_as_started(msg.task_id)

            ctx = msg.context.copy()
            ctx["goal"] = msg.context.get("question", msg.goal)
            ctx["task_id"] = msg.task_id

            logger.info(f"[DB-Queue] Starting task {msg.task_id} with role: {agent_config['role']}")

            result = agent.run(msg.task_id, ctx)
            elapsed_time = time.time() - start_time

            if result.get("success"):
                task_repo.mark_as_completed(msg.task_id, json.dumps(result))
                logger.info(f"[DB-Queue] Task {msg.task_id} completed in {elapsed_time:.2f}s")
            else:
                task_repo.mark_as_failed(msg.task_id, result.get("error", "Unknown"))
                logger.error(f"[DB-Queue] Task {msg.task_id} failed after {elapsed_time:.2f}s: "
                           f"{result.get('error', 'Unknown')}")

            # Write reply message to DB
            try:
                msg_repo.create(Message(
                    scenario_id=self.scenario_id,
                    task_id=msg.task_id,
                    from_agent="execution",
                    to_agent="scheduling",
                    message_type="reply",
                    content=json.dumps({
                        "task_id": msg.task_id,
                        "success": result.get("success", False),
                        "result": result,
                    }, ensure_ascii=False),
                    timestamp=datetime.now(),
                ))
            except Exception as pe:
                logger.error(f"Failed to persist reply message: {pe}")

        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"[DB-Queue] Task {msg.task_id} error after {elapsed_time:.2f}s: {e}")

            try:
                task_repo.mark_as_failed(msg.task_id, str(e))
            except Exception as db_error:
                logger.error(f"Failed to update task state in DB: {db_error}")

            # Write error reply to DB
            try:
                msg_repo.create(Message(
                    scenario_id=self.scenario_id,
                    task_id=msg.task_id,
                    from_agent="execution",
                    to_agent="scheduling",
                    message_type="reply",
                    content=json.dumps({
                        "task_id": msg.task_id,
                        "success": False,
                        "error": str(e),
                    }, ensure_ascii=False),
                    timestamp=datetime.now(),
                ))
            except Exception as pe:
                logger.error(f"Failed to persist error reply message: {pe}")
        finally:
            agent.cleanup()

    def stop(self):
        self._stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
        if self.executor:
            self.executor.shutdown(wait=False)


class MessageQueueService:
    """
    DB-backed message queue service with per-BOT consumer offsets.

    Messages are stored in the `messages` table.
    Each BOT (consumer) tracks its own read position in `consumer_offsets`.
    """

    def __init__(self):
        self._workers: Dict[str, ExecutionWorker] = {}
        self._lock = threading.Lock()

    def _ensure_worker(self, scenario_id: str, max_workers: int = 3):
        """Lazy-init ExecutionWorker for a scenario"""
        if scenario_id in self._workers:
            return
        with self._lock:
            if scenario_id in self._workers:
                return
            worker = ExecutionWorker(scenario_id, max_workers=max_workers)
            self._workers[scenario_id] = worker
            worker.start()

    def dispatch_subtasks(self, scenario_id: str,
                          subtasks: List[TaskMessage],
                          max_workers: int = 3) -> None:
        """Write dispatch messages to DB, ensure worker is running"""
        self._ensure_worker(scenario_id, max_workers=max_workers)
        msg_repo = MessageRepository()
        for msg in subtasks:
            try:
                msg_repo.create(Message(
                    scenario_id=scenario_id,
                    task_id=msg.task_id,
                    from_agent="scheduling",
                    to_agent="execution",
                    message_type="dispatch",
                    content=json.dumps({
                        "task_id": msg.task_id,
                        "parent_task_id": msg.parent_task_id,
                        "goal": msg.goal,
                        "context": msg.context,
                    }, ensure_ascii=False),
                    timestamp=datetime.now(),
                ))
            except Exception as e:
                logger.error(f"Failed to persist dispatch message: {e}")
        logger.info(f"Dispatched {len(subtasks)} subtask(s) to scenario {scenario_id} "
                    f"with {max_workers} parallel workers")

    def collect_replies(self, scenario_id: str, expected_count: int,
                        timeout: int = 300) -> List[ReplyMessage]:
        """Poll DB for reply messages until expected_count reached or timeout"""
        replies: List[ReplyMessage] = []
        deadline = datetime.now().timestamp() + timeout
        consumer_id = _consumer_id_scheduling(scenario_id)
        msg_repo = MessageRepository()
        offset_repo = ConsumerOffsetRepository()

        while len(replies) < expected_count:
            remaining = deadline - datetime.now().timestamp()
            if remaining <= 0:
                logger.warning(
                    f"collect_replies timeout: got {len(replies)}/{expected_count} "
                    f"for scenario {scenario_id}"
                )
                break

            pending = msg_repo.find_pending_messages(
                consumer_id, scenario_id, "reply", limit=expected_count - len(replies)
            )

            for msg_record in pending:
                try:
                    content = json.loads(msg_record.content) if msg_record.content else {}
                    reply = ReplyMessage(
                        task_id=content.get("task_id", msg_record.task_id),
                        success=content.get("success", False),
                        result=content.get("result", {}),
                    )
                    replies.append(reply)
                    offset_repo.update_offset(consumer_id, msg_record.id)
                except (json.JSONDecodeError, TypeError):
                    logger.error(f"Failed to parse reply message {msg_record.id}")
                    offset_repo.update_offset(consumer_id, msg_record.id)

            if len(replies) < expected_count:
                time.sleep(min(0.5, remaining))

        return replies

    def stop_worker(self, scenario_id: str) -> None:
        """Stop worker for a scenario"""
        with self._lock:
            worker = self._workers.pop(scenario_id, None)

        if worker:
            worker.stop()
            logger.info(f"Stopped ExecutionWorker for scenario {scenario_id}")

    def has_scenario(self, scenario_id: str) -> bool:
        return scenario_id in self._workers


# Global singleton
mqs = MessageQueueService()
