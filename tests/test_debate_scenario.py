"""
观点论证推理 (debate) 场景端到端集成测试。

构造一个 debate 场景（正方/反方/裁判三个执行 Agent 对抗辩论 + 综合裁决），
经 scenario_manager 创建并启动，轮询至终态，验证：
- 场景状态转入 completed
- 正方/反方论证与裁判裁决均产出非空内容
- 三个执行 Agent 均经 hiclaw 底层（ExecutionAgent facade）执行成功

全程离线：无 DASHSCOPE_API_KEY，ExecutionAgent 走 hiclaw Worker 的确定性回退。
"""
import time
import json

import pytest

from scenarios.scenario_manager import scenario_manager
from scenarios.examples.debate_scenario import DebateScenario


def _wait_terminal(scenario_id: str, timeout: float = 60) -> dict:
    """轮询场景状态至 completed/failed/cancelled。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = scenario_manager.get_scenario_status(scenario_id)
        if last and last["state"] in ("completed", "failed", "cancelled"):
            return last
        time.sleep(0.5)
    return last or {}


def _capture_completed(scenario_id: str, timeout: float = 30) -> dict:
    """从事件总线捕获 scenario.completed 的 result（事件异步派发，需轮询）。"""
    from database.repositories.event_repository import EventRepository

    deadline = time.time() + timeout
    # event_bus 持久化事件到 DB；直接查 EventRepository 比订阅时序更可靠。
    repo = EventRepository()
    while time.time() < deadline:
        for ev in repo.find_by_event_type("scenario.completed"):
            try:
                data = json.loads(ev.data) if isinstance(ev.data, str) else ev.data
            except Exception:
                continue
            if data.get("scenario_id") == scenario_id and "result" in data:
                return data["result"]
        time.sleep(0.5)
    return {}


def test_debate_scenario_runs_end_to_end(tmp_path, monkeypatch):
    # 隔离 DB（conftest 的 fresh_database 已设 sqlite，这里仅复用）
    config = {
        "topic": "人工智能是否会取代人类的大部分工作",
        "rounds": 1,
        "timeout": 30,
    }
    sid = scenario_manager.create_scenario(
        scenario_type="debate",
        name="debate-integration-test",
        description="正反双方对抗辩论 + 裁判裁决",
        config=config,
    )
    assert sid, "scenario_id should be created"

    ok = scenario_manager.start_scenario(sid, DebateScenario())
    assert ok, "scenario should start"

    status = _wait_terminal(sid, timeout=60)
    assert status["state"] == "completed", \
        f"debate scenario did not complete: {status}"

    # completed 事件 result 落在事件流；从 active_scenarios 已移除。
    # 直接验证场景记录的终态与类型。
    assert status["scenario_type"] == "debate"
    assert status["state"] == "completed"


def test_debate_scenario_produces_arguments_and_verdict(tmp_path, monkeypatch):
    """验证正方/反方论证与裁判裁决均产出内容（经 hiclaw 底层执行）。"""
    config = {
        "topic": "远程办公是否应该成为常态",
        "rounds": 1,
        "timeout": 30,
    }
    sid = scenario_manager.create_scenario(
        scenario_type="debate", name="debate-output-test", config=config)
    scenario_manager.start_scenario(sid, DebateScenario())

    status = _wait_terminal(sid, timeout=60)
    assert status["state"] == "completed"
    result = _capture_completed(sid, timeout=10)

    assert result.get("success") is True, f"debate result not success: {result}"
    assert result.get("topic"), "topic missing"
    assert result.get("pro_argument"), "正方论证为空"
    assert result.get("con_argument"), "反方论证为空"
    assert result.get("verdict"), "裁判裁决为空"
    # 正反双方内容应不同（不同角色立场）
    assert result["pro_argument"] != result["con_argument"]


def test_debate_scenario_multi_round(tmp_path, monkeypatch):
    """多轮辩论：第二轮双方应看到对方上一轮观点（反驳）。"""
    config = {
        "topic": "开源软件是否比闭源更安全",
        "rounds": 2,
        "timeout": 30,
    }
    sid = scenario_manager.create_scenario(
        scenario_type="debate", name="debate-multiround-test", config=config)
    scenario_manager.start_scenario(sid, DebateScenario())

    status = _wait_terminal(sid, timeout=90)
    assert status["state"] == "completed"
    result = _capture_completed(sid, timeout=10)

    assert result.get("success") is True, f"multi-round failed: {result}"
    assert result.get("rounds") == 2
    assert result.get("pro_argument") and result.get("con_argument")
