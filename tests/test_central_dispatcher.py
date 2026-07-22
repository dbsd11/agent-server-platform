"""
Unit tests for core/central_dispatcher.py.

Covers:
- finalize_task: writes a reply row in the legacy shape + flips task state
- run_task_locally: full in-process execution (LLM stubbed) -> reply row
- CentralDispatcher._dispatch_one routing: no-server -> local, connected ->
  forward, offline -> defer
- _bootstrap_offset: first run jumps to MAX(messages.id)
"""
import json
import uuid
from datetime import datetime

import pytest

from core.central_dispatcher import (
    CentralDispatcher, finalize_task, run_task_locally, GLOBAL_CONSUMER_ID,
)
from core.message_queue import TaskMessage
from core.state_machine import TaskState
from database.repositories.message_repository import MessageRepository
from database.repositories.consumer_offset_repository import ConsumerOffsetRepository
from database.repositories.task_repository import TaskRepository
from database.models.task import Task


# --- helpers -----------------------------------------------------------------

def _create_task(task_repo, task_id, scenario_id="scn"):
    task_repo.create(Task(
        task_id=task_id, goal="g", state=TaskState.PENDING.value,
        scenario_id=scenario_id, context="{}",
        created_at=datetime.now(), updated_at=datetime.now(),
    ))


def _reply_rows(scenario_id):
    rows = MessageRepository().find_by_scenario_id(scenario_id, limit=50)
    return [r for r in rows if r.message_type == "reply"]


@pytest.fixture
def stub_llm(monkeypatch):
    """Replace the shared LLM singleton's chat with a deterministic stub."""
    from core import llm_client
    monkeypatch.setattr(llm_client.llm_client, "client", object())  # truthy
    monkeypatch.setattr(
        llm_client.llm_client, "chat",
        lambda messages, temperature=0.7: "42",
    )
    return llm_client.llm_client


# --- finalize_task -----------------------------------------------------------

class TestFinalizeTask:
    def test_success_marks_completed_and_writes_reply(self, task_repo):
        tid = f"fin-{uuid.uuid4().hex[:8]}"
        scn = f"scn-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)

        result = {"success": True, "output": "42", "role": "数学家", "question": "?"}
        finalize_task(scn, tid, result)

        task = task_repo.find_by_task_id(tid)
        assert task.state == "success"

        replies = _reply_rows(scn)
        assert len(replies) == 1
        content = json.loads(replies[0].content)
        assert content["task_id"] == tid
        assert content["success"] is True
        assert content["result"] == result
        # legacy wire format
        assert replies[0].from_agent == "execution"
        assert replies[0].to_agent == "scheduling"
        assert replies[0].message_type == "reply"

    def test_failure_marks_failed_and_writes_reply(self, task_repo):
        tid = f"fin-{uuid.uuid4().hex[:8]}"
        scn = f"scn-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)

        finalize_task(scn, tid, {"success": False, "error": "boom"})

        assert task_repo.find_by_task_id(tid).state == "failed"
        content = json.loads(_reply_rows(scn)[0].content)
        assert content["success"] is False
        assert content["result"]["error"] == "boom"

    def test_success_stamps_agent_role_name_duration(self, task_repo):
        tid = f"fin-{uuid.uuid4().hex[:8]}"
        scn = f"scn-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)

        result = {"success": True, "output": "42", "role": "数学家"}
        finalize_task(scn, tid, result, agent_name="ExecutionAgent",
                      agent_role="数学家", execution_duration=1.23)

        task = task_repo.find_by_task_id(tid)
        assert task.state == "success"
        assert task.agent_name == "ExecutionAgent"
        assert task.agent_role == "数学家"
        assert task.execution_duration == 1.23


# --- run_task_locally --------------------------------------------------------

class TestRunTaskLocally:
    def test_runs_agent_and_writes_reply(self, task_repo, stub_llm):
        tid = f"loc-{uuid.uuid4().hex[:8]}"
        scn = f"scn-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)

        msg = TaskMessage(
            task_id=tid, parent_task_id="p", goal="What is 2+2?",
            context={"role": "数学家", "system_prompt": "math expert",
                     "question": "What is 2+2?"},
        )
        run_task_locally(scn, msg)

        task = task_repo.find_by_task_id(tid)
        assert task.state == "success"
        content = json.loads(_reply_rows(scn)[0].content)
        assert content["success"] is True
        assert content["result"]["output"] == "42"

    def test_stamps_agent_role_name_duration(self, task_repo, stub_llm):
        tid = f"loc-{uuid.uuid4().hex[:8]}"
        scn = f"scn-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)

        run_task_locally(scn, TaskMessage(
            tid, "p", "?",
            context={"role": "数学家", "system_prompt": "s", "question": "?"},
        ))

        task = task_repo.find_by_task_id(tid)
        assert task.state == "success"
        assert task.agent_name == "ExecutionAgent"
        assert task.agent_role == "数学家"
        assert task.execution_duration is not None and task.execution_duration >= 0

    def test_agent_failure_still_writes_reply(self, task_repo, monkeypatch):
        # LLM raises -> run_task_locally must still finalize (failed reply row).
        from core import llm_client
        monkeypatch.setattr(llm_client.llm_client, "client", object())

        def _boom(messages, temperature=0.7):
            raise RuntimeError("llm down")
        monkeypatch.setattr(llm_client.llm_client, "chat", _boom)

        tid = f"loc-{uuid.uuid4().hex[:8]}"
        scn = f"scn-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)

        run_task_locally(scn, TaskMessage(tid, "p", "?",
                                          {"role": "r", "system_prompt": "s"}))

        assert task_repo.find_by_task_id(tid).state == "failed"
        content = json.loads(_reply_rows(scn)[0].content)
        assert content["success"] is False
        assert "llm down" in content["result"]["error"]


# --- _bootstrap_offset -------------------------------------------------------

class TestBootstrapOffset:
    def test_first_run_sets_offset_to_max_message_id(self, task_repo):
        # Write a dispatch row so MAX(id) > 0.
        scn = f"bs-{uuid.uuid4().hex[:8]}"
        tid = f"bs-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)
        from core.message_queue import mqs
        mqs.dispatch_subtasks(scn, [TaskMessage(tid, "p", "g", {})])

        offset_repo = ConsumerOffsetRepository()
        # Ensure no global offset yet.
        assert offset_repo.find_by_id(GLOBAL_CONSUMER_ID) is None

        d = CentralDispatcher(max_workers=1)
        d._bootstrap_offset()

        max_id = MessageRepository().max_message_id()
        assert offset_repo.get_offset(GLOBAL_CONSUMER_ID) == max_id

    def test_second_run_leaves_offset_unchanged(self, task_repo):
        scn = f"bs2-{uuid.uuid4().hex[:8]}"
        tid = f"bs2-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)
        from core.message_queue import mqs
        mqs.dispatch_subtasks(scn, [TaskMessage(tid, "p", "g", {})])

        d = CentralDispatcher(max_workers=1)
        d._bootstrap_offset()
        first = ConsumerOffsetRepository().get_offset(GLOBAL_CONSUMER_ID)

        # A new dispatcher instance must NOT clobber the existing offset.
        CentralDispatcher(max_workers=1)._bootstrap_offset()
        assert ConsumerOffsetRepository().get_offset(GLOBAL_CONSUMER_ID) == first


# --- _dispatch_one routing ---------------------------------------------------

class _FakeWS:
    """In-memory stand-in for WSDispatcher exposing the routing surface."""
    def __init__(self, connected_servers):
        self._connected = set(connected_servers)
        self.forwarded = []   # (server_id, scenario_id, msg)
        self.deferred = []    # (server_id, scenario_id, msg)

    def is_connected(self, server_id):
        return server_id in self._connected

    def schedule_forward(self, server_id, scenario_id, msg):
        self.forwarded.append((server_id, scenario_id, msg))

    def defer(self, server_id, scenario_id, msg):
        self.deferred.append((server_id, scenario_id, msg))


class TestDispatchOneRouting:
    def test_no_server_runs_locally(self, task_repo, stub_llm):
        tid = f"r1-{uuid.uuid4().hex[:8]}"
        scn = f"r1-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)
        d = CentralDispatcher(max_workers=1)
        d.ws = _FakeWS(connected_servers=["node-1"])

        d._dispatch_one(scn, TaskMessage(tid, "p", "?",
                                         {"role": "r", "system_prompt": "s",
                                          "question": "?"}))
        # flush the thread pool

        assert task_repo.find_by_task_id(tid).state == "success"
        assert d.ws.forwarded == [] and d.ws.deferred == []

    def test_connected_server_is_forwarded(self, task_repo):
        tid = f"r2-{uuid.uuid4().hex[:8]}"
        scn = f"r2-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)
        d = CentralDispatcher(max_workers=1)
        d.ws = _FakeWS(connected_servers=["node-1"])

        msg = TaskMessage(tid, "p", "?", {"server_id": "node-1", "role": "r"})
        d._dispatch_one(scn, msg)

        assert len(d.ws.forwarded) == 1
        fwd_sid, fwd_scn, fwd_msg = d.ws.forwarded[0]
        assert fwd_sid == "node-1"
        assert fwd_scn == scn
        assert fwd_msg.task_id == tid
        assert d.ws.deferred == []
        # Task is NOT finalized by the dispatcher thread (forward_task owns that);
        # it should still be pending here.
        assert task_repo.find_by_task_id(tid).state == TaskState.PENDING.value

    def test_offline_server_is_deferred(self, task_repo):
        tid = f"r3-{uuid.uuid4().hex[:8]}"
        scn = f"r3-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)
        d = CentralDispatcher(max_workers=1)
        d.ws = _FakeWS(connected_servers=[])  # node-1 NOT connected

        msg = TaskMessage(tid, "p", "?", {"server_id": "node-1", "role": "r"})
        d._dispatch_one(scn, msg)

        assert d.ws.forwarded == []
        assert len(d.ws.deferred) == 1
        assert d.ws.deferred[0][0] == "node-1"
        # No reply written yet (task parked).
        assert _reply_rows(scn) == []
        assert task_repo.find_by_task_id(tid).state == TaskState.PENDING.value

    def test_no_ws_subsystem_falls_back_to_local(self, task_repo, stub_llm):
        tid = f"r4-{uuid.uuid4().hex[:8]}"
        scn = f"r4-{uuid.uuid4().hex[:8]}"
        _create_task(task_repo, tid, scn)
        d = CentralDispatcher(max_workers=1)
        d.ws = None  # WS subsystem not running

        d._dispatch_one(scn, TaskMessage(tid, "p", "?",
                                         {"server_id": "node-1", "role": "r",
                                          "system_prompt": "s", "question": "?"}))

        assert task_repo.find_by_task_id(tid).state == "success"
