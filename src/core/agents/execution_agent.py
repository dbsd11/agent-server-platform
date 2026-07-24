# Execution Agent —— 基于 hiclaw 底层的执行 Agent
#
# 设计：ExecutionAgent 是 BaseAgent 契约的薄 facade，执行底层委托给 hiclaw 的
# HiclawWorker（spec.md/result.md 持久化 + Room 通知 + 沙箱/MCP/Q&A 分派）。
# 调用方（task_runner / central_dispatcher / agent_manager）无需改动，也无须
# runtime 标记：role + system_prompt 直接流入 Worker 的 SOUL。
#
# 复用：core.sandbox.Sandbox（脚本/命令隔离执行），不另造沙箱。
import tempfile
from typing import Any, Dict

from .base_agent import BaseAgent
from core.event_bus import event_bus
from core.sandbox import Sandbox
from logger import logger

# hiclaw 底层
from hiclaw.object_store import LocalFileStore
from hiclaw.crd import WorkerSpec
from hiclaw.room import RoomService
from hiclaw.worker import HiclawWorker


class ExecutionAgent(BaseAgent):
    """
    执行 Agent：hiclaw 底层驱动。

    职责：
    - 角色化任务执行（role + system_prompt → Worker SOUL）
    - 脚本/命令沙箱执行、MCP 工具调用、LLM Q&A 三路分派
    - 产物持久化（shared/tasks/<id>/spec.md|result.md）+ Room 通知
    """

    def __init__(self):
        self.config: Dict[str, Any] = {}
        self.role = None
        self.system_prompt = None
        self.sandbox = Sandbox()
        # hiclaw 执行底层（per-agent Worker + 对象存储 + Room）
        self._worker: HiclawWorker = None  # type: ignore
        self._store = None
        self._rooms = None

    def get_agent_type(self) -> str:
        return "execution"

    def initialize(self, config: Dict[str, Any]) -> None:
        """初始化：角色配置 + 沙箱 + hiclaw Worker 物化。"""
        self.config = config
        self.role = config.get("role", "general assistant")
        self.system_prompt = config.get(
            "system_prompt", "You are a helpful assistant.")
        self.sandbox.initialize()

        # hiclaw 底层：per-agent temp store + Worker（共享 sandbox）
        self._store = LocalFileStore(tempfile.mkdtemp(prefix="hiclaw_exec_"))
        self._rooms = RoomService()
        spec = WorkerSpec(
            name=f"exec-{id(self):x}", role=self.role,
            system_prompt=self.system_prompt,
        )
        self._worker = HiclawWorker(spec, self._store, self._rooms,
                                    sandbox=self.sandbox)
        self._worker.provision()
        logger.info(f"ExecutionAgent initialized with role: {self.role} "
                    f"(hiclaw-backed)")

    def _plan_execution(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """分类任务：script / command / qa；都没有则抛 ValueError。"""
        if "script" in context:
            return {"type": "script", "script": context["script"]}
        if "command" in context:
            return {"type": "command", "command": context["command"]}
        if context.get("question") or context.get("goal"):
            return {"type": "qa"}
        raise ValueError("No script or command provided")

    def run(self, task_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行任务：RECEIVE → PROCESS（hiclaw Worker）→ RESPOND。

        发出 task.execution_started / completed / failed 事件。
        返回 {success, output, role, task_id, returncode?, stderr?, error?}。
        """
        event_bus.emit("task.execution_started", {"task_id": task_id})

        try:
            plan = self._plan_execution(context)
            event_bus.emit("task.execution_planned", {
                "task_id": task_id,
                "plan_type": plan["type"],
                "role": self.role,
            })
            ctx = dict(context)
            ctx["plan"] = plan
            ctx["task_id"] = task_id
            goal = context.get("goal") or context.get("question", "")

            res = self._worker.execute(task_id, goal, ctx)

            result: Dict[str, Any] = {
                "success": res.get("success", False),
                "output": res.get("output", ""),
                "role": self.role,
                "task_id": task_id,
            }
            for k in ("returncode", "stderr"):
                if k in res:
                    result[k] = res[k]
            if not result["success"] and "error" in res:
                result["error"] = res["error"]

            event_bus.emit("task.execution_completed", {
                "task_id": task_id,
                "role": self.role,
                "response_length": len(result["output"]),
            })
            return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"ExecutionAgent error for task {task_id}: {error_msg}")
            event_bus.emit("task.execution_failed", {
                "task_id": task_id,
                "error": error_msg,
            })
            return {"success": False, "output": "", "error": error_msg}

    def cleanup(self) -> None:
        """清理沙箱（Worker 无额外资源，store 为 temp dir 随进程回收）。"""
        self.sandbox.cleanup()
        logger.info("ExecutionAgent cleaned up")
