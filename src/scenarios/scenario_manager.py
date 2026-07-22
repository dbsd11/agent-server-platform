# Scenario Manager - scenario lifecycle management
import uuid
import json
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime

from database.repositories.scenario_repository import ScenarioRepository
from database.models.scenario import Scenario
from core.state_machine import ScenarioState, SCENARIO_STATE_MACHINE
from core.event_bus import event_bus
from scenarios.base_scenario import BaseScenario, ScenarioContext
from logger import logger


class ScenarioManager:
    """
    Scenario Manager: Lifecycle management and orchestration.

    Responsibilities:
    - Scenario registration and management
    - Scenario lifecycle (start/stop/cleanup)
    - State machine transitions
    - Agent orchestration
    """

    def __init__(self):
        self.scenario_repo = ScenarioRepository()
        self.active_scenarios: Dict[str, BaseScenario] = {}
        self.scenario_contexts: Dict[str, ScenarioContext] = {}
        self.lock = threading.Lock()

    def create_scenario(self, scenario_type: str, name: str, description: str = "",
                       config: Dict[str, Any] = None, created_by: int = None) -> str:
        """
        Create a new scenario.

        Args:
            scenario_type: Scenario type (simple_qa, code_execution)
            name: Scenario name
            description: Scenario description
            config: Scenario configuration
            created_by: User ID who created the scenario

        Returns:
            Scenario ID
        """
        scenario_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())  # Trace for this scenario's lifecycle

        scenario = Scenario(
            scenario_id=scenario_id,
            scenario_type=scenario_type,
            name=name,
            description=description,
            state=ScenarioState.INITIALIZING.value,
            config=json.dumps(config or {}),
            context=json.dumps({"trace_id": trace_id}),
            created_by=created_by,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        self.scenario_repo.create(scenario)

        event_bus.emit("scenario.created", {
            "scenario_id": scenario_id,
            "scenario_type": scenario_type,
            "name": name,
        }, trace_id=trace_id)

        logger.info(f"Created scenario: {scenario_id} ({scenario_type}) trace:{trace_id}")
        return scenario_id

    def start_scenario(self, scenario_id: str, scenario_instance: BaseScenario) -> bool:
        """
        Start scenario execution.

        Args:
            scenario_id: Scenario ID
            scenario_instance: Scenario instance to execute

        Returns:
            True if started successfully
        """
        # Get scenario from database
        scenario = self.scenario_repo.find_by_scenario_id(scenario_id)
        if not scenario:
            logger.error(f"Scenario not found: {scenario_id}")
            return False

        # Validate state transition
        sm = SCENARIO_STATE_MACHINE
        sm.initialize(ScenarioState(scenario.state))

        if not sm.can_transition(ScenarioState.RUNNING):
            logger.error(f"Invalid state transition: {scenario.state} -> running")
            return False

        # Update state
        self.scenario_repo.update_scenario_state(scenario_id, ScenarioState.RUNNING.value)

        # Register scenario
        with self.lock:
            self.active_scenarios[scenario_id] = scenario_instance

        # Extract trace_id from scenario context (propagate through lifecycle)
        scenario_context = json.loads(scenario.context) if scenario.context else {}
        trace_id = scenario_context.get("trace_id")

        # Start execution in background thread
        config = json.loads(scenario.config) if scenario.config else {}
        config["scenario_id"] = scenario_id
        config["trace_id"] = trace_id

        thread = threading.Thread(
            target=self._execute_scenario,
            args=(scenario_id, scenario_instance, config, trace_id),
            daemon=True
        )
        thread.start()

        event_bus.emit("scenario.started", {
            "scenario_id": scenario_id,
        }, trace_id=trace_id)

        logger.info(f"Started scenario: {scenario_id} trace:{trace_id}")
        return True

    def _execute_scenario(self, scenario_id: str, scenario: BaseScenario,
                         config: Dict[str, Any], trace_id: str = None):
        """Execute scenario in background thread with trace propagation"""
        try:
            # Execute scenario
            result = scenario.start(config)

            # Update state to completed
            self.scenario_repo.mark_as_completed(scenario_id)

            event_bus.emit("scenario.completed", {
                "scenario_id": scenario_id,
                "result": result,
            }, trace_id=trace_id)

            logger.info(f"Scenario completed: {scenario_id}")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Scenario execution error: {error_msg}")

            # Update state to failed
            self.scenario_repo.mark_as_failed(scenario_id)

            event_bus.emit("scenario.failed", {
                "scenario_id": scenario_id,
                "error": error_msg,
            }, trace_id=trace_id)

        finally:
            # Release agent runtime resources (释放底层agent运行环境)
            self._release_scenario_agents(scenario_id, trace_id)

            # Cleanup
            with self.lock:
                if scenario_id in self.active_scenarios:
                    del self.active_scenarios[scenario_id]

    def _release_scenario_agents(self, scenario_id: str, trace_id: str = None):
        """
        Release all resources for a scenario (释放底层agent运行环境).

        1. Stop execution worker + clean consumer offsets
        2. Cancel non-terminal tasks
        3. Cleanup agent runs (call agent.cleanup + remove from registry)
        """
        from agents.agent_manager import agent_manager
        from core.message_queue import mqs
        from database.repositories.task_repository import TaskRepository
        from database.repositories.consumer_offset_repository import ConsumerOffsetRepository

        # 1. Stop execution worker
        mqs.stop_worker(scenario_id)

        # 2. Clean consumer offsets for this scenario
        try:
            offset_repo = ConsumerOffsetRepository()
            for prefix in ("execution_worker", "scheduling_agent"):
                consumer_id = f"{prefix}:{scenario_id}"
                offset = offset_repo.find_by_id(consumer_id)
                if offset:
                    offset_repo.delete(consumer_id)
        except Exception as e:
            logger.error(f"Failed to clean consumer offsets for {scenario_id}: {e}")

        # 2b. Clean agent DB registrations for this scenario
        try:
            from database.repositories.agent_repository import AgentRepository
            agent_repo = AgentRepository()
            deleted = agent_repo.delete_by_scenario_id(scenario_id)
            if deleted > 0:
                logger.info(f"Cleaned {deleted} agent DB record(s) for scenario {scenario_id}")
        except Exception as e:
            logger.error(f"Failed to clean agent DB records for {scenario_id}: {e}")

        # 3. Process all tasks for this scenario
        task_repo = TaskRepository()
        tasks = task_repo.find_by_scenario_id(scenario_id)
        terminal_states = {"success", "failed", "timeout", "cancelled"}
        released_runs = 0
        cancelled_tasks = 0

        for task in tasks:
            # Cancel non-terminal tasks
            if task.state not in terminal_states:
                task_repo.mark_as_failed(task.task_id, "Cancelled: scenario ended")
                cancelled_tasks += 1

            # Cleanup agent run (call agent.cleanup + remove from registry)
            if task.agent_run_id:
                with agent_manager.lock:
                    agent_run = agent_manager.agent_runs.pop(task.agent_run_id, None)
                if agent_run:
                    try:
                        agent_run.agent.cleanup()
                    except Exception as e:
                        logger.error(f"Agent cleanup error for run {task.agent_run_id}: {e}")
                released_runs += 1

        if released_runs > 0 or cancelled_tasks > 0:
            event_bus.emit("scenario.agents_released", {
                "scenario_id": scenario_id,
                "released_runs": released_runs,
                "cancelled_tasks": cancelled_tasks,
            }, trace_id=trace_id)
            logger.info(f"Scenario {scenario_id}: released {released_runs} agent run(s), "
                       f"cancelled {cancelled_tasks} task(s)")

    def stop_scenario(self, scenario_id: str) -> bool:
        """
        Stop scenario execution.

        Args:
            scenario_id: Scenario ID

        Returns:
            True if stopped successfully
        """
        scenario = self.scenario_repo.find_by_scenario_id(scenario_id)
        if not scenario:
            logger.error(f"Scenario not found: {scenario_id}")
            return False

        # Extract trace_id for event propagation
        scenario_context = json.loads(scenario.context) if scenario.context else {}
        trace_id = scenario_context.get("trace_id")

        # Update state to cancelled
        self.scenario_repo.update_scenario_state(scenario_id, ScenarioState.CANCELLED.value)

        # Remove from active scenarios
        with self.lock:
            if scenario_id in self.active_scenarios:
                # Cleanup scenario
                try:
                    self.active_scenarios[scenario_id].cleanup()
                except:
                    pass
                del self.active_scenarios[scenario_id]

        # Release all agent resources (worker, tasks, agent runs, consumer offsets)
        self._release_scenario_agents(scenario_id, trace_id)

        event_bus.emit("scenario.stopped", {
            "scenario_id": scenario_id,
        }, trace_id=trace_id)

        logger.info(f"Stopped scenario: {scenario_id}")
        return True

    def get_scenario_status(self, scenario_id: str) -> Optional[Dict[str, Any]]:
        """Get scenario status"""
        scenario = self.scenario_repo.find_by_scenario_id(scenario_id)
        if not scenario:
            return None

        return {
            "scenario_id": scenario.scenario_id,
            "scenario_type": scenario.scenario_type,
            "name": scenario.name,
            "state": scenario.state,
            "config": json.loads(scenario.config) if scenario.config else {},
            "created_at": scenario.created_at.isoformat() if scenario.created_at else None,
            "started_at": scenario.started_at.isoformat() if scenario.started_at else None,
            "completed_at": scenario.completed_at.isoformat() if scenario.completed_at else None,
        }

    def list_scenarios(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List all scenarios"""
        scenarios = self.scenario_repo.find_all(limit=limit)
        return [
            {
                "scenario_id": s.scenario_id,
                "scenario_type": s.scenario_type,
                "name": s.name,
                "state": s.state,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in scenarios
        ]

    def shutdown(self):
        """Shutdown scenario manager"""
        # Stop all active scenarios
        with self.lock:
            for scenario_id in list(self.active_scenarios.keys()):
                self.stop_scenario(scenario_id)

        logger.info("ScenarioManager shutdown")


# Global scenario manager instance
scenario_manager = ScenarioManager()
