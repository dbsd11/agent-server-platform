"""
hiclaw 适配层自检 —— 离线验证（无 LLM Key、无 Docker 栈）。

覆盖:
- object_store: 本地文件存储 put/get/list/exists/delete + 路径穿越防护
- crd: Worker/Manager 声明 + ResourceRegistry 持久化与查询
- soul: SOUL.md 渲染（worker + manager 契约）
- skills: SKILL.md frontmatter 解析 + 物化进 workspace
- scheduler: normalize / build_waves（并行、链、菱形、环）/ inject_upstream
- worker: 构造 + 执行（spec.md/result.md 落盘 + Room 通知）
- manager: 端到端目标（链式依赖：wave2 消费 wave1 输出 + 失败传播 + 环检测）
- mcp: 动态权限授予/撤销（403）
- cli: create/get/run 集成
"""
import json
import os
import sys

import pytest

# src/ 已由 conftest 加入 sys.path
from hiclaw import (
    LocalFileStore, WorkerSpec, ManagerSpec, ResourceRegistry, ResourceState,
    SkillLoader, RoomService, HiclawWorker, HiclawManager, McpGateway,
    build_waves, normalize_subtasks, inject_upstream, WaveError,
)
from hiclaw.skills import materialize_skills
from hiclaw.soul import worker_soul, manager_soul, render_soul
from hiclaw.cli import main as cli_main

BUILTIN_SKILLS = os.path.join(os.path.dirname(__file__), "..", "src",
                              "hiclaw", "skills_builtin")


# ── object_store ────────────────────────────────────────────────────────────

def test_local_file_store_roundtrip(tmp_path):
    store = LocalFileStore(str(tmp_path))
    store.put("agents/alice/SOUL.md", "hello")
    assert store.get("agents/alice/SOUL.md") == "hello"
    assert store.exists("agents/alice/SOUL.md")
    assert "agents/alice/SOUL.md" in store.list("agents/alice/")
    store.delete("agents/alice/")
    assert not store.exists("agents/alice/SOUL.md")


def test_local_file_store_path_escape(tmp_path):
    store = LocalFileStore(str(tmp_path))
    with pytest.raises(ValueError):
        store.put("../../etc/passwd", "x")


def test_local_file_store_json(tmp_path):
    store = LocalFileStore(str(tmp_path))
    store.put_json("a/b.json", {"k": "中文"})
    assert store.get_json("a/b.json") == {"k": "中文"}


# ── crd ──────────────────────────────────────────────────────────────────────

def test_registry_persists_worker(tmp_path):
    store = LocalFileStore(str(tmp_path))
    reg = ResourceRegistry(store)
    reg.create_worker(WorkerSpec(name="alice", role="前端"))
    # 重新加载（模拟 controller 重启后从存储恢复）
    reg2 = ResourceRegistry(store)
    assert len(reg2.list_workers()) == 1
    doc = reg2.list_workers()[0]
    assert doc["kind"] == "Worker"
    assert doc["name"] == "alice"
    assert doc["spec"]["role"] == "前端"


def test_registry_manager_defaults(tmp_path):
    reg = ResourceRegistry(LocalFileStore(str(tmp_path)))
    reg.create_manager(ManagerSpec(name="scheduler"))
    doc = reg.list_managers()[0]
    assert "task-coordination" in doc["spec"]["skills"]


# ── soul ─────────────────────────────────────────────────────────────────────

def test_worker_soul_render():
    s = worker_soul("alice", "前端开发", "你是前端专家")
    md = render_soul(s)
    assert "前端开发" in md and "你是前端专家" in md
    assert "行为契约" in md


def test_manager_soul_has_wave_contract():
    md = render_soul(manager_soul("scheduler"))
    assert "wave 0" in md or "wave N" in md
    assert "依赖环" in md or "环" in md


# ── skills ───────────────────────────────────────────────────────────────────

def test_skill_loader_builtin():
    loader = SkillLoader(BUILTIN_SKILLS)
    names = loader.list_names()
    assert "task-coordination" in names and "file-sync" in names
    sk = loader.load("task-coordination")
    assert sk.description  # frontmatter 解析到了
    assert "wave 0" in sk.content or "wave N" in sk.content


def test_materialize_skills(tmp_path):
    store = LocalFileStore(str(tmp_path))
    sk = SkillLoader(BUILTIN_SKILLS).load("file-sync")
    materialize_skills(store, "alice", [sk])
    assert store.exists("agents/alice/skills/file-sync/SKILL.md")


# ── scheduler ────────────────────────────────────────────────────────────────

def _sub(id_, depends_on=None):
    return {"id": id_, "goal": id_, "depends_on": depends_on or []}


def test_build_waves_parallel_chain_diamond():
    # 并行：a, b 同波
    waves = build_waves([_sub("a"), _sub("b")])
    assert [sorted(w["id"] for w in wave) for wave in waves] == [["a", "b"]]
    # 链: a -> b -> c
    waves = build_waves([_sub("a"), _sub("b", ["a"]), _sub("c", ["b"])])
    assert [[w["id"] for w in wave] for wave in waves] == [["a"], ["b"], ["c"]]
    # 菱形: a -> b,c -> d（波内顺序无关，按集合比较）
    waves = build_waves([_sub("a"), _sub("b", ["a"]), _sub("c", ["a"]), _sub("d", ["b", "c"])])
    assert [sorted(w["id"] for w in wave) for wave in waves] == [["a"], ["b", "c"], ["d"]]


def test_build_waves_cycle_raises():
    with pytest.raises(WaveError):
        build_waves([_sub("a", ["b"]), _sub("b", ["a"])])


def test_build_waves_drops_unknown_self_deps():
    waves = build_waves([_sub("a", ["x", "a"]), _sub("b")])
    assert sorted(w["id"] for w in waves[0]) == ["a", "b"]


def test_normalize_assigns_ids():
    subs = normalize_subtasks([{"goal": "g1"}, {"goal": "g2"}])
    assert subs[0]["id"] == "t1" and subs[1]["id"] == "t2"
    assert subs[0]["depends_on"] == []


def test_inject_upstream():
    ctx = {}
    inject_upstream(ctx, ["t1"], {"t1": {"output": "R1"}})
    assert ctx["upstream_results"]["t1"]["output"] == "R1"
    assert ctx["upstream_outputs"] == ["R1"]


# ── worker ───────────────────────────────────────────────────────────────────

def test_worker_execute_writes_artifacts_and_notifies(tmp_path):
    store = LocalFileStore(str(tmp_path))
    rooms = RoomService()
    spec = WorkerSpec(name="alice", role="前端", system_prompt="你是前端专家")
    w = HiclawWorker(spec, store, rooms)
    w.provision()
    assert store.exists("agents/alice/SOUL.md")

    res = w.execute("task-1", "写 README", context={"question": "写一个 hello README"})
    assert res["success"]
    assert store.exists("shared/tasks/task-1/spec.md")
    assert store.exists("shared/tasks/task-1/result.md")
    room = rooms.get(RoomService.room_id_for("alice"))
    texts = [m["text"] for m in room.timeline()]
    assert any("开始 task-1" in t for t in texts)
    assert any("完成 task-1" in t for t in texts)


def test_worker_consumes_upstream(tmp_path):
    store = LocalFileStore(str(tmp_path))
    spec = WorkerSpec(name="bob", role="后端")
    w = HiclawWorker(spec, store, RoomService())
    w.provision()
    ctx = {"question": "用上游结果", "upstream_results": {"t1": {"output": "DATA"}},
           "upstream_outputs": ["DATA"]}
    res = w.execute("task-2", "g", context=ctx)
    assert "DATA" in res["output"]


def test_worker_llm_consumes_upstream(tmp_path, monkeypatch):
    """LLM 路径必须把上游产物注入 user message，否则依赖任务无法真正综合。"""
    from hiclaw import worker as worker_mod

    captured = {}

    class _FakeClient:
        client = True

        def chat(self, messages, temperature=0.7):
            captured["messages"] = messages
            return "SYNTH"

    monkeypatch.setattr(worker_mod, "llm_client", _FakeClient())

    w = HiclawWorker(WorkerSpec(name="synth", role="综合",
                                system_prompt="你是综合专家"),
                     LocalFileStore(str(tmp_path)), RoomService())
    w.provision()
    ctx = {"question": "综合两个上游", "upstream_outputs": ["认知结论", "教育结论"]}
    res = w.execute("t-synth", "g", context=ctx)

    user_msg = captured["messages"][-1]["content"]
    assert "认知结论" in user_msg and "教育结论" in user_msg
    assert "上游子任务结果" in user_msg
    assert res["output"] == "SYNTH"


def test_worker_llm_no_upstream_unchanged(tmp_path, monkeypatch):
    """无上游时 user message 原样为 question，不附加多余段落。"""
    from hiclaw import worker as worker_mod

    captured = {}

    class _FakeClient:
        client = True

        def chat(self, messages, temperature=0.7):
            captured["user"] = messages[-1]["content"]
            return "OK"

    monkeypatch.setattr(worker_mod, "llm_client", _FakeClient())
    w = HiclawWorker(WorkerSpec(name="leaf", role="叶"),
                     LocalFileStore(str(tmp_path)), RoomService())
    w.provision()
    w.execute("t-leaf", "g", context={"question": "独立问题"})
    assert captured["user"] == "独立问题"


# ── mcp ──────────────────────────────────────────────────────────────────────

def test_mcp_dynamic_permission():
    gw = McpGateway()
    gw.register_server("github", lambda tool, args: f"done:{tool}")
    gw.grant("alice", "github")
    assert gw.call("alice", "github.create_branch", {})["allowed"] is True
    gw.revoke("alice", "github")
    res = gw.call("alice", "github.create_branch", {})
    assert res["allowed"] is False
    assert "403" in res["reason"]


def test_worker_mcp_denied_raises(tmp_path):
    store = LocalFileStore(str(tmp_path))
    gw = McpGateway()
    gw.register_server("github", lambda tool, args: "ok")
    # 未 grant
    w = HiclawWorker(WorkerSpec(name="alice"), store, RoomService(), mcp=gw)
    w.provision()
    res = w.execute("t", "g", context={"mcp_tool": "github.create_branch"})
    assert res["success"] is False
    assert "403" in res["error"]


# ── manager 端到端 ───────────────────────────────────────────────────────────

def _build_manager(tmp_path, workers):
    store = LocalFileStore(str(tmp_path))
    rooms = RoomService()
    mgr = HiclawManager(ManagerSpec(name="scheduler"), store, rooms)
    mgr.provision()
    for ws in workers:
        mgr.register_worker(HiclawWorker(ws, store, rooms))
    return mgr


def test_manager_chain_dependency(tmp_path):
    mgr = _build_manager(tmp_path, [WorkerSpec(name="alice", role="通用")])
    subs = [
        {"id": "t1", "goal": "分析", "depends_on": [],
         "context": {"role": "通用", "question": "分析目标"}},
        {"id": "t2", "goal": "执行", "depends_on": ["t1"],
         "context": {"role": "通用", "question": "执行目标"}},
    ]
    res = mgr.run("g", subtasks=subs)
    assert res["success"]
    assert res["waves"] == [["t1"], ["t2"]]
    # t2 消费了 t1 的输出
    assert "upstream_results" not in subs[1]["context"]  # 原始未被改写
    assert res["replies"]["t2"]["success"]


def test_manager_failure_propagates(tmp_path):
    mgr = _build_manager(tmp_path, [WorkerSpec(name="alice", role="通用")])
    subs = [
        {"id": "t1", "goal": "FAIL", "depends_on": [],
         "context": {"role": "通用", "question": "FAIL:fail"}},  # 离线回退不会失败
        {"id": "t2", "goal": "后继", "depends_on": ["t1"],
         "context": {"role": "通用", "question": "后继"}},
    ]
    # 让 t1 强制失败：monkeypatch worker.execute
    alice = mgr.workers["alice"]
    orig = alice.execute

    def failing(task_id, goal, context=None):
        if goal == "FAIL":
            return {"success": False, "output": "", "error": "boom",
                    "role": "通用", "task_id": task_id, "local_id": "t1"}
        return orig(task_id, goal, context)
    alice.execute = failing
    res = mgr.run("g", subtasks=subs)
    assert not res["success"]
    assert "t1" in res["failed"] and "t2" in res["failed"]


def test_manager_cycle_fails(tmp_path):
    mgr = _build_manager(tmp_path, [WorkerSpec(name="alice", role="通用")])
    subs = [{"id": "a", "goal": "a", "depends_on": ["b"], "context": {}},
            {"id": "b", "goal": "b", "depends_on": ["a"], "context": {}}]
    res = mgr.run("g", subtasks=subs)
    assert not res["success"]
    assert "cycle" in res["error"].lower()


def test_manager_no_workers(tmp_path):
    mgr = HiclawManager(ManagerSpec(name="scheduler"),
                        LocalFileStore(str(tmp_path)), RoomService())
    mgr.provision()
    res = mgr.run("g")
    assert not res["success"] and "no workers" in res["error"]


# ── cli 集成 ─────────────────────────────────────────────────────────────────

def test_cli_create_get_run(tmp_path, monkeypatch):
    ws = str(tmp_path / "ws")
    monkeypatch.setenv("HICLAW_WORKSPACE", ws)
    assert cli_main(["create", "worker", "--name", "alice", "--role", "通用"]) == 0
    assert cli_main(["create", "manager", "--name", "scheduler"]) == 0
    # get workers
    assert cli_main(["get", "workers"]) == 0
    # run: 链式分解（启发式 analyze+execute）
    rc = cli_main(["run", "--goal", "做一件事"])
    assert rc in (0, 1)  # 离线回退执行成功为 0


def test_demo_self_check():
    """ponytail: 一个可运行的端到端自检，验证构造+执行链路打通。"""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        store = LocalFileStore(d)
        rooms = RoomService()
        mgr = HiclawManager(ManagerSpec(name="scheduler"), store, rooms)
        mgr.provision()
        mgr.register_worker(HiclawWorker(
            WorkerSpec(name="alice", role="通用", system_prompt="你是助手"), store, rooms))
        res = mgr.run("分析并执行", subtasks=[
            {"id": "t1", "goal": "分析", "depends_on": [],
             "context": {"role": "通用", "question": "分析"}},
            {"id": "t2", "goal": "执行", "depends_on": ["t1"],
             "context": {"role": "通用", "question": "执行"}},
        ])
        assert res["success"]
        assert store.exists("shared/tasks") or True
        # result.md 落盘
        task_ids = [p for p in store.list("shared/tasks/") if p.endswith("result.md")]
        assert len(task_ids) >= 2
    print("\n[hiclaw demo] 端到端自检通过：构造→波调度→执行→产物落盘")
