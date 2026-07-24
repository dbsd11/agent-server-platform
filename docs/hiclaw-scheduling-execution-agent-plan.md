# 基于 hiclaw 实现调度 Agent 与执行 Agent 的构造和执行 —— 设计与执行计划

> 依据：`docs/agentteams-hiclaw.md`（AgentTeams / hiclaw 框架概念、主流程、示例场景）
>
> 目标：在 hiclaw（AgentTeams）技术栈上，落地本项目的 **调度 Agent（SchedulingAgent）** 与 **执行 Agent（ExecutionAgent）** 的「构造」与「执行」，复用其 CRD 声明、Skills 体系、Matrix 通信、MinIO 状态持久化与 Higress 网关能力。
>
> 范围：设计与执行计划文档。不含代码实现（实现按第 8 节阶段推进）。

---

## 1. 背景与目标

### 1.1 现状

本项目（`src/`）已有一套在进程内运行的调度/执行 Agent：

- `core/agents/scheduling_agent.py` —— ReAct 模式，目标分解为子任务，构建 **DAG 依赖波（waves）**，逐波并行派发、串行收敛，失败传播跳过后继。
- `core/agents/execution_agent.py` —— 角色化 LLM 执行，`role` + `system_prompt` 驱动 Q&A。
- 配套：`central_dispatcher`（ack-after-execute）、`ws_server` + `execution_server`（解耦 WS 执行端）、`message_queue`、`watchdog`、`sandbox`、`event_bus`、`a2a_protocol`。

该实现运行在 **单进程 Python + SQLite** 上，调度与执行耦合在同一个后端，水平扩展与多运行时（Node/Python）受限。

### 1.2 目标

引入 hiclaw 后：

- **调度 Agent ↔ hiclaw Manager**：用 Manager 承载调度逻辑，复用其 `task-coordination` / `task-management` / `worker-management` Skills 与 CRD 生命周期。
- **执行 Agent ↔ hiclaw Worker**：每个执行角色对应一个 Worker 容器，按需创建、无状态、产物落 MinIO，支持 `openclaw` / `copaw` / `hermes` 多运行时。
- **构造** = CRD 声明 + `agt` CLI 物化 + Skills 推送 + MinIO 配置/SOUL。
- **执行** = Matrix Room 任务下发 → `shared/tasks/{id}/` 元数据 → Worker 执行写 `result.md` → Room 通知 → Manager 收敛状态；依赖波调度由 Manager 的 task-coordination Skill 承担。

### 1.3 非目标（YAGNI）

- 不重写已有 Python 调度算法的核心逻辑（waves/拓扑/失败传播），仅将其映射为 Manager 行为契约。
- 不引入 K8s 部署作为首阶段目标；首阶段用本地 embedded 模式验证。
- 不替换现有进程内实现，二者并存，hiclaw 作为「可扩展运行时」选项（见第 7.6 节回退策略）。

---

## 2. 概念映射

| 本项目概念 | hiclaw 对应 | 说明 |
|---|---|---|
| `SchedulingAgent` | **Manager**（或 Team Leader，多团队时） | 目标分解、依赖波调度、Worker 编排 |
| `ExecutionAgent` | **Worker** | 角色化任务执行，每角色一容器 |
| `agent_roles.execution_agents[]` | **Worker CRD** 列表 | 角色 = Worker，`role/system_prompt` 落 `SOUL.md` |
| `Task` / `Scenario` | `shared/tasks/{id}/` + Manager task-management | 任务元数据持久化在 MinIO |
| `depends_on` / waves | Manager `task-coordination` Skill 行为 | 依赖拓扑 + 上游结果注入 |
| `central_dispatcher`（ack-after-execute） | Manager heartbeat + Room 通知 | Manager 收敛 Worker 状态 |
| `execution_server`（WS 解耦端） | Worker 容器（按需创建） | 执行与调度物理隔离 |
| `message_queue` | Matrix Room + MinIO 文件 | 通信与状态载体 |
| `a2a_protocol` | Manager↔Worker 的 Matrix 消息 + shared 文件 | A2A 通过 Room 与共享前缀 |
| `sandbox` | Worker 容器本身（进程/容器级隔离） | 容器即沙箱 |
| LLM 调用 | Higress OpenAI 兼容路由 + key-auth | 统一出口、按身份鉴权 |
| MCP（github-operations 等） | Higress 托管的 MCP Server + `mcporter` | 凭据集中、动态授权 |

---

## 3. 总体设计

### 3.1 架构

```
                 ┌─────────────── Human (Element Web / Matrix 客户端) ──────────────┐
                 │                                                                  │
                 ▼                                                                  ▼
        ┌────────────────┐   Matrix(Tuwunel)   ┌──────────────────────────────────────┐
        │  Manager        │ ◄────────────────► │  Workers (执行 Agent，按角色)          │
        │  (调度 Agent)    │                    │  W_alice(openclaw) W_bob(copaw) ...   │
        │  - task-coord    │                    │  - role/SOUL  - skills  - result.md   │
        │  - wave 调度      │ ◄── Higress ──►   │  - mcporter(MCP)                       │
        └────────┬─────────┘   LLM/MCP 路由     └────────────────┬──────────────────────┘
                 │  REST 8090                                       │  mc sync
                 ▼                                                  ▼
        ┌────────────────┐                               ┌──────────────────┐
        │  Controller     │  reconciles CRD              │  MinIO           │
        │  (Go operator)  │  Worker/Manager/Team/Human    │  agents/<n>/…    │
        │  agt CLI        │                              │  shared/tasks/…  │
        └────────────────┘                               └──────────────────┘
```

### 3.2 角色定义

**调度 Agent（Manager）**

- 职责：目标分解 → DAG 波调度 → 派发到 Worker → 收集结果 → 失败传播 → heartbeat。
- 运行时：`openclaw`（Node，默认）或 `qwenpaw`（Python，若需复用现有 Python 调度代码片段）。
- 关键 Skills（来自 hiclaw 内置 16 个）：`task-coordination`、`task-management`、`worker-management`、`team-management`、`channel-management`、`file-sync-management`、`mcporter`、`mcp-server-management`、`project-management`。
- SOUL 注入：本项目调度契约（波调度、依赖注入、失败传播规则）作为 Manager `soul` 覆盖写入。

**执行 Agent（Worker）**

- 职责：接收任务 → 执行（LLM Q&A / 命令 / MCP） → 写 `result.md` → Room 通知完成。
- 运行时：按角色选 `openclaw`（默认）/ `copaw`（Python QwenPaw）/ `hermes`（Python Hermes）。
- 内置核心 Skills（hiclaw 物化）：`file-sync`、`mcporter`、`find-skills`、`project-participation`、`task-progress`。
- 按需 Skills：`github-operations`、`git-delegation`（由 Manager 在 `spec.skills` 引用时推送）。
- SOUL：`role` + `system_prompt` 写入 `agents/<name>/SOUL.md`。

---

## 4. 调度 Agent 的构造

### 4.1 Manager CRD（`agentteams.io/v1beta1`）

```yaml
apiVersion: agentteams.io/v1beta1
kind: Manager
metadata:
  name: scheduler
spec:
  model: qwen3.5-plus          # 经 Higress 路由
  runtime: openclaw             # 或 qwenpaw
  image: agentteams/manager:latest
  skills:                       # 内置 + 按需
    - task-coordination
    - task-management
    - worker-management
    - team-management
    - channel-management
    - file-sync-management
    - mcporter
    - mcp-server-management
    - project-management
  config:
    heartbeatInterval: 30s
    workerIdleTimeout: 600s
    notifyChannel: "#scheduler-notify"
  soul:                         # 调度契约覆盖
    # 波调度规则、依赖注入字段名、失败传播策略
  state: Running
  accessEntries: []             # Higress consumer 凭据范围
```

### 4.2 构造步骤

1. **声明 CRD**：`agt create manager -f manager-scheduler.yaml`（或 Helm values 的 `manager.bootstrapCR`）。
2. **Controller 调和**：`ManagerReconciler` 创建 Manager 容器，注入 `AGENTTEAMS_CONTROLLER_URL`。
3. **SOUL 写入**：调度契约（`_build_waves` / `_inject_upstream` / `_propagate_failure` 的等价语义）落到 `manager/SOUL.md`，作为 Manager task-coordination 的指令。
4. **Skills 物化**：内置 9 个 Skills 从镜像路径加载；`soul` 覆盖若需 QwenPaw 专属 prompt，放 `manager/agent/copaw-manager-agent/`。
5. **Higress consumer**：为 Manager 生成 LLM/MCP key-auth 凭据（operator 注入）。
6. **校验**：Manager 容器 Running、Matrix 上线、能 `agt get workers`。

### 4.3 调度契约（SOUL 关键内容）

将现有 `scheduling_agent.py` 的不变量转译为自然语言契约：

- 每个子任务带 `id` + `depends_on`（缺失则 wave 0）。
- 拓扑分层：无依赖 → wave 0；依赖全在更早波 → wave N。
- 波内并行派发，波间串行；后继任务上下文注入 `upstream_results` / `upstream_outputs`。
- 前驱失败 → 后继标记 `skipped`（failed），不基于缺失输入运行。
- 环检测：检测到环即整体失败，不死锁。

---

## 5. 执行 Agent 的构造

### 5.1 Worker CRD

```yaml
apiVersion: agentteams.io/v1beta1
kind: Worker
metadata:
  name: alice                    # 角色名
spec:
  model: qwen3.5-plus
  runtime: openclaw              # 角色适配：代码执行用 openclaw；Python 工具链用 copaw/hermes
  image: agentteams/worker-agent:latest
  skills:                        # 按需可分发
    - github-operations
  mcpServers: [github]           # 引用 Higress 托管的 MCP route
  state: Running
  accessEntries:
    - provider: github
      scope: repo:<org>/*        # 云凭据范围隔离
```

`SOUL.md`（`agents/alice/SOUL.md`）由 Manager 在创建时生成：

```markdown
# Alice
role: 前端开发
system_prompt: <对应 agent_roles.execution_agents[i].role / system_prompt>
```

### 5.2 构造步骤

1. **触发**：Human 在 Element Web 私聊 Manager：「创建名为 alice 的 Worker，负责前端开发」；或 `agt create worker --name alice --model qwen3.5-plus`。
2. **Manager 物化**：
   - 注册 Matrix 账户（Tuwunel）。
   - 创建 Higress consumer 凭据。
   - MinIO 生成 `agents/alice/`（含 `SOUL.md`、skills 物化）。
   - 创建共享 Room（Human + Manager + Alice 三方）。
3. **容器启动**：挂载 Docker socket 时自动 `docker run`；否则 Manager 返回手动启动命令（见 `docs/agentteams-hiclaw.md` 步骤 1）。
4. **核心 Skills 物化**：`file-sync`、`mcporter`、`find-skills`、`project-participation`、`task-progress` 进 workspace。
5. **按需 Skills 推送**：`spec.skills` 引用 `github-operations` → Manager 从 `manager/agent/worker-skills/` 推送。
6. **校验**：Room 3 成员、Higress 显示 `worker-alice` consumer、MinIO 有 `agents/alice/SOUL.md`、容器 Running。

---

## 6. 执行流程

### 6.1 端到端任务生命周期

```
Human ──(Matrix DM/Room)──► Manager
   1. Manager 分解目标 → 子任务 DAG（id + depends_on）
   2. 逐波派发：wave 内并行，每个子任务
      ├── shared/tasks/{tid}/meta.json  + spec.md  (MinIO)
      └── Room @alice: "执行 <goal>"
   3. Worker(alice)
      ├── 拉取 spec（mc sync）
      ├── 执行（LLM / 命令 / mcporter→MCP）
      ├── 写 shared/tasks/{tid}/result.md
      └── Room 通知: "完成 <tid>"
   4. Manager 收敛：标记 completed；注入 upstream_results 给后继波
   5. heartbeat：巡视 Room 活动 + 状态询问
```

### 6.2 依赖波调度映射

| 现有 `scheduling_agent.py` | hiclaw Manager 行为 |
|---|---|
| `_decompose_goal` | Manager task-management 分解，LLM 经 Higress |
| `_normalize_subtasks` | 契约要求 LLM 输出 `id`+`depends_on`；缺失 → wave 0 |
| `_build_waves` | task-coordination Skill 拓扑分层 |
| `mqs.dispatch_subtasks`（波内并行） | 并行 @ 多个 Worker Room |
| `_inject_upstream` | 后继子任务 `spec.md` 内嵌 `upstream_results` |
| `mqs.collect_replies` | Manager 监听各 Room 完成通知 |
| `_propagate_failure` | 前驱失败 → 后继 spec 标 `skipped`，不派发 |
| watchdog 超时 | Manager `workerIdleTimeout` + heartbeat |

### 6.3 中途干预与动态权限

- **中途干预**（对应现有「步骤 3」）：Human 在 Worker Room 追加要求 → Manager 将新要求并入该任务 `spec.md` → Worker 重新同步执行。
- **动态 MCP 权限**（对应现有「步骤 8」）：Human 经 Manager 撤销 alice 的 github MCP → Higress route consumer 解绑 → alice 下次调用 403；恢复则反向操作。凭据始终由 MCP Server 集中持有，Worker 不接触 PAT。

---

## 7. 关键设计决策

### 7.1 状态持久化：MinIO 而非本地

Worker 无状态，所有配置/产物在 `agents/<name>/` 与 `shared/tasks/<id>/`。崩溃后 Worker 容器可重建并 `mc sync` 恢复，对应现有 `recover_orphans_on_startup` 的语义升级（从「标记 failed」到「可恢复续跑」）。

### 7.2 通信：Matrix Room 作为单一时间线

Room 同时承载分配、进度、干预、完成通知 —— 即现有 `event_bus` + `message_queue` 的可观测面外移到 Matrix，获得 human-in-the-loop 可见性，无需自建事件日志 UI。

### 7.3 LLM/MCP 出口统一：Higress

所有 LLM 走 OpenAI 兼容路由 + key-auth，按 Manager/Worker 身份隔离；MCP 走 Higress 托管。对应现有 `llm_client`（DashScope 包装）的外移，且天然支持多 provider 切换（`model-switch` Skill）。

### 7.4 多运行时：按角色选 Worker runtime

- 代码/命令执行角色 → `openclaw`（Node + OpenClaw 网关 + mcporter）。
- Python 工具链角色 → `copaw` 或 `hermes`。
- 现有 `execution_agent.py` 的 `role/system_prompt` 逻辑等价于 `SOUL.md`，与运行时无关。

### 7.5 安全边界

- Higress key-auth 按 consumer 范围隔离 LLM/存储/MCP。
- 云凭据经 credential-provider sidecar 做 STS 范围隔离（`accessEntries`）。
- Worker 永不持有原始凭据（PAT 等）。

### 7.6 与现有实现的并存与回退

- 首阶段不替换 `src/` 进程内实现；hiclaw 作为独立运行时验证。
- 若 hiclaw 链路不稳定，现有 `central_dispatcher` + `execution_server` WS 链路继续承担生产流量。
- 共用同一套 `agent_roles` / `Scenario` 配置语义，仅 `runtime` 字段区分 `local` vs `hiclaw`。

---

## 8. 执行计划（分阶段）

### Phase 0 —— 环境准备（本地 embedded）

- [ ] Docker 就绪；准备 LLM API Key（Qwen/百炼 或 OpenAI 兼容）。
- [ ] 运行 `make install`（或一键脚本）启 `agentteams-controller` + `agentteams-manager`。
- [ ] 校验：Element Web（`http://127.0.0.1:18088`）、Higress Console（18001）、MinIO 健康。
- [ ] `agt get managers` / `agt get workers` 通。

### Phase 1 —— 调度 Agent（Manager）构造

- [ ] 编写 `manager-scheduler.yaml`（含 9 个 Skills + soul 调度契约）。
- [ ] `agt create manager -f`；调和成功，Manager 上线 Matrix。
- [ ] 将现有 `_build_waves`/`_inject_upstream`/`_propagate_failure` 语义写入 SOUL 并验证 Manager 能复述/执行该契约。
- [ ] 验收：单目标分解为 ≥2 子任务，DAG 正确。

### Phase 2 —— 执行 Agent（Worker）构造

- [ ] 经 Manager 创建 Worker `alice`（openclaw）。
- [ ] 校验 Room 三方、Higress consumer、MinIO `agents/alice/SOUL.md`、容器 Running。
- [ ] 验证核心 Skills 物化（file-sync / mcporter / task-progress）。
- [ ] 创建第二个 Worker `bob`（copaw 或 hermes），验证多运行时并存。

### Phase 3 —— 执行流程联调

- [ ] 在 alice Room 分配 hello-world README 任务，走完 `spec.md → result.md → completed`。
- [ ] 验证 `shared/tasks/{id}/` 元数据落盘。
- [ ] 中途干预：追加要求，验证并入结果。
- [ ] heartbeat：长任务，验证 Manager 巡视与状态询问可见。

### Phase 4 —— 依赖波调度联调

- [ ] 分配需协作的任务（alice 前端 HTML + bob 后端 API），验证 Manager 拆分 + 波调度。
- [ ] 验证上游结果注入后继子任务 spec。
- [ ] 注入一个失败前驱，验证后继被 `skipped` 而非空跑。
- [ ] 验证环检测（构造循环依赖，整体失败不死锁）。

### Phase 5 —— MCP 与动态权限

- [ ] 配置 GitHub PAT → Higress 托管 GitHub MCP Server。
- [ ] alice 经 mcporter 完成：读 README → 建分支 → 加文件 → 开 PR。
- [ ] alice + bob 联合 GitHub 任务，各开 PR。
- [ ] 动态撤销/恢复 alice github 权限，验证 403 ↔ 成功。

### Phase 6 —— 对接与回退

- [ ] 在 `agent_roles` 配置增加 `runtime: hiclaw` 选项，路由到 hiclaw Manager。
- [ ] 现有 `local` 链路保持可用，灰度切换。
- [ ] 文档化 hiclaw 链路的运维/排障入口（`agt`、Higress Console、MinIO、Element Web）。

---

## 9. 验证方案

每个 Phase 的「验收」即验收标准。总体回归对照现有 `tests/`：

- **调度正确性**：DAG 波序、上游注入、失败传播、环检测 —— 用现有 `scheduling_agent` 测试用例的输入/期望输出，在 hiclaw Manager 上重放比对。
- **执行正确性**：`role/system_prompt` 在 Worker SOUL 下产出等价结果。
- **可靠性**：Worker 容器杀掉后重建，`mc sync` 恢复 workspace，任务可续跑（对标现有 ack-after-execute + watchdog）。
- **安全**：Worker 内无明文 PAT；撤销权限后 403。
- **可观测**：Room 时间线覆盖分配/进度/干预/完成。

> Would a staff engineer approve this? —— 映射清晰、YAGNI 边界明确、可回退、每阶段可独立验收。

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| Manager SOUL 契约 LLM 不稳定遵守 | 契约尽量结构化（JSON schema 输出）；缺失字段回退 wave 0 |
| 多运行时 Skills 行为不一致 | 首阶段只用 openclaw；copaw/hermes 在 Phase 2 验证后再用 |
| Matrix/MinIO 链路延迟影响波收敛 | heartbeat 间隔可调；watchdog 超时兜底标记 failed |
| 本地 embedded 资源占用 | 首阶段验证用，生产走 K8s（后续阶段） |
| 与现有 local 实现语义漂移 | 共用 `agent_roles`/`Scenario` 配置，仅 runtime 字段区分；回归用同一套期望 |

---

## 11. 参考索引

- 概念与流程：`docs/agentteams-hiclaw.md` 第 1、2 节
- 示例场景：`docs/agentteams-hiclaw.md` 第 3 节（Quickstart 9 步）
- 现有实现：`src/core/agents/scheduling_agent.py`、`src/core/agents/execution_agent.py`
- 配套机制：`src/core/central_dispatcher.py`、`src/core/ws_server.py`、`src/execution_server/`
- 上游文档：`docs/` 下 `architecture.md`、`quickstart.md`、`k8s-native-agent-orch.md`、`declarative-resource-management.md`
