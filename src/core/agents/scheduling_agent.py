# Scheduling Agent - ReAct pattern for goal decomposition
import uuid
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from .base_agent import BaseAgent
from core.state_machine import TaskState, TASK_STATE_MACHINE
from core.event_bus import event_bus
from core.message_queue import mqs, TaskMessage
from core.llm_client import llm_client
from database.repositories.task_repository import TaskRepository
from database.models.task import Task
from logger import logger


class SchedulingAgent(BaseAgent):
    """
    Scheduling Agent: ReAct pattern, idempotent control, task state machine

    Responsibilities:
    - Goal decomposition into tasks
    - Task scheduling and dependency management
    - Idempotent task execution (retry-safe)
    - State machine transitions
    """

    def __init__(self):
        self.task_repo = TaskRepository()
        self.config = {}

    def get_agent_type(self) -> str:
        return "scheduling"

    def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize scheduling agent with configuration"""
        self.config = config
        logger.info("SchedulingAgent initialized")

    def run(self, task_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        ReAct loop:
        1. REASON: Analyze goal, plan tasks
        2. ACT: Create tasks, schedule execution
        3. OBSERVE: Monitor task progress
        4. REPEAT until goal achieved

        Args:
            task_id: Parent task ID (the goal)
            context: Goal context

        Returns:
            Scheduling result
        """
        goal = context.get("goal", "")
        if not goal:
            return {"success": False, "error": "No goal provided"}

        logger.info(f"SchedulingAgent processing goal: {goal}")

        try:
            # REASON: Decompose goal into subtopics (子主题)
            topic_id = str(uuid.uuid4())
            logger.info(f"Starting goal decomposition for task {task_id}")
            subtasks = self._decompose_goal(goal, context)
            logger.info(f"Goal decomposition completed: {len(subtasks)} subtasks created")

            # ACT: Create subtasks with idempotency + topic tracking
            # Tag subtasks with scenario_id so _release_scenario_agents can
            # find/cancel them by scenario — without this they orphan on timeout.
            scenario_id = context.get("scenario_id")
            created_tasks = []
            for subtask in subtasks:
                subtask_id = self._create_subtask(task_id, subtask,
                                                  topic_id=topic_id,
                                                  scenario_id=scenario_id)
                if subtask_id:
                    created_tasks.append((subtask_id, subtask))

            subtask_ids = [t[0] for t in created_tasks]

            # DISPATCH: Send subtasks to execution via message queue
            if scenario_id and created_tasks:
                task_messages = [
                    TaskMessage(
                        task_id=sid,
                        parent_task_id=task_id,
                        goal=sub.get("goal", ""),
                        context=sub.get("context", {}),
                    )
                    for sid, sub in created_tasks
                ]
                # Support parallel execution with configurable workers (default: 3)
                max_workers = context.get("max_workers", 3)
                mqs.dispatch_subtasks(scenario_id, task_messages, max_workers=max_workers)

                # COLLECT: Wait for execution replies
                timeout = context.get("timeout_seconds", 300)
                replies = mqs.collect_replies(scenario_id, len(task_messages), timeout)

                event_bus.emit("task.scheduled", {
                    "task_id": task_id,
                    "topic_id": topic_id,
                    "subtask_count": len(subtask_ids),
                    "subtask_ids": subtask_ids,
                })

                return {
                    "success": True,
                    "topic_id": topic_id,
                    "subtask_ids": subtask_ids,
                    "subtask_count": len(subtask_ids),
                    "replies": {r.task_id: r.result for r in replies},
                }

            # OBSERVE: Return scheduling result (no scenario_id — skip dispatch)
            event_bus.emit("task.scheduled", {
                "task_id": task_id,
                "topic_id": topic_id,
                "subtask_count": len(subtask_ids),
                "subtask_ids": subtask_ids,
            })

            return {
                "success": True,
                "topic_id": topic_id,
                "subtask_ids": subtask_ids,
                "subtask_count": len(subtask_ids),
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(f"SchedulingAgent error: {error_msg}")

            event_bus.emit("task.scheduling_failed", {
                "task_id": task_id,
                "error": error_msg,
            })

            return {
                "success": False,
                "error": error_msg,
            }

    def _decompose_goal(self, goal: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Decompose goal into subtasks (子主题).

        Strategy:
        1. Try LLM-based intelligent decomposition first
        2. Fall back to rule-based heuristic if LLM fails or not configured

        Returns:
            List of subtask dicts
        """
        priority = context.get("priority", 0)
        timeout = context.get("timeout_seconds", 3600)
        exec_context = context.get("execution_context", {})

        logger.info(f"Attempting goal decomposition for: {goal[:100]}...")

        # 优先使用大模型进行智能分解
        if llm_client.client:
            try:
                logger.info("Calling LLM for goal decomposition...")
                llm_subtasks = llm_client.decompose_goal(goal, context)
                if llm_subtasks:
                    logger.info(f"LLM successfully decomposed goal into {len(llm_subtasks)} subtasks")
                    return llm_subtasks
                else:
                    logger.warning("LLM returned None or empty subtasks, falling back to heuristic")
            except Exception as e:
                logger.warning(f"LLM decomposition failed, falling back to heuristic: {str(e)}")
        else:
            logger.info("LLM client not configured, using heuristic decomposition")

        # 回退到基于规则的启发式分解
        logger.info("Using rule-based heuristic for goal decomposition")

        # Heuristic: if goal contains "and", split into multiple subtasks
        lower_goal = goal.lower()
        if " and " in lower_goal:
            parts = [p.strip() for p in goal.split(" and ") if p.strip()]
            if len(parts) > 1:
                return [
                    {
                        "goal": part,
                        "type": "execution",
                        "priority": priority,
                        "timeout_seconds": timeout,
                        "context": exec_context,
                    }
                    for part in parts
                ]

        # Default: create analysis + execution subtasks
        return [
            {
                "goal": f"Analyze: {goal}",
                "type": "execution",
                "priority": priority,
                "timeout_seconds": min(60, timeout),
                "context": {"command": f"echo 'Analyzing: {goal}'"},
            },
            {
                "goal": f"Execute: {goal}",
                "type": "execution",
                "priority": priority,
                "timeout_seconds": timeout,
                "context": exec_context,
            },
        ]

    def _create_subtask(self, parent_task_id: str, subtask: Dict[str, Any],
                        topic_id: str = None,
                        scenario_id: str = None) -> Optional[str]:
        """
        Create a subtask with idempotency control (幂等控制).

        If a task with the same idempotency_key already exists, returns
        the existing task_id instead of creating a duplicate.

        Returns:
            task_id if created/found, None if error
        """
        goal = subtask.get("goal", "")
        idempotency_key = f"{parent_task_id}:{goal}"

        # Idempotency check: return existing if already created
        existing = self.task_repo.find_by_idempotency_key(idempotency_key)
        if existing:
            logger.info(f"Subtask already exists (idempotent): {idempotency_key}")
            return existing.task_id

        task_id = str(uuid.uuid4())

        task = Task(
            task_id=task_id,
            parent_task_id=parent_task_id,
            topic_id=topic_id,
            scenario_id=scenario_id,
            idempotency_key=idempotency_key,
            goal=goal,
            state=TaskState.PENDING.value,
            priority=subtask.get("priority", 0),
            timeout_seconds=subtask.get("timeout_seconds", 3600),
            max_retries=3,
            retry_count=0,
            context=json.dumps(subtask.get("context", {}), ensure_ascii=False),
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        self.task_repo.create(task)

        event_bus.emit("task.created", {
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "topic_id": topic_id,
            "goal": goal,
        })

        logger.info(f"Created subtask {task_id} for parent {parent_task_id} (topic: {topic_id})")
        return task_id

    def create_task(self, goal: str, parent_task_id: Optional[str] = None,
                   context: Dict[str, Any] = None) -> str:
        """
        Create a new task.

        Args:
            goal: Task goal
            parent_task_id: Parent task ID (for hierarchies)
            context: Task context

        Returns:
            Task ID
        """
        task_id = str(uuid.uuid4())

        task = Task(
            task_id=task_id,
            parent_task_id=parent_task_id,
            goal=goal,
            state=TaskState.PENDING.value,
            priority=context.get("priority", 0) if context else 0,
            timeout_seconds=context.get("timeout_seconds", 3600) if context else 3600,
            max_retries=3,
            retry_count=0,
            context=json.dumps(context, ensure_ascii=False) if context else "{}",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        self.task_repo.create(task)

        event_bus.emit("task.created", {
            "task_id": task_id,
            "goal": goal,
        })

        return task_id

    def transition_task_state(self, task_id: str, new_state: TaskState) -> bool:
        """
        Transition task state machine.

        Args:
            task_id: Task ID
            new_state: New state

        Returns:
            True if transition succeeded
        """
        task = self.task_repo.find_by_task_id(task_id)
        if not task:
            logger.error(f"Task not found: {task_id}")
            return False

        # Validate transition
        sm = TASK_STATE_MACHINE
        sm.initialize(TaskState(task.state))

        if not sm.can_transition(new_state):
            logger.error(f"Invalid state transition: {task.state} -> {new_state.value}")
            return False

        # Perform transition
        old_state = task.state
        self.task_repo.update_task_state(task_id, new_state.value)

        event_bus.emit("task.state_changed", {
            "task_id": task_id,
            "old_state": old_state,
            "new_state": new_state.value,
        })

        logger.info(f"Task {task_id} state: {old_state} -> {new_state.value}")
        return True

    def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("SchedulingAgent cleaned up")
