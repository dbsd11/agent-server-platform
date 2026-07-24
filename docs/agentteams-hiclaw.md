# AgentTeams (hiclaw) 框架文档

> 来源：https://github.com/agentscope-ai/AgentTeams/tree/main/docs
>
> 命名说明：仓库官方名称为 **AgentTeams**（agentscope-ai）。docs 树中并无名为 "hiclaw" 的框架；当前本地 git 分支为 `hiclaw-agent`，故 "hiclaw" 应为本地项目对该技术栈的内部命名。本文档汇总该 docs 目录中与「概念 / 主流程 / 示例场景」相关的内容。

---

## 1. 概念（核心架构）

AgentTeams 是一个 **Agent 协调平台**：**Manager** 编排 **Worker**（以及可选的、由 **Team Leader** 带领的 **Team**），**Human** 通过 **Matrix** 参与。

### 四个逻辑分层

1. **Human 层** —— 浏览器 / Matrix 客户端。
2. **基础设施层** —— Higress（AI + API 网关）、Tuwunel（Matrix homeserver，conduwuit 系）、MinIO（对象存储）、Element Web（UI）。
3. **Controller 层** —— Go operator，负责对 CRD 做调和（reconcile），REST API 端口 8090。
4. **Agent 容器层** —— Manager Agent、Worker 容器、可选 Team Leader。

### 关键组件

- **Controller（`agentteams-controller`）** —— 调和 Worker/Manager/Team/Human CRD。本地模式以「embedded」镜像交付，内置 Higress+Tuwunel+MinIO+Element Web；K8s 模式下作为独立 Deployment 运行。
- **Manager** —— 协调 Agent，处理任务、Worker、Team、Human，以及通过 Matrix + Controller API 管理 Higress 路由 / MCP。运行时：OpenClaw（Node，默认）或 QwenPaw（Python）。
- **Worker** —— 任务执行器，每 Worker 一个容器，按需创建。**无状态** —— 配置与产物持久化在对象存储。运行时：`openclaw`（默认）、`copaw`、`hermes`。

### 四个 CRD（`agentteams.io/v1beta1`）

- **Worker** —— model、runtime、image、skills、MCP、state（Running/Sleeping/Stopped）、accessEntries。
- **Manager** —— model、runtime、soul/agent 覆盖、skills、config（heartbeat 间隔、idle 超时、notify channel）。
- **Team** —— Leader + Workers、admin、peerMentions、team channelPolicy。
- **Human** —— 显示名、email、permissionLevel、可访问的 teams/workers。

### 通信机制

- **Matrix（Tuwunel）** —— Human/Manager/Worker 通信走 Matrix client-server API；Room 提供「human-in-the-loop」可见性（分配、进度、干预共享同一时间线）。Tuwunel 属 conduwuit 家族，通过 `CONDUWUIT_` 环境变量配置。
- **MinIO（S3/OSS 兼容）** —— 共享存储：`agents/<name>/…`、`shared/tasks/…`、`manager/…`、team 前缀。Manager 与 Worker 用 `mc` 客户端做镜像 / 推送。因持久状态在 bucket 而非本地，Worker 可被替换。
- **Higress（AI Gateway + API Gateway）** —— LLM 流量走 OpenAI 兼容路由，按身份做 consumer key 鉴权；MCP server 与可选的 Worker 端口 HTTP/gRPC 暴露建模为网关路由，在调和期间管理。

### 运行时模型

- **Worker 运行时**
  - `openclaw`（默认）：openclaw-base 衍生镜像内的 Node/OpenClaw 网关，经 mcporter 通过 Higress 调用 MCP。
  - `copaw`：Python/QwenPaw，替代 agent loop，Matrix 通信走 QwenPaw channels；skills 在 `copaw-worker-agent/`。
  - `hermes`：Python Hermes，Matrix worker 运行时，policy/config 树在 `hermes-worker-agent/`。
- **Manager 运行时**：内置 entrypoint 在 OpenClaw（默认）与 QwenPaw 间选择；hermes 仅作 Worker 运行时。

### Skills 系统

Skills = 面向 Agent 的 Markdown（`SKILL.md`）+ 可选 `scripts/` / `references/`，从 workspace 或镜像路径加载。

- **Manager Skills（共 16 个）**：channel-management、file-sync-management、git-delegation-management、agentteams-find-worker、human-management、matrix-server-management、mcporter、mcp-server-management、model-switch、project-management、service-publishing、task-coordination、task-management、team-management、worker-management、worker-model-switch。OpenClaw 与 QwenPaw Manager 共享，QwenPaw 专属 prompt 覆盖在 `manager/agent/copaw-manager-agent/`。
- **Worker Skills**：按运行时模板化（`manager/agent/worker-agent/`、`copaw-worker-agent/`、`hermes-worker-agent/`），内置核心集（file-sync、mcporter、find-skills、project-participation、task-progress）在每个 worker workspace 物化。按需可分发 skills 在 `manager/agent/worker-skills/`（如 github-operations、git-delegation），Manager 在 `spec.skills` 引用时推送给 worker。
- **Team Leader Skills**：`manager/agent/team-leader-agent/skills/` 下：communication、file-sharing、mcporter、organization、project-management、task-management、team-coordination。

### 安全

Higress consumers 使用 key-auth（Bearer）按 Manager/Worker 身份对 LLM、存储、MCP 路由做范围隔离。Secret（网关 key、密码）由 operator/installer 生成或注入。云部署可用 credential-provider sidecar 做 STS 范围隔离的对象存储与网关 API。

### 部署模式

- **本地单机（`install/`）**：安装脚本拉取 embedded controller 镜像（含 Higress all-in-one、Tuwunel、MinIO、mc、Element Web、controller 二进制、`agt` CLI、supervisord 接线）。先启 controller，等待 Higress/Tuwunel/MinIO 内部健康检查，再由 ManagerReconciler 创建 Manager（以及添加 Worker CR 或 CLI 时创建 Worker）。同机时主机端口（如网关 18080）映射进 controller 容器。
- **Kubernetes（`helm/agentteams`）**：每个主组件独立 Pod / chart 依赖：Higress subchart、Tuwunel StatefulSet、MinIO、Element Web、controller Deployment，加上由 CR 创建的 Manager/Worker Pod。Manager 以 `AGENTTEAMS_RUNTIME=k8s` 运行，经 `mc` 从集群 MinIO 同步 workspace，消费 operator 注入的凭据。

---

## 2. 主流程

### 启动序列（本地模式）

1. 安装脚本拉取 embedded controller 镜像 → 启动 `agentteams-controller`（Higress+Tuwunel+MinIO+Element Web+Go controller+`agt` CLI）。
2. 等待内部健康检查（Higress/Tuwunel/MinIO）。
3. `ManagerReconciler` 创建 **Manager** 容器（以及添加 Worker CR 或使用 `agt` 时创建 Worker）。
4. Manager 接收 `AGENTTEAMS_CONTROLLER_URL` + 可选 Docker socket，用于 Worker 生命周期管理。

### 任务生命周期

1. Human 请求创建 Worker（经 Matrix 私聊 `manager`，或 `agt create worker`）。
2. Manager 注册 Matrix 账户 → 创建 Higress 凭据 → 在 MinIO 生成配置 → 创建共享 Room → 启动 Worker 容器。
3. Human 在 Worker 的 Room 中分配任务 → Manager 转发 → 元数据（`meta.json`、`spec.md`）落地到 `shared/tasks/{task-id}/`。
4. Worker 执行 → 写 `result.md` → 在 Room 通知完成 → Manager 将任务标记为 `completed`。
5. Manager 周期性运行 **heartbeat** 检查（Room 活动 + 状态询问）。

### 声明式 CLI（`agt`）

```bash
docker exec agentteams-controller agt create worker --name alice --model qwen3.5-plus
docker exec agentteams-controller agt get workers
```

---

## 3. 示例场景（Quickstart 演练）

前置条件：Docker 运行中、LLM API Key（默认 Qwen/百炼，也支持任意 OpenAI 兼容 provider，手动配 `/v1` Base URL）、可选 GitHub PAT。

安装：

```bash
bash <(curl -sSL https://raw.githubusercontent.com/agentscope-ai/AgentTeams/main/install/agentteams-install.sh)
# 或仓库内
AGENTTEAMS_LLM_API_KEY="sk-xxx" make install
```

embedded 安装（v1.1.0+）启动两个容器：`agentteams-controller`（内置 Higress、Tuwunel、MinIO、Element Web、Go controller，REST API 端口 8090）与 `agentteams-manager`。Element Web 地址 `http://127.0.0.1:18088`，Higress Console 端口 18001。

**步骤 1：创建 Worker Alice**

在 Element Web 私聊 `manager`，请求「请创建一个名为 alice 的新 Worker，负责前端开发任务。」（或 CLI：`make replay TASK="..."`）。Manager 注册 Matrix 账户、创建 Higress 凭据、在 MinIO 生成配置、创建共享 Room 并启动 Worker。若挂载了 Docker socket 则容器自动启动，否则 Manager 提供手动 `docker run` 命令：

```bash
docker run -d --name agentteams-worker-alice \
  -e AGENTTEAMS_WORKER_NAME=alice \
  -e AGENTTEAMS_FS_ENDPOINT=http://<MANAGER_HOST>:9000 \
  -e AGENTTEAMS_FS_ACCESS_KEY=<ACCESS_KEY> \
  -e AGENTTEAMS_FS_SECRET_KEY=<SECRET_KEY> \
  agentteams/worker-agent:latest
```

校验：Alice 的 Room 出现 3 名成员、Higress 显示 `worker-alice` consumer、MinIO 有 `agents/alice/SOUL.md`、worker 容器运行中。

**步骤 2：给 Alice 分配任务**

在 Alice 的 Room 发送请求，为 hello-world 项目创建 README.md。Manager 接收并转发任务；MinIO 的 `shared/tasks/{task-id}/` 下出现 `meta.json`、`spec.md`。Alice 工作、写 `result.md`、在 Room 通知完成，Manager 将任务状态更新为 `completed`。

**步骤 3：任务中途人工干预**

让 Alice 写 Python「Hello, World!」脚本；工作进行中追加一条要求：脚本需接受命令行 name 参数。Alice 与 Manager 均将新要求纳入最终结果。

**步骤 4：观察 Heartbeat**

分配较长任务。Manager 周期性运行 heartbeat 检查，巡视每个 Worker 的 Room 近期活动并询问进度更新。询问与 Alice 的进度回复在 Room 中可见。

**步骤 5：创建 Worker Bob 并协作**

让 Manager 创建第二个 Worker Bob 负责后端。再请求协作任务：Alice 建前端 HTML，Bob 建后端 API，通过 MinIO 共享文件协调。Manager 拆分任务，双方在各自 Room 汇报进度，MinIO 中有共享协调文件。

**步骤 6：经 MCP 做 GitHub 操作**

需在安装时配置 GitHub PAT。给 Alice 分配 GitHub 任务：读仓库 README、建分支、加文件、开 PR。Alice 经 `mcporter` 调用 Higress 托管的 GitHub MCP Server。PAT 由 MCP Server 集中持有，Alice 永不直接接触。

**步骤 7：多 Worker GitHub 协作**

给 Alice 和 Bob 分配联合 GitHub 任务：Alice 建 `feature/alice-docs` 分支加 `docs/alice.md`，Bob 建 `feature/bob-api` 分支加 `src/bob.py`，各开一个 PR。双方在 Room 汇报完成。

**步骤 8：动态 MCP 权限控制**

经 Manager 消息撤销 Alice 的 GitHub MCP 访问。Alice 尝试 GitHub 操作时收到 403 错误。再次经 Manager 消息恢复访问后，Alice 的 GitHub 操作成功。

**卸载**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/agentscope-ai/AgentTeams/main/install/agentteams-install.sh) uninstall
```

移除 manager、所有 worker 容器、controller（Higress/Tuwunel/MinIO/Element Web）、可选 Docker 代理、数据卷、env 文件、workspace 目录、网络与安装日志。

---

## 参考文件索引

docs 目录下与本文相关的主要文件：

- 概念：`architecture.md`、`k8s-native-agent-orch.md`、`declarative-resource-management.md`
- 主流程：`quickstart.md`、`manager-guide.md`、`worker-guide.md`、`import-worker.md`、`development.md`
- 示例场景：`cms-integration.md`、`dingtalk-setup-guide.md`、`windows-deploy.md`
- 参考：`faq.md`、`faq-legacy.md`

中文版位于 `docs/zh-cn/`（同名文件）。
