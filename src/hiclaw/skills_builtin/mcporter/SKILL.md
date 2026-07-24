---
name: mcporter
description: 经 Higress 网关调用 MCP Server（凭据集中持有，Worker 不接触原始凭据）
---

# mcporter

调用 Higress 托管的 MCP Server（如 github、git-delegation）。

- PAT 等凭据由 MCP Server 集中持有，Worker 仅经 consumer key 调用。
- 权限由 Manager 动态授予/撤销；撤销后调用返回 403。
