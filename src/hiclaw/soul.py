# SOUL —— Agent 人格/契约定义，写入 agents/<name>/SOUL.md 或 manager/SOUL.md
#
# 对应 hiclaw Manager 的 soul 覆盖与 Worker 的 SOUL.md。SOUL 是 Agent 的
# 「灵魂文件」，定义其角色、系统提示词与行为契约。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Soul:
    """SOUL 定义。

    - Worker：role + system_prompt + 可执行契约要点。
    - Manager：调度契约（波调度 / 依赖注入 / 失败传播规则）。
    """
    name: str
    kind: str                          # "worker" | "manager"
    role: str = ""
    system_prompt: str = ""
    directives: List[str] = field(default_factory=list)   # 行为契约条目

    def render(self) -> str:
        lines = [f"# SOUL — {self.name}", ""]
        if self.role:
            lines += [f"**角色**: {self.role}", ""]
        if self.system_prompt:
            lines += ["## 系统提示词", "", self.system_prompt, ""]
        if self.directives:
            lines += ["## 行为契约", ""]
            for i, d in enumerate(self.directives, 1):
                lines.append(f"{i}. {d}")
            lines.append("")
        return "\n".join(lines)


# Manager 调度契约 —— 转译自 src/core/agents/scheduling_agent.py 的不变量：
# _build_waves / _inject_upstream / _propagate_failure / _normalize_subtasks。
MANAGER_DIRECTIVES = [
    "每个子任务带唯一 id 与 depends_on 数组；缺失则归入 wave 0（并行）。",
    "拓扑分层：无依赖 → wave 0；依赖全在更早波 → wave N。波内并行，波间串行。",
    "后继子任务的 spec 必须注入前驱结果（upstream_results / upstream_outputs）。",
    "前驱失败的后继标记 skipped（failed），不基于缺失输入运行。",
    "检测到依赖环即整体失败，不得死锁。",
    "周期性 heartbeat：巡视各 Worker Room 近期活动并询问进度。",
]


def worker_soul(name: str, role: str, system_prompt: str) -> Soul:
    return Soul(name=name, kind="worker", role=role,
                system_prompt=system_prompt,
                directives=[
                    "从 shared/tasks/<id>/spec.md 拉取任务规格（mc 同步）。",
                    "执行后将产物写入 shared/tasks/<id>/result.md。",
                    "完成后在 Room 通知 Manager：'完成 <task_id>'。",
                ])


def manager_soul(name: str) -> Soul:
    return Soul(name=name, kind="manager", role="调度 Agent",
                system_prompt="你是 hiclaw 调度 Agent，负责目标分解、依赖波调度与 Worker 编排。",
                directives=MANAGER_DIRECTIVES)


def render_soul(soul: Soul) -> str:
    return soul.render()
