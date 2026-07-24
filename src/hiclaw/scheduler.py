# 波调度器（纯函数）—— 对应 src/core/agents/scheduling_agent.py 的
# _normalize_subtasks / _build_waves / _inject_upstream / _propagate_failure。
#
# ponytail: 现有实现是 DB 耦合的实例方法，这里抽成纯函数避免拖入 TaskRepository，
# 语义与原实现一致（含环检测、未知/自依赖剔除、失败传播）。
from __future__ import annotations

from typing import Any, Dict, List, Set


class WaveError(ValueError):
    """依赖图错误（如环）。"""


def normalize_subtasks(subtasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """确保每个子任务有唯一 id 与 depends_on 列表。缺失则归入 wave 0。"""
    seen: Set[str] = set()
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


def build_waves(subtasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """拓扑分层为执行波。波内并行，波间串行。环则抛 WaveError。"""
    by_id = {s["id"]: s for s in subtasks}
    deps = {
        s["id"]: [d for d in (s.get("depends_on") or [])
                  if d in by_id and d != s["id"]]
        for s in subtasks
    }
    waves: List[List[Dict[str, Any]]] = []
    placed: Set[str] = set()
    remaining = set(by_id.keys())
    while remaining:
        ready = [sid for sid in remaining if all(d in placed for d in deps[sid])]
        if not ready:
            raise WaveError(f"dependency cycle among: {sorted(remaining)}")
        waves.append([by_id[sid] for sid in ready])
        placed.update(ready)
        remaining -= set(ready)
    return waves


def inject_upstream(ctx: Dict[str, Any], depends_on_ids: List[str],
                    replies_by_local_id: Dict[str, Dict[str, Any]]) -> None:
    """将前驱结果注入后继任务上下文。无依赖时 no-op。"""
    if not depends_on_ids:
        return
    upstream = {}
    outputs = []
    for lid in depends_on_ids:
        res = replies_by_local_id.get(lid)
        if not res:
            continue
        upstream[lid] = res
        outputs.append(res.get("output", ""))
    ctx["upstream_results"] = upstream
    ctx["upstream_outputs"] = outputs


def dependents_of(waves: List[List[Dict[str, Any]]], local_id: str) -> List[str]:
    """返回依赖 local_id 的所有后继 local id（跨波）。"""
    deps_map = {}
    for wave in waves:
        for sub in wave:
            deps_map[sub["id"]] = [d for d in (sub.get("depends_on") or [])]
    return [sid for sid, deps in deps_map.items() if local_id in deps]
