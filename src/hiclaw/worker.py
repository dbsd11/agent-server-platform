# HiclawWorker —— 执行 Agent（对应 hiclaw Worker 容器）
#
# 构造：WorkerSpec → 物化 SOUL/Skills → 创建共享 Room。
# 执行：拉取 spec.md → 执行（LLM 或离线回退）→ 写 result.md → Room 通知完成。
# 无状态：所有配置/产物在对象存储，崩溃后可重建。
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .object_store import ObjectStore
from .crd import WorkerSpec, ResourceState
from .soul import worker_soul, render_soul
from .skills import Skill, materialize_skills
from .room import Room, RoomService
from .mcp import McpGateway

try:
    from core.llm_client import llm_client
    from core.sandbox import Sandbox
    from logger import logger
except Exception:                       # 允许脱离主项目独立运行（如纯 hiclaw 测试）
    llm_client = None  # type: ignore
    Sandbox = None  # type: ignore
    import logging
    logger = logging.getLogger("hiclaw.worker")


MANAGER_NAME = "manager"


class HiclawWorker:
    """执行 Agent。每角色一个实例，对应一个 Worker 容器。"""

    def __init__(self, spec: WorkerSpec, store: ObjectStore,
                 rooms: RoomService, skills: Optional[List[Skill]] = None,
                 mcp: Optional[McpGateway] = None,
                 sandbox: Optional[Any] = None):
        self.spec = spec
        self.store = store
        self.rooms = rooms
        self.skills = skills or []
        self.mcp = mcp or McpGateway()
        self.sandbox = sandbox                   # 可选 Sandbox（脚本/命令执行）
        self.room: Optional[Room] = None
        self.soul = worker_soul(spec.name, spec.role, spec.system_prompt)

    # --- 构造（provision）-------------------------------------------------
    def provision(self) -> None:
        """物化 Worker：写 SOUL.md、skills，创建共享 Room。

        对应 hiclaw Manager 创建 Worker 时的：MinIO 配置 + skills 物化 + Room。
        """
        self.store.put(f"agents/{self.spec.name}/SOUL.md", render_soul(self.soul))
        materialize_skills(self.store, self.spec.name, self.skills)
        rid = RoomService.room_id_for(self.spec.name)
        self.room = self.rooms.get_or_create(rid, [MANAGER_NAME, self.spec.name])
        logger.info(f"HiclawWorker provisioned: {self.spec.name} "
                    f"(runtime={self.spec.runtime}, role={self.spec.role})")

    # --- 执行 -------------------------------------------------------------
    def execute(self, task_id: str, goal: str,
                context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行单个任务：RECEIVE → PROCESS → RESPOND。

        写 shared/tasks/<id>/spec.md 与 result.md，在 Room 通知完成。
        返回 {success, output, role, task_id}。
        """
        context = context or {}
        upstream = context.get("upstream_results", {})
        question = context.get("question") or goal

        # 写 spec.md（任务规格 + 上游注入）
        spec_md = self._render_spec(task_id, goal, question, upstream)
        self.store.put(f"shared/tasks/{task_id}/spec.md", spec_md)
        self.store.put_json(f"shared/tasks/{task_id}/meta.json", {
            "task_id": task_id, "worker": self.spec.name,
            "role": self.spec.role, "goal": goal,
        })
        if self.room:
            self.room.send(self.spec.name, f"开始 {task_id}: {goal}")

        try:
            proc = self._process(question, context)
            success = proc.get("success", True)
            output = proc.get("output", "")
            self.store.put(f"shared/tasks/{task_id}/result.md",
                           output if success else f"FAILED: {output}")
            if self.room:
                tag = "完成" if success else "失败"
                self.room.send(self.spec.name, f"{tag} {task_id}")
            result: Dict[str, Any] = {"success": success, "output": output,
                                      "role": self.spec.role, "task_id": task_id}
            if "returncode" in proc:
                result["returncode"] = proc["returncode"]
            if "stderr" in proc:
                result["stderr"] = proc["stderr"]
            return result
        except Exception as e:
            err = str(e)
            logger.error(f"Worker {self.spec.name} task {task_id} failed: {err}")
            self.store.put(f"shared/tasks/{task_id}/result.md", f"FAILED: {err}")
            if self.room:
                self.room.send(self.spec.name, f"失败 {task_id}: {err}")
            return {"success": False, "output": "", "error": err,
                    "role": self.spec.role, "task_id": task_id}

    def _process(self, question: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """PROCESS：按 context 类型分派执行，返回 {output, success?, returncode?, stderr?}。

        分派顺序：script → sandbox；command → sandbox；mcp_tool → MCP；
        否则 LLM Q&A（无 Key 离线回退）。
        """
        # 脚本/命令 → 沙箱执行（复用 core.sandbox.Sandbox）
        if "script" in context and self.sandbox is not None:
            return self._run_sandbox("script", task_id=context.get("task_id", ""),
                                     script=context["script"])
        if "command" in context and self.sandbox is not None:
            return self._run_sandbox("command", task_id=context.get("task_id", ""),
                                     command=context["command"])

        # MCP 工具调用（若 context 指定 mcp tool）
        mcp_tool = context.get("mcp_tool")
        if mcp_tool:
            res = self.mcp.call(self.spec.name, mcp_tool, context.get("mcp_args", {}))
            if not res.get("allowed"):
                raise PermissionError(f"MCP {mcp_tool} denied: {res.get('reason')}")
            return {"output": res.get("output", "")}

        if llm_client is not None and llm_client.client:
            user_content = self._compose_user_prompt(question, context)
            messages = [
                {"role": "system", "content": self.spec.system_prompt or
                 f"You are a {self.spec.role or 'helpful assistant'}."},
                {"role": "user", "content": user_content},
            ]
            resp = llm_client.chat(messages, temperature=0.7)
            if resp:
                return {"output": resp}
            logger.warning(f"LLM empty for {self.spec.name}, falling back to heuristic")

        # 离线回退：确定性产出，保证无 Key 亦可验证执行链路
        user_content = self._compose_user_prompt(question, context)
        return {"output": f"[{self.spec.role or 'worker'}] {user_content}"}

    def _run_sandbox(self, kind: str, task_id: str,
                     script: str = "", command: str = "") -> Dict[str, Any]:
        """经 Sandbox 执行脚本或命令。返回 {output, returncode, stderr, success}。"""
        if self.sandbox is None:
            raise RuntimeError("sandbox not configured for this worker")
        if kind == "script":
            r = self.sandbox.execute(task_id, {"script": script})
        else:
            r = self.sandbox.execute_command(task_id, command)
        return {"output": r.get("stdout", ""), "stderr": r.get("stderr", ""),
                "returncode": r.get("returncode", -1),
                "success": r.get("success", False)}

    def _compose_user_prompt(self, question: str,
                             context: Dict[str, Any]) -> str:
        """构造 LLM/回退用的 user prompt：question + 上游子任务输出。

        依赖任务（depends_on 非空）会拿到上游真实产物，从而能做真正的综合，
        而非仅凭一句泛指令重新生成。无上游时原样返回 question。
        """
        upstream = context.get("upstream_outputs") or []
        if not upstream:
            return question
        blocks = []
        for i, out in enumerate(upstream, 1):
            blocks.append(f"### 上游子任务 {i}\n{out}")
        sections = "\n\n---\n\n".join(blocks)
        return (f"{question}\n\n"
                "## 上游子任务结果（请在此基础上综合/延续，不要忽略其内容）\n\n"
                f"{sections}")

    def _render_spec(self, task_id: str, goal: str, question: str,
                     upstream: Dict[str, Any]) -> str:
        lines = [f"# Task {task_id}", "", f"**Goal**: {goal}",
                 f"**Worker**: {self.spec.name} ({self.spec.role})", "",
                 "## Question", "", question, ""]
        if upstream:
            lines += ["## Upstream Results", ""]
            for lid, res in upstream.items():
                lines.append(f"- {lid}: {res.get('output', '')}")
            lines.append("")
        return "\n".join(lines)
