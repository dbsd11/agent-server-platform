# HiclawManager —— 调度 Agent（对应 hiclaw Manager）
#
# 构造：ManagerSpec + soul → 物化 manager/SOUL.md。
# 执行：run(goal) → 分解（LLM 或启发式）→ 波调度 → 派发到 Worker →
#       收集结果 → 注入上游 → 传播失败 → 返回。
#
# 复用：scheduler.py 的纯函数；llm_client 的 decompose_goal（无 Key 回退启发式）。
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .object_store import ObjectStore
from .crd import ManagerSpec
from .soul import manager_soul, render_soul
from .skills import Skill
from .room import Room, RoomService
from .worker import HiclawWorker, MANAGER_NAME
from .scheduler import (
    normalize_subtasks, build_waves, inject_upstream, dependents_of, WaveError,
)

try:
    from core.llm_client import llm_client
    from logger import logger
except Exception:                       # 脱离主项目独立运行
    llm_client = None  # type: ignore
    import logging
    logger = logging.getLogger("hiclaw.manager")


class HiclawManager:
    """调度 Agent：目标分解 + 依赖波调度 + Worker 编排。"""

    def __init__(self, spec: ManagerSpec, store: ObjectStore,
                 rooms: RoomService, skills: Optional[List[Skill]] = None):
        self.spec = spec
        self.store = store
        self.rooms = rooms
        self.skills = skills or []
        self.soul = manager_soul(spec.name)
        # name -> HiclawWorker（按角色注册的执行 Agent）
        self.workers: Dict[str, HiclawWorker] = {}
        self.notify_room: Optional[Room] = None

    # --- 构造（provision）-------------------------------------------------
    def provision(self) -> None:
        """物化 Manager：写 manager/SOUL.md + skills，创建 notify Room。"""
        self.store.put("manager/SOUL.md", render_soul(self.soul))
        for sk in self.skills:
            self.store.put(f"manager/skills/{sk.name}/SKILL.md", sk.raw)
        rid = self.spec.config.notify_channel or "!scheduler-notify"
        self.notify_room = self.rooms.get_or_create(rid, [MANAGER_NAME])
        logger.info(f"HiclawManager provisioned: {self.spec.name}")

    def register_worker(self, worker: HiclawWorker) -> None:
        """注册一个执行 Agent（对应创建 Worker 并加入编排）。"""
        worker.provision()
        self.workers[worker.spec.name] = worker

    def find_worker_for_role(self, role: str) -> Optional[HiclawWorker]:
        """按角色名匹配 Worker；无匹配则回退首个（对应 _associate_role 语义）。"""
        if not self.workers:
            return None
        for w in self.workers.values():
            if w.spec.role == role or w.spec.name == role:
                return w
        first = next(iter(self.workers.values()))
        logger.warning(f"role '{role}' not matched, fallback to '{first.spec.name}'")
        return first

    # --- 执行 -------------------------------------------------------------
    def run(self, goal: str, subtasks: Optional[List[Dict[str, Any]]] = None,
            max_workers: int = 3, timeout_seconds: int = 300) -> Dict[str, Any]:
        """调度执行一个目标。

        - subtasks: 可选，直接提供分解结果（跳过 LLM）；用于测试与确定性场景。
        - 返回 {success, topic_id, subtasks, waves, replies, failed}。
        """
        if not self.workers:
            return {"success": False, "error": "no workers registered"}

        # REASON: 分解
        if subtasks is None:
            subtasks = self._decompose(goal)
        subtasks = normalize_subtasks(subtasks)

        try:
            waves = build_waves(subtasks)
        except WaveError as e:
            logger.error(f"HiclawManager: {e}")
            return {"success": False, "error": str(e)}

        topic_id = f"topic-{int(time.time())}"
        deadline = time.time() + timeout_seconds
        all_replies: Dict[str, Dict[str, Any]] = {}   # local_id -> reply
        failed_ids: set = set()

        # ACT + OBSERVE: 逐波派发与收集
        for wave_idx, wave in enumerate(waves):
            if time.time() > deadline:
                logger.warning(f"timeout before wave {wave_idx}")
                break

            wave_results: List[Dict[str, Any]] = []
            for sub in wave:
                lid = sub["id"]
                deps = [d for d in (sub.get("depends_on") or [])]
                # 前驱失败 → 跳过
                if any(d in failed_ids for d in deps):
                    logger.info(f"skip {lid}: predecessor failed")
                    continue
                ctx = dict(sub.get("context", {}) or {})
                inject_upstream(ctx, deps, all_replies)
                worker = self.find_worker_for_role(ctx.get("role", ""))
                if worker is None:
                    failed_ids.add(lid)
                    continue
                task_id = f"{topic_id}-{lid}"
                if self.notify_room:
                    self.notify_room.send(MANAGER_NAME,
                                          f"派发 {task_id} -> {worker.spec.name}")
                res = worker.execute(task_id, sub.get("goal", ""), ctx)
                res["local_id"] = lid
                wave_results.append(res)
                all_replies[lid] = res
                if not res.get("success"):
                    failed_ids.add(lid)

            # 传播失败到后继（标记 skipped，不派发）
            if failed_ids:
                self._propagate_failure(failed_ids, waves, wave_idx + 1,
                                        all_replies)

        success = not failed_ids
        if self.notify_room:
            self.notify_room.send(
                MANAGER_NAME,
                f"目标完成: {goal} ({'成功' if success else '部分失败'})")

        return {
            "success": success,
            "topic_id": topic_id,
            "subtasks": subtasks,
            "waves": [[s["id"] for s in w] for w in waves],
            "replies": all_replies,
            "failed": sorted(failed_ids),
        }

    # --- 分解 -------------------------------------------------------------
    def _decompose(self, goal: str) -> List[Dict[str, Any]]:
        """LLM 分解；无 Key/失败则启发式回退。"""
        if llm_client is not None and llm_client.client:
            try:
                role_names = [w.spec.role or w.spec.name
                              for w in self.workers.values()]
                ctx = {"execution_role_names": role_names,
                       "priority": 0, "timeout_seconds": 3600}
                subs = llm_client.decompose_goal(goal, ctx)
                if subs:
                    return subs
            except Exception as e:
                logger.warning(f"LLM decompose failed, heuristic fallback: {e}")

        # 启发式：含 " and "/"与/并" 则拆分，否则 analyze+execute 链
        lower = goal.lower()
        if " and " in lower:
            parts = [p.strip() for p in goal.split(" and ") if p.strip()]
            if len(parts) > 1:
                return [{"id": f"t{i+1}", "goal": p, "type": "execution",
                         "depends_on": [],
                         "context": {"role": "", "question": p}}
                        for i, p in enumerate(parts)]
        return [
            {"id": "t1", "goal": f"分析: {goal}", "type": "execution",
             "depends_on": [],
             "context": {"role": "", "question": f"分析目标: {goal}"}},
            {"id": "t2", "goal": f"执行: {goal}", "type": "execution",
             "depends_on": ["t1"],
             "context": {"role": "", "question": f"执行目标: {goal}"}},
        ]

    def _propagate_failure(self, failed_ids: set,
                           waves: List[List[Dict[str, Any]]],
                           start_wave: int,
                           all_replies: Dict[str, Dict[str, Any]]) -> None:
        """将失败传播到后继波：标记 skipped。"""
        for wave in waves[start_wave:]:
            for sub in wave:
                deps = [d for d in (sub.get("depends_on") or [])]
                if any(d in failed_ids for d in deps):
                    all_replies[sub["id"]] = {
                        "success": False, "output": "",
                        "error": "skipped: predecessor failed",
                        "local_id": sub["id"]}
                    failed_ids.add(sub["id"])
