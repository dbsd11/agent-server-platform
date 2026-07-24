---
name: file-sync
description: 与对象存储同步 workspace——拉取 spec、推送 result
---

# file-sync

Worker 无状态，配置与产物持久化在对象存储。

- 拉取：`shared/tasks/<id>/spec.md`、`meta.json`
- 推送：`shared/tasks/<id>/result.md`

使用 `mc`（MinIO client）或等价同步脚本镜像对象。
