# McpGateway —— 模拟 Higress 托管的 MCP Server + 动态权限控制
#
# 对应 hiclaw 的 mcporter + Higress MCP route。凭据集中持有，Worker 仅经
# consumer key 调用；Manager 可动态授予/撤销 Worker 对某 MCP 的访问，
# 撤销后调用返回 403。本地实现用内存权限表模拟。
from __future__ import annotations

from typing import Any, Dict, Set


class McpGateway:
    """MCP 网关：注册 MCP server + 按 worker 身份做动态授权。"""

    def __init__(self):
        # server name -> handler callable(args)->str
        self._servers: Dict[str, Any] = {}
        # (worker_name, server_name) 授权集合
        self._grants: Set[tuple] = set()

    def register_server(self, name: str, handler) -> None:
        self._servers[name] = handler

    def grant(self, worker_name: str, server_name: str) -> None:
        self._grants.add((worker_name, server_name))

    def revoke(self, worker_name: str, server_name: str) -> None:
        self._grants.discard((worker_name, server_name))

    def is_allowed(self, worker_name: str, server_name: str) -> bool:
        return (worker_name, server_name) in self._grants

    def call(self, worker_name: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """调用 MCP tool。tool 形如 'github.create_branch'。

        返回 {allowed, output|reason}。撤销授权时 allowed=False（对应 403）。
        """
        server_name = tool.split(".", 1)[0] if "." in tool else tool
        if not self.is_allowed(worker_name, server_name):
            return {"allowed": False, "reason": f"403: {worker_name} not granted {server_name}"}
        handler = self._servers.get(server_name)
        if handler is None:
            return {"allowed": False, "reason": f"unknown mcp server: {server_name}"}
        try:
            out = handler(tool, args)
            return {"allowed": True, "output": out}
        except Exception as e:
            return {"allowed": False, "reason": str(e)}
