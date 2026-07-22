"""
Unit / integration tests for core/message_queue.py

Tests:
- TaskMessage and ReplyMessage dataclasses
- MessageQueueService dispatch/collect roundtrip
- Idempotent stop
- Timeout with partial results
- Scenario isolation
- has_scenario
"""
import pytest
import time
import uuid
import json
from datetime import datetime

from core.message_queue import (
    mqs, TaskMessage, ReplyMessage, MessageQueueService, ExecutionWorker,
    _STOP_SENTINEL,
)
from core.state_machine import TaskState
from database.repositories.task_repository import TaskRepository
from database.models.task import Task


class TestTaskMessage:
    def test_creation(self):
        msg = TaskMessage("task-1", "parent-1", "Analyze data",
                          context={"key": "value"})
        assert msg.task_id == "task-1"
        assert msg.parent_task_id == "parent-1"
        assert msg.goal == "Analyze data"
        assert msg.context == {"key": "value"}

    def test_default_context(self):
        msg = TaskMessage("task-1", "parent-1", "goal")
        assert msg.context == {}


class TestReplyMessage:
    def test_creation(self):
        msg = ReplyMessage("task-1", True, {"output": "done"})
        assert msg.task_id == "task-1"
        assert msg.success is True
        assert msg.result == {"output": "done"}

    def test_default_result(self):
        msg = ReplyMessage("task-1", False)
        assert msg.result == {}


class TestStopSentinel:
    def test_sentinel_value(self):
        assert _STOP_SENTINEL == "STOP"


class TestMessageQueueService:
    def test_dispatch_and_collect_roundtrip(self):
        scenario_id = f"test-mqs-{uuid.uuid4().hex[:8]}"
        task_repo = TaskRepository()

        # Create subtasks in DB
        sub1_id = f"sub1-{uuid.uuid4().hex[:8]}"
        sub2_id = f"sub2-{uuid.uuid4().hex[:8]}"
        for sid, cmd in [(sub1_id, "echo hello"), (sub2_id, "echo world")]:
            task_repo.create(Task(
                task_id=sid,
                goal=f"Subtask {sid}",
                state=TaskState.PENDING.value,
                scenario_id=scenario_id,
                context=json.dumps({"command": cmd}),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ))

        # Dispatch
        messages = [
            TaskMessage(task_id=sub1_id, parent_task_id="",
                        goal="Subtask 1", context={"command": "echo hello"}),
            TaskMessage(task_id=sub2_id, parent_task_id="",
                        goal="Subtask 2", context={"command": "echo world"}),
        ]
        mqs.dispatch_subtasks(scenario_id, messages)

        # Collect replies
        replies = mqs.collect_replies(scenario_id, expected_count=2, timeout=30)

        assert len(replies) == 2
        reply_ids = {r.task_id for r in replies}
        assert sub1_id in reply_ids
        assert sub2_id in reply_ids

        for r in replies:
            assert r.success is True
            assert "output" in r.result

        # Verify DB state
        for sid in [sub1_id, sub2_id]:
            task = task_repo.find_by_task_id(sid)
            assert task.state == "success"

        mqs.stop_worker(scenario_id)
        assert not mqs.has_scenario(scenario_id)

    def test_idempotent_stop(self):
        scenario_id = f"test-stop-{uuid.uuid4().hex[:8]}"
        task_repo = TaskRepository()

        task_id = f"t-{uuid.uuid4().hex[:8]}"
        task_repo.create(Task(
            task_id=task_id,
            goal="test",
            state=TaskState.PENDING.value,
            scenario_id=scenario_id,
            context=json.dumps({"command": "echo ok"}),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ))

        mqs.dispatch_subtasks(scenario_id, [
            TaskMessage(task_id=task_id, parent_task_id="", goal="test",
                        context={"command": "echo ok"}),
        ])
        mqs.collect_replies(scenario_id, 1, timeout=10)

        mqs.stop_worker(scenario_id)
        mqs.stop_worker(scenario_id)  # second stop should not raise

    def test_timeout_returns_partial(self):
        scenario_id = f"test-timeout-{uuid.uuid4().hex[:8]}"
        task_repo = TaskRepository()

        fast1_id = f"fast1-{uuid.uuid4().hex[:8]}"
        fast2_id = f"fast2-{uuid.uuid4().hex[:8]}"
        slow_id = f"slow-{uuid.uuid4().hex[:8]}"

        for sid, cmd in [
            (fast1_id, "echo fast1"),
            (fast2_id, "echo fast2"),
            (slow_id, "sleep 10"),
        ]:
            task_repo.create(Task(
                task_id=sid,
                goal=f"task {sid}",
                state=TaskState.PENDING.value,
                scenario_id=scenario_id,
                context=json.dumps({"command": cmd}),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ))

        messages = [
            TaskMessage(task_id=fast1_id, parent_task_id="", goal="fast1",
                        context={"command": "echo fast1"}),
            TaskMessage(task_id=fast2_id, parent_task_id="", goal="fast2",
                        context={"command": "echo fast2"}),
            TaskMessage(task_id=slow_id, parent_task_id="", goal="slow",
                        context={"command": "sleep 10"}),
        ]
        mqs.dispatch_subtasks(scenario_id, messages)

        replies = mqs.collect_replies(scenario_id, expected_count=3, timeout=5)

        assert len(replies) >= 2
        fast_reply_ids = {r.task_id for r in replies}
        assert fast1_id in fast_reply_ids
        assert fast2_id in fast_reply_ids

        mqs.stop_worker(scenario_id)

    def test_scenario_isolation(self):
        s1 = f"iso-s1-{uuid.uuid4().hex[:8]}"
        s2 = f"iso-s2-{uuid.uuid4().hex[:8]}"
        task_repo = TaskRepository()

        t1_id = f"t1-{uuid.uuid4().hex[:8]}"
        t2_id = f"t2-{uuid.uuid4().hex[:8]}"

        for sid, tid in [(s1, t1_id), (s2, t2_id)]:
            task_repo.create(Task(
                task_id=tid,
                goal=f"task in {sid}",
                state=TaskState.PENDING.value,
                scenario_id=sid,
                context=json.dumps({"command": f"echo {sid}"}),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ))

        mqs.dispatch_subtasks(s1, [
            TaskMessage(task_id=t1_id, parent_task_id="", goal="s1 task",
                        context={"command": f"echo {s1}"}),
        ])
        mqs.dispatch_subtasks(s2, [
            TaskMessage(task_id=t2_id, parent_task_id="", goal="s2 task",
                        context={"command": f"echo {s2}"}),
        ])

        r1 = mqs.collect_replies(s1, 1, timeout=10)
        r2 = mqs.collect_replies(s2, 1, timeout=10)

        assert len(r1) == 1 and r1[0].task_id == t1_id
        assert len(r2) == 1 and r2[0].task_id == t2_id

        mqs.stop_worker(s1)
        mqs.stop_worker(s2)

    def test_has_scenario(self):
        scenario_id = f"has-{uuid.uuid4().hex[:8]}"
        assert not mqs.has_scenario(scenario_id)

        task_repo = TaskRepository()
        task_id = f"t-{uuid.uuid4().hex[:8]}"
        task_repo.create(Task(
            task_id=task_id,
            goal="test",
            state=TaskState.PENDING.value,
            scenario_id=scenario_id,
            context=json.dumps({"command": "echo ok"}),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ))

        mqs.dispatch_subtasks(scenario_id, [
            TaskMessage(task_id=task_id, parent_task_id="", goal="test",
                        context={"command": "echo ok"}),
        ])

        assert mqs.has_scenario(scenario_id)
        mqs.stop_worker(scenario_id)
