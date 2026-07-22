"""
Unit / integration tests for agent system:
- core/agents/base_agent.py (AgentRun, SystemMessageOutput, BaseAgent)
- core/agents/scheduling_agent.py (SchedulingAgent)
- core/agents/execution_agent.py (ExecutionAgent)
"""
import pytest
import uuid
import json
import time
from datetime import datetime

from core.agents.base_agent import AgentRun, SystemMessageOutput, BaseAgent
from core.agents.scheduling_agent import SchedulingAgent
from core.agents.execution_agent import ExecutionAgent
from core.state_machine import TaskState
from database.repositories.task_repository import TaskRepository
from database.models.task import Task


class TestSystemMessageOutput:
    def test_emit_and_get_message(self):
        smo = SystemMessageOutput()
        smo.emit_message("hello")
        msg = smo.get_message(timeout=1)
        assert msg is not None
        assert msg["message"] == "hello"
        assert "timestamp" in msg

    def test_set_and_get_result(self):
        smo = SystemMessageOutput()
        smo.set_result({"output": 42})
        result = smo.get_result(timeout=1)
        assert result == {"output": 42}

    def test_get_message_timeout_returns_none(self):
        smo = SystemMessageOutput()
        result = smo.get_message(timeout=0.1)
        assert result is None

    def test_get_result_timeout_returns_none(self):
        smo = SystemMessageOutput()
        result = smo.get_result(timeout=0.1)
        assert result is None

    def test_reset_clears_queues(self):
        smo = SystemMessageOutput()
        smo.emit_message("msg1")
        smo.set_result({"x": 1})
        smo.reset()
        assert smo.get_message(timeout=0.1) is None
        assert smo.get_result(timeout=0.1) is None

    def test_multiple_messages_fifo(self):
        smo = SystemMessageOutput()
        for i in range(3):
            smo.emit_message(f"msg-{i}")
        for i in range(3):
            msg = smo.get_message(timeout=1)
            assert msg["message"] == f"msg-{i}"


class TestAgentRun:
    def _make_agent(self):
        agent = SchedulingAgent()
        agent.initialize({})
        return agent

    def test_initial_state(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {})
        assert agent_run.processing is False
        assert agent_run.result is None
        assert agent_run.error is None
        assert agent_run.started_at is None
        assert agent_run.completed_at is None

    def test_start(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {})
        agent_run.start()
        assert agent_run.processing is True
        assert agent_run.started_at is not None

    def test_complete(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {})
        agent_run.start()
        agent_run.complete({"output": "done"})
        assert agent_run.processing is False
        assert agent_run.result == {"output": "done"}
        assert agent_run.completed_at is not None

    def test_fail(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {})
        agent_run.start()
        agent_run.fail("something broke")
        assert agent_run.processing is False
        assert agent_run.error == "something broke"
        assert agent_run.completed_at is not None

    def test_reset(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {})
        agent_run.start()
        agent_run.complete({"x": 1})
        agent_run.reset()
        assert agent_run.processing is False
        assert agent_run.result is None
        assert agent_run.error is None
        assert agent_run.started_at is None
        assert agent_run.completed_at is None

    def test_to_dict(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {"key": "val"})
        agent_run.start()
        agent_run.complete({"out": "ok"})

        d = agent_run.to_dict()
        assert d["task_id"] == "task-1"
        assert d["processing"] is False
        assert d["result"] == {"out": "ok"}
        assert d["error"] is None
        assert d["started_at"] is not None
        assert d["completed_at"] is not None

    def test_to_dict_before_start(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {})
        d = agent_run.to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None

    def test_complete_sets_system_message_output(self):
        agent_run = AgentRun(self._make_agent(), "task-1", {})
        agent_run.start()
        agent_run.complete({"answer": 42})

        result = agent_run.system_message_output.get_result(timeout=1)
        assert result == {"answer": 42}


class TestSchedulingAgent:
    def test_agent_type(self):
        agent = SchedulingAgent()
        assert agent.get_agent_type() == "scheduling"

    def test_initialize(self):
        agent = SchedulingAgent()
        agent.initialize({"key": "value"})
        assert agent.config == {"key": "value"}

    def test_decompose_goal_with_and(self):
        agent = SchedulingAgent()
        agent.initialize({})
        subtasks = agent._decompose_goal("Analyze data and generate report", {})
        assert len(subtasks) == 2
        goals = [s["goal"] for s in subtasks]
        assert "Analyze data" in goals
        assert "generate report" in goals

    def test_decompose_goal_without_and(self):
        agent = SchedulingAgent()
        agent.initialize({})
        subtasks = agent._decompose_goal("Analyze data", {})
        assert len(subtasks) == 2
        goals = [s["goal"] for s in subtasks]
        assert any("Analyze" in g for g in goals)
        assert any("Execute" in g for g in goals)

    def test_create_task(self, task_repo):
        agent = SchedulingAgent()
        agent.initialize({})
        task_id = agent.create_task("Test goal")
        assert task_id is not None

        task = task_repo.find_by_task_id(task_id)
        assert task is not None
        assert task.goal == "Test goal"
        assert task.state == TaskState.PENDING.value

    def test_create_task_with_parent(self, task_repo):
        agent = SchedulingAgent()
        agent.initialize({})
        parent_id = agent.create_task("Parent")
        child_id = agent.create_task("Child", parent_task_id=parent_id)

        child = task_repo.find_by_task_id(child_id)
        assert child.parent_task_id == parent_id

    def test_create_task_with_context(self, task_repo):
        agent = SchedulingAgent()
        agent.initialize({})
        task_id = agent.create_task("Test", context={
            "priority": 5, "timeout_seconds": 120
        })
        task = task_repo.find_by_task_id(task_id)
        assert task.priority == 5
        assert task.timeout_seconds == 120

    def test_create_subtask_idempotent(self, task_repo):
        """Creating the same subtask twice returns the same task_id."""
        agent = SchedulingAgent()
        agent.initialize({})

        parent_id = str(uuid.uuid4())
        subtask = {"goal": "Analyze: test", "type": "execution"}

        id1 = agent._create_subtask(parent_id, subtask, topic_id="topic-1")
        id2 = agent._create_subtask(parent_id, subtask, topic_id="topic-1")

        assert id1 == id2

    def test_transition_task_state_valid(self, task_repo):
        agent = SchedulingAgent()
        agent.initialize({})
        task_id = agent.create_task("Test transition")

        result = agent.transition_task_state(task_id, TaskState.RUNNING)
        assert result is True

        task = task_repo.find_by_task_id(task_id)
        assert task.state == TaskState.RUNNING.value

    def test_transition_task_state_invalid(self, task_repo):
        agent = SchedulingAgent()
        agent.initialize({})
        task_id = agent.create_task("Test invalid transition")

        # PENDING -> SUCCESS is invalid
        result = agent.transition_task_state(task_id, TaskState.SUCCESS)
        assert result is False

    def test_transition_task_state_nonexistent(self):
        agent = SchedulingAgent()
        agent.initialize({})
        result = agent.transition_task_state("nonexistent-task", TaskState.RUNNING)
        assert result is False

    def test_run_without_goal(self):
        agent = SchedulingAgent()
        agent.initialize({})
        result = agent.run("task-1", {})
        assert result["success"] is False
        assert "error" in result

    def test_run_creates_subtasks(self, task_repo):
        agent = SchedulingAgent()
        agent.initialize({})

        # First create a parent task
        parent_task_id = agent.create_task("Parent goal")

        # Now run scheduling on the parent
        result = agent.run(parent_task_id, {
            "goal": "Analyze data and generate report"
        })

        assert result["success"] is True
        assert result["subtask_count"] == 2
        assert result["topic_id"] is not None
        assert len(result["subtask_ids"]) == 2

    def test_run_emits_events(self, event_bus, task_repo):
        received = []
        handler = lambda e: received.append(e)
        event_bus.subscribe("task.scheduled", handler)

        agent = SchedulingAgent()
        agent.initialize({})
        parent_id = agent.create_task("Event test")
        agent.run(parent_id, {"goal": "Simple goal"})

        time.sleep(0.5)
        event_bus.unsubscribe("task.scheduled", handler)

        scheduled = [e for e in received if e["event_type"] == "task.scheduled"]
        assert len(scheduled) >= 1

    def test_cleanup(self):
        agent = SchedulingAgent()
        agent.initialize({})
        agent.cleanup()  # should not raise


class TestExecutionAgent:
    def test_agent_type(self):
        agent = ExecutionAgent()
        assert agent.get_agent_type() == "execution"

    def test_initialize(self):
        agent = ExecutionAgent()
        agent.initialize({})
        assert agent.sandbox.temp_dir is not None
        agent.cleanup()

    def test_run_script(self):
        agent = ExecutionAgent()
        agent.initialize({})
        try:
            result = agent.run("task-1", {
                "script": "print('hello from agent')"
            })
            assert result["success"] is True
            assert "hello from agent" in result["output"]
            assert result["returncode"] == 0
        finally:
            agent.cleanup()

    def test_run_command(self):
        agent = ExecutionAgent()
        agent.initialize({})
        try:
            result = agent.run("task-2", {
                "command": "echo 'shell test'"
            })
            assert result["success"] is True
            assert "shell test" in result["output"]
        finally:
            agent.cleanup()

    def test_run_no_script_or_command(self):
        agent = ExecutionAgent()
        agent.initialize({})
        try:
            result = agent.run("task-3", {})
            assert result["success"] is False
            assert "error" in result
        finally:
            agent.cleanup()

    def test_run_failing_script(self):
        agent = ExecutionAgent()
        agent.initialize({})
        try:
            result = agent.run("task-4", {
                "script": "import sys; print('err', file=sys.stderr); sys.exit(1)"
            })
            assert result["success"] is False
            assert result["returncode"] != 0
        finally:
            agent.cleanup()

    def test_run_emits_events(self, event_bus):
        received = []
        handler = lambda e: received.append(e)

        event_bus.subscribe("task.execution_started", handler)
        event_bus.subscribe("task.execution_completed", handler)

        agent = ExecutionAgent()
        agent.initialize({})
        agent.run("task-5", {"script": "print('ok')"})

        time.sleep(0.5)

        event_bus.unsubscribe("task.execution_started", handler)
        event_bus.unsubscribe("task.execution_completed", handler)

        started = [e for e in received if e["event_type"] == "task.execution_started"]
        assert len(started) >= 1

    def test_plan_execution_script(self):
        agent = ExecutionAgent()
        agent.initialize({})
        plan = agent._plan_execution({"script": "print(1)"})
        assert plan["type"] == "script"

    def test_plan_execution_command(self):
        agent = ExecutionAgent()
        agent.initialize({})
        plan = agent._plan_execution({"command": "echo 1"})
        assert plan["type"] == "command"

    def test_plan_execution_no_input_raises(self):
        agent = ExecutionAgent()
        agent.initialize({})
        with pytest.raises(ValueError, match="No script or command"):
            agent._plan_execution({})
        agent.cleanup()
