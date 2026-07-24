---
name: task-coordination
description: 依赖波调度——目标分解为子任务 DAG，波内并行、波间串行，注入上游结果，传播失败
---

# task-coordination

将目标分解为带 `id` 与 `depends_on` 的子任务，构成无环 DAG。

## 规则

1. 无依赖子任务 → wave 0，并行派发。
2. 依赖全在更早波的子任务 → wave N，串行于前波之后。
3. 后继子任务 spec 注入 `upstream_results` 与 `upstream_outputs`。
4. 前驱失败 → 后继标记 `skipped`，不基于缺失输入运行。
5. 检测到环 → 整体失败，不死锁。
