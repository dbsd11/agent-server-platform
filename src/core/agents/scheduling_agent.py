# Scheduling Agent - ReAct pattern for goal decomposition
import uuid
import json
import time
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
            # Ensure every subtask has an id + depends_on (heuristic fallback
            # or older LLM output may omit them -> all parallel, wave 0).
            subtasks = self._normalize_subtasks(subtasks)
            logger.info(f"Goal decomposition completed: {len(subtasks)} subtasks created")

            # ACT: Create subtasks with idempotency + topic tracking
            # Tag subtasks with scenario_id so _release_scenario_agents can
            # find/cancel them by scenario — without this they orphan on timeout.
            scenario_id = context.get("scenario_id")

            # Build dependency waves (topological). Tasks within a wave run in
            # parallel; waves run serially so dependents see predecessors' output.
            try:
                waves = self._build_waves(subtasks)
            except ValueError as cyc:
                logger.error(f"SchedulingAgent: {cyc}")
                return {"success": False, "error": str(cyc)}

            # Create all subtask rows up front (visible in task monitor).
            # Map local id -> real task_id for dependency resolution.
            local_id_to_task_id: Dict[str, str] = {}
            created_by_id: Dict[str, tuple] = {}  # local_id -> (task_id, subtask)
            for sub in subtasks:
                lid = sub["id"]
                deps = [d for d in (sub.get("depends_on") or [])]
                subtask_id = self._create_subtask(
                    task_id, sub, topic_id=topic_id, scenario_id=scenario_id,
                    depends_on=deps, local_id_to_task_id=local_id_to_task_id,
                )
                if subtask_id:
                    local_id_to_task_id[lid] = subtask_id
                    created_by_id[lid] = (subtask_id, sub)

            subtask_ids = list(local_id_to_task_id.values())

            # DISPATCH + COLLECT per wave.
            if scenario_id and created_by_id:
                configured_roles = (context.get("agent_roles") or {}).get(
                    "execution_agents", []) or []
                max_workers = context.get("max_workers", 3)
                # Share the scenario timeout across all waves.
                total_timeout = context.get("timeout_seconds", 300)
                deadline = time.time() + total_timeout
                all_replies: Dict[str, dict] = {}
                failed_ids: set = set()

                for wave_idx, wave in enumerate(waves):
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(f"SchedulingAgent: timeout before wave {wave_idx}")
                        break

                    # Skip tasks whose predecessors failed.
                    wave_msgs = []
                    wave_lids = []
                    for sub in wave:
                        tid = local_id_to_task_id.get(sub["id"])
                        if not tid:
                            continue
                        # If any predecessor failed/skipped, this task is skipped.
                        deps = [d for d in (sub.get("depends_on") or [])]
                        if any(local_id_to_task_id.get(d) in failed_ids for d in deps):
                            self.task_repo.mark_as_failed(
                                tid, "Skipped: predecessor task failed")
                            failed_ids.add(tid)
                            event_bus.emit("task.skipped", {
                                "task_id": tid, "reason": "predecessor failed"})
                            continue

                        ctx = dict(sub.get("context", {}) or {})
                        role, sys_prompt, server_id = self._associate_role(
                            sub, configured_roles)
                        ctx["role"] = role
                        ctx["system_prompt"] = sys_prompt
                        if server_id:
                            ctx["server_id"] = server_id
                        # Inject predecessor results so this task can build on them.
                        self._inject_upstream(ctx, deps, local_id_to_task_id, all_replies)
                        wave_msgs.append(TaskMessage(
                            task_id=tid, parent_task_id=task_id,
                            goal=sub.get("goal", ""), context=ctx,
                        ))
                        wave_lids.append(sub["id"])

                    if not wave_msgs:
                        continue

                    logger.info(f"SchedulingAgent: wave {wave_idx} dispatching "
                                f"{len(wave_msgs)} task(s) {[sub['id'] for sub in wave]}")
                    mqs.dispatch_subtasks(scenario_id, wave_msgs, max_workers=max_workers)

                    wave_timeout = max(int(remaining), 1)
                    replies = mqs.collect_replies(
                        scenario_id, len(wave_msgs), timeout=wave_timeout)
                    for r in replies:
                        all_replies[r.task_id] = r.result
                        if not r.success:
                            failed_ids.add(r.task_id)

                    # Propagate failure to all remaining dependents.
                    if failed_ids:
                        self._propagate_failure(
                            failed_ids, waves, local_id_to_task_id, wave_idx + 1)

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
                    "replies": all_replies,
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

    def _associate_role(self, subtask: Dict[str, Any],
                        configured_roles: List[Dict[str, Any]]):
        """Associate a subtask with a configured execution-agent role.

        Returns (role_name, system_prompt, server_id):
        - If the subtask's role exactly matches a configured role name, use
          that role's configured definition (system prompt) + server_id.
        - If no match but roles are configured, fall back to the FIRST
          configured role (with a warning) so the task still routes and
          executes — an unassociated task cannot execute correctly.
        - If no roles are configured, pass through the LLM's role/system_prompt
          with server_id=None (local execution, the default behavior).
        """
        ctx = subtask.get("context", {}) or {}
        role = ctx.get("role", "")

        if configured_roles:
            for ag in configured_roles:
                if ag.get("name") == role:
                    return (ag.get("name", role),
                            ag.get("role", ""),
                            ag.get("server_id"))
            # No exact match — the LLM invented a name. Fall back to the first
            # configured role so the task is still associated + routable.
            first = configured_roles[0]
            logger.warning(
                f"Subtask role '{role}' not in configured roles "
                f"{[r.get('name') for r in configured_roles]}; "
                f"assigning '{first.get('name')}'"
            )
            return (first.get("name", role),
                    first.get("role", ""),
                    first.get("server_id"))

        # No configured roles -> pass through LLM values, execute locally.
        return role, ctx.get("system_prompt", ""), None

    # --- dependency / wave scheduling ---------------------------------------

    def _normalize_subtasks(self, subtasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure each subtask has a unique id + a depends_on list.

        Heuristic decomposition or older LLM output may omit these; without an
        id they can't participate in the dependency graph. Missing -> all land
        in wave 0 (parallel), preserving the legacy behavior.
        """
        seen = set()
        normalized = []
        for idx, sub in enumerate(subtasks):
            s = dict(sub)
            tid = s.get("id") or f"t{idx + 1}"
            if tid in seen:
                tid = f"t{idx + 1}"
            seen.add(tid)
            s["id"] = tid
            deps = s.get("depends_on") or []
            if not isinstance(deps, list):
                deps = []
            s["depends_on"] = deps
            normalized.append(s)
        return normalized

    def _build_waves(self, subtasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Group subtasks into topological execution waves.

        Wave 0 = subtasks with no (valid) dependencies. Wave N = subtasks all
        of whose dependencies are in earlier waves. Tasks within a wave run in
        parallel; waves run serially so a dependent task can consume its
        predecessors' results.

        Drops unknown/self dependency refs. Raises ValueError on a cycle.
        """
        by_id = {s["id"]: s for s in subtasks}
        # Sanitize depends_on to known, non-self refs.
        deps = {
            s["id"]: [d for d in (s.get("depends_on") or []) if d in by_id and d != s["id"]]
            for s in subtasks
        }
        waves: List[List[Dict[str, Any]]] = []
        placed: set = set()
        remaining = set(by_id.keys())

        while remaining:
            ready = [sid for sid in remaining if all(d in placed for d in deps[sid])]
            if not ready:
                raise ValueError(
                    f"Dependency cycle detected among: {sorted(remaining)}"
                )
            waves.append([by_id[sid] for sid in ready])
            placed.update(ready)
            remaining -= set(ready)
        return waves

    def _inject_upstream(self, ctx: Dict[str, Any], depends_on_ids: List[str],
                         local_id_to_task_id: Dict[str, str],
                         replies_by_task_id: Dict[str, dict]) -> None:
        """Inject predecessor results into a dependent task's context.

        Adds ctx["upstream_results"] = {local_id: reply_result} and
        ctx["upstream_outputs"] = [result["output"]...] in dependency order.
        No-op when depends_on_ids is empty.
        """
        if not depends_on_ids:
            return
        upstream = {}
        outputs = []
        for lid in depends_on_ids:
            tid = local_id_to_task_id.get(lid)
            if not tid or tid not in replies_by_task_id:
                continue
            res = replies_by_task_id[tid]
            upstream[lid] = res
            outputs.append(res.get("output", ""))
        ctx["upstream_results"] = upstream
        ctx["upstream_outputs"] = outputs

    def _propagate_failure(self, failed_ids: set, waves: List[List[Dict[str, Any]]],
                           local_id_to_task_id: Dict[str, str],
                           start_wave: int) -> set:
        """Mark all transitive dependents of failed_ids in later waves as
        skipped (failed). Returns the set of skipped task_ids."""
        skipped = set()
        # Collect all remaining tasks from start_wave onward
        for wave in waves[start_wave:]:
            for sub in wave:
                deps = [d for d in (sub.get("depends_on") or [])]
                # Skip if any predecessor failed or was already skipped
                failed_or_skipped = failed_ids | skipped
                if any(local_id_to_task_id.get(d) in failed_or_skipped for d in deps):
                    tid = local_id_to_task_id.get(sub["id"])
                    if tid:
                        try:
                            self.task_repo.mark_as_failed(
                                tid, "Skipped: predecessor task failed")
                        except Exception as e:
                            logger.error(f"Failed to mark skipped task {tid}: {e}")
                        event_bus.emit("task.skipped", {
                            "task_id": tid, "reason": "predecessor failed",
                        })
                        skipped.add(tid)
        return skipped

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

        # Pass configured execution-agent role names to the LLM so it picks
        # from them (rather than inventing names that won't route).
        configured_roles = (context.get("agent_roles") or {}).get(
            "execution_agents", []) or []
        if configured_roles:
            context = dict(context)
            context["execution_role_names"] = [r.get("name", "") for r in configured_roles]

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
                        scenario_id: str = None,
                        depends_on: List[str] = None,
                        local_id_to_task_id: Dict[str, str] = None) -> Optional[str]:
        """
        Create a subtask with idempotency control (幂等控制).

        If a task with the same idempotency_key already exists, returns
        the existing task_id instead of creating a duplicate.

        Args:
            depends_on: local predecessor ids (e.g. ["t1"])
            local_id_to_task_id: map to resolve local ids -> real task_ids
                for persistence. The new task's own id is registered here.

        Returns:
            task_id if created/found, None if error
        """
        goal = subtask.get("goal", "")
        idempotency_key = f"{parent_task_id}:{goal}"

        # Idempotency check: return existing if already created
        existing = self.task_repo.find_by_idempotency_key(idempotency_key)
        if existing:
            logger.info(f"Subtask already exists (idempotent): {idempotency_key}")
            if local_id_to_task_id is not None and subtask.get("id"):
                local_id_to_task_id[subtask["id"]] = existing.task_id
            return existing.task_id

        task_id = str(uuid.uuid4())
        # Resolve local predecessor ids to real task_ids for persistence.
        resolved_deps = []
        if depends_on and local_id_to_task_id:
            resolved_deps = [local_id_to_task_id[d] for d in depends_on
                             if d in local_id_to_task_id]

        task = Task(
            task_id=task_id,
            parent_task_id=parent_task_id,
            topic_id=topic_id,
            scenario_id=scenario_id,
            idempotency_key=idempotency_key,
            depends_on=json.dumps(resolved_deps, ensure_ascii=False) if resolved_deps else None,
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

        if local_id_to_task_id is not None and subtask.get("id"):
            local_id_to_task_id[subtask["id"]] = task_id

        event_bus.emit("task.created", {
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "topic_id": topic_id,
            "goal": goal,
            "depends_on": resolved_deps,
        })

        logger.info(f"Created subtask {task_id} for parent {parent_task_id} "
                    f"(topic: {topic_id}, depends_on: {resolved_deps})")
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
