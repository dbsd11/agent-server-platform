# agt CLI —— 对应 hiclaw 的 agt 声明式命令行（本地实现）
#
# 用法:
#   python -m hiclaw create worker --name alice --role 前端开发
#   python -m hiclaw create manager --name scheduler
#   python -m hiclaw get workers
#   python -m hiclaw get managers
#   python -m hiclaw run --goal "实现登录页 and 实现登录API"
from __future__ import annotations

import argparse
import json
import os
import sys

from .object_store import LocalFileStore
from .crd import (
    WorkerSpec, ManagerSpec, ManagerConfig, ResourceRegistry, ResourceState,
)
from .skills import SkillLoader
from .room import RoomService
from .worker import HiclawWorker
from .manager import HiclawManager

DEFAULT_WORKSPACE = os.environ.get("HICLAW_WORKSPACE", ".hiclaw-workspace")
BUILTIN_SKILLS = os.path.join(os.path.dirname(__file__), "skills_builtin")


def _store(args) -> LocalFileStore:
    return LocalFileStore(args.workspace)


def _registry(store) -> ResourceRegistry:
    return ResourceRegistry(store)


def _load_builtin_skills(names) -> list:
    loader = SkillLoader(BUILTIN_SKILLS)
    return [loader.load(n) for n in names if loader.load(n)]


def cmd_create_worker(args) -> int:
    store = _store(args)
    reg = _registry(store)
    spec = WorkerSpec(
        name=args.name, model=args.model, runtime=args.runtime,
        role=args.role or "", system_prompt=args.system_prompt or "",
        skills=args.skills or [],
    )
    reg.create_worker(spec)
    # 物化 Worker
    rooms = RoomService()
    skills = _load_builtin_skills(spec.skills) if spec.skills else []
    worker = HiclawWorker(spec, store, rooms, skills=skills)
    worker.provision()
    print(f"created worker: {spec.name} (runtime={spec.runtime})")
    return 0


def cmd_create_manager(args) -> int:
    store = _store(args)
    reg = _registry(store)
    spec = ManagerSpec(name=args.name, model=args.model, runtime=args.runtime)
    reg.create_manager(spec)
    print(f"created manager: {spec.name} (runtime={spec.runtime})")
    return 0


def cmd_get(args) -> int:
    store = _store(args)
    reg = _registry(store)
    if args.resource == "workers":
        rows = reg.list_workers()
    elif args.resource == "managers":
        rows = reg.list_managers()
    else:
        print(f"unknown resource: {args.resource}", file=sys.stderr)
        return 2
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    store = _store(args)
    reg = _registry(store)
    rooms = RoomService()

    docs = reg.list_managers()
    mgr_spec = _to_manager_spec(docs[0]) if docs else None
    if mgr_spec is None:
        mgr_spec = ManagerSpec(name="scheduler")
        reg.create_manager(mgr_spec)
    manager = HiclawManager(mgr_spec, store, rooms,
                            skills=_load_builtin_skills(mgr_spec.skills))
    manager.provision()

    # 注册所有已声明 Worker 为执行 Agent
    for doc in reg.list_workers():
        ws = _to_worker_spec(doc)
        skills = _load_builtin_skills(ws.skills) if ws.skills else []
        manager.register_worker(HiclawWorker(ws, store, rooms, skills=skills))

    result = manager.run(args.goal)
    print(json.dumps({"success": result["success"],
                      "waves": result["waves"],
                      "failed": result["failed"],
                      "replies": {k: {"success": v.get("success"),
                                      "output": v.get("output", "")}
                                  for k, v in result["replies"].items()}},
                     ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


def _to_worker_spec(doc: dict) -> WorkerSpec:
    s = doc["spec"]
    return WorkerSpec(
        name=s["name"], model=s.get("model", "qwen-plus"),
        runtime=s.get("runtime", "openclaw"),
        role=s.get("role", ""), system_prompt=s.get("system_prompt", ""),
        skills=s.get("skills", []),
        state=ResourceState(s.get("state", "Running")),
    )


def _to_manager_spec(doc: dict) -> ManagerSpec:
    s = doc["spec"]
    cfg = s.get("config") or {}
    config = ManagerConfig(
        heartbeat_interval=cfg.get("heartbeat_interval", 30),
        worker_idle_timeout=cfg.get("worker_idle_timeout", 600),
        notify_channel=cfg.get("notify_channel", "#scheduler-notify"),
    )
    return ManagerSpec(
        name=s["name"], model=s.get("model", "qwen-plus"),
        runtime=s.get("runtime", "openclaw"), skills=s.get("skills", []),
        config=config,
    )


def _spec_only(doc: dict) -> dict:
    return doc["spec"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hiclaw", description="hiclaw agt CLI (local)")
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("create", help="创建资源")
    sp_sub = sp.add_subparsers(dest="resource", required=True)
    w = sp_sub.add_parser("worker")
    w.add_argument("--name", required=True)
    w.add_argument("--model", default="qwen-plus")
    w.add_argument("--runtime", default="openclaw")
    w.add_argument("--role", default="")
    w.add_argument("--system-prompt", dest="system_prompt", default="")
    w.add_argument("--skills", nargs="*", default=[])
    w.set_defaults(func=cmd_create_worker)
    m = sp_sub.add_parser("manager")
    m.add_argument("--name", required=True)
    m.add_argument("--model", default="qwen-plus")
    m.add_argument("--runtime", default="openclaw")
    m.set_defaults(func=cmd_create_manager)

    g = sub.add_parser("get", help="查询资源")
    g.add_argument("resource", choices=["workers", "managers"])
    g.set_defaults(func=cmd_get)

    r = sub.add_parser("run", help="调度执行目标")
    r.add_argument("--goal", required=True)
    r.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
