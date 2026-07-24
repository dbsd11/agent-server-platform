# Agent Server Platform - Task List

## Phase 1: Core Infrastructure (Week 1)

### ✅ Completed

- [x] Set up project structure (copy ai-app-template base)
- [x] Implement database models (Agent, Task, Scenario, Event)
- [x] Implement repositories (AgentRepository, TaskRepository, ScenarioRepository, EventRepository)
- [x] Implement state machine engine (enum-based)
- [x] Implement event bus (queue-based)
- [x] Implement watchdog (timeout detection)

### 🔄 In Progress

- [ ] Implement Gradio pages (home, agent_registry, task_monitor, event_log)
- [ ] Implement Flask API (tasks, scenarios, events)

### ⏳ Pending

- [ ] Test: Create agent, submit task, view events in UI and API

## Phase 2: Agent System (Week 2)

- [ ] Implement BaseAgent interface
- [ ] Implement SchedulingAgent (ReAct pattern)
- [ ] Implement ExecutionAgent (sandbox execution)
- [ ] Implement Sandbox (subprocess-based)
- [ ] Implement AgentManager
- [ ] Implement A2A protocol (queue-based)

## Phase 3: Scenario System (Week 3)

- [ ] Implement BaseScenario interface
- [ ] Implement ScenarioManager
- [ ] Implement example scenarios (simple_qa, code_execution)
- [ ] Implement scenario Gradio page
- [ ] Implement scenario Flask API

## Phase 4: Testing + Documentation (Week 4)

- [ ] Write unit tests (state machine, event bus, watchdog)
- [ ] Write integration tests (agent/scenario lifecycle)
- [ ] Complete knowledge/README.md
- [ ] Fix bugs
- [ ] Performance optimization

## Review

### What Worked

- Reusing ai-app-template patterns (BaseModel, BaseRepository) saved significant time
- Enum-based state machine is simple and effective
- Queue-based event bus is easy to understand and test

### Lessons Learned

- Always create __init__.py files for packages
- Database models need proper datetime handling
- Event bus needs wildcard subscription support

### Next Steps

- Complete Gradio UI pages
- Implement Flask REST API
- Create knowledge/README.md
- Test Phase 1 integration

## Phase 2: Agent System (Week 2) - ✅ COMPLETE

### ✅ Completed

- [x] Implement BaseAgent interface and AgentRun
- [x] Implement Sandbox system (subprocess-based)
- [x] Implement ExecutionAgent (sandbox execution)
- [x] Implement SchedulingAgent (ReAct pattern, goal decomposition)
- [x] Implement AgentManager (registry, lifecycle, task submission)
- [x] Implement A2A Protocol (queue-based messaging)
- [x] Create Phase 2 integration test
- [x] All tests passed! ✅

### Review

**What Worked**:
- Sandbox isolation works perfectly for Python scripts
- ExecutionAgent properly wraps sandbox with error handling
- SchedulingAgent successfully decomposes goals into subtasks
- AgentManager handles async execution correctly
- A2A Protocol enables inter-agent communication

**Issues Fixed**:
- Fixed import error: GlobalLoopUtil → get_random_work_loop
- Fixed missing Task model import in agent_manager.py
- Fixed context passing: now properly passes original context to agents

**Test Results**:
- ✅ Sandbox execution
- ✅ ExecutionAgent lifecycle
- ✅ SchedulingAgent goal decomposition
- ✅ AgentRun state management
- ✅ A2A message send/receive
- ✅ AgentManager task submission
- ✅ Full integration test (task execution with script)

**Next Steps**: Phase 3 - Scenario System

## Phase 3: Scenario System (Week 3) - ✅ COMPLETE

### ✅ Completed

- [x] Implement BaseScenario interface and ScenarioContext
- [x] Implement ScenarioManager (lifecycle management, state transitions)
- [x] Implement SimpleQAScenario (question-answering workflow)
- [x] Implement CodeExecutionScenario (sandbox code execution)
- [x] Update Scenario API endpoints to use ScenarioManager
- [x] Create Phase 3 integration test
- [x] All tests passed! ✅

### Review

**What Worked**:
- BaseScenario interface is clean and extensible
- ScenarioManager handles lifecycle correctly (create → start → complete/failed)
- SimpleQAScenario successfully integrates with agent system
- CodeExecutionScenario successfully executes code in sandbox
- Scenario API endpoints work with new ScenarioManager

**Issues Fixed**:
- Updated scenario API to use ScenarioManager instead of direct database operations
- Added proper scenario instance creation based on type
- Integrated scenario system with existing agent system

**Test Results**:
- ✅ BaseScenario interface
- ✅ ScenarioContext management
- ✅ ScenarioManager create/start/stop lifecycle
- ✅ SimpleQAScenario execution
- ✅ CodeExecutionScenario with sandbox
- ✅ Full scenario lifecycle
- ✅ Scenario API integration

**Next Steps**: Phase 4 - Testing and Documentation

## Phase 4: Testing and Documentation (Week 4) - ✅ COMPLETE

### ✅ Completed

- [x] Write unit tests for core components (state machine, event bus, watchdog, sandbox, agents)
- [x] Write integration tests (scenario lifecycle, agent-task integration, event flow)
- [x] Update knowledge base with Phase 2 and 3 patterns
- [x] Create comprehensive API documentation
- [x] Create user guide with quick start and examples

### Review

**What Worked**:
- Comprehensive unit tests cover all core components
- Integration tests verify end-to-end workflows
- Knowledge base provides clear patterns and best practices
- API documentation is complete with examples
- User guide covers common workflows and troubleshooting

**Test Coverage**:
- Unit tests: state machine, event bus, watchdog, sandbox, agents
- Integration tests: scenario lifecycle, agent-task interaction, event flow
- All tests passing ✅

**Documentation**:
- knowledge/README.md: Updated with Phase 2 and 3 patterns
- docs/API.md: Complete API documentation with examples
- docs/USER_GUIDE.md: Comprehensive user guide

**Next Steps**: Production deployment (Phase 5)

## Phase 5: Spec Alignment (agent-server.txt) - ✅ COMPLETE

### ✅ Completed

- [x] Fix broken loop: scenarios now wait for task results (wait_for_task)
- [x] Real goal decomposition in SchedulingAgent (multi-subtask, heuristic-based)
- [x] Execution Agent planning step (_plan_execution)
- [x] Idempotency control (idempotency_key on Task model + SchedulingAgent)
- [x] Sub-topic tracking (topic_id groups subtasks from same decomposition)
- [x] Flow definition (FlowDefinition with topological sort)
- [x] Agent role definition (AgentRole class + define_roles() on BaseScenario)
- [x] Component definition (ScenarioComponent ABC + ScenarioOutput)
- [x] Componentized output (ScenarioOutput with named component results)
- [x] Enhanced event instrumentation (trace_id + metadata on events)
- [x] Sub-topic lifecycle tracking (topic.completed event)
- [x] Runtime release on scenario completion (_release_scenario_agents)
- [x] Database migration (ALTER TABLE for new columns, no data loss)
- [x] Spec alignment test (20/20 checks pass)
- [x] All existing integration tests still pass (Phase 1, 2, 3)

### Review

**What Worked**:
- Phased approach allowed incremental verification
- ALTER TABLE migration preserved existing dev data
- Ponytail mode kept each change minimal and focused
- trace_id propagation gives full observability across scenario lifecycle

**Issues Fixed**:
- Scenarios were fire-and-forget — now collect real task results
- SchedulingAgent trivial decomposition — now creates multiple subtasks
- No duplicate prevention — idempotency_key prevents duplicate subtask creation
- No flow/component concepts — now have FlowDefinition and ScenarioComponent
- No trace context — all events now carry trace_id for cross-event tracking

**Test Results**:
- ✅ Phase 1 integration test (core infra)
- ✅ Phase 2 integration test (agent system)
- ✅ Phase 3 integration test (scenario system)
- ✅ Spec alignment test (20/20 checks)

**Next Steps**: Production deployment — MySQL, Docker sandbox, Redis event bus, distributed A2A

---

## Debate Scenario — 观点论证推理 (config 341c3477-1adc-4f67-b896-525ee05f2191)

### Tasks
- [x] Create `src/scenarios/examples/debate_presets.py` (config_id → preset, seeded with 341c3477)
- [x] Create `src/scenarios/examples/debate_scenario.py` (正方/反方/裁判, N rounds, rebuttal)
- [x] Register `debate` scenario_type in `src/api/route/scenario/__init__.py`
- [x] Verify import + dispatch wiring

### Design
- Reuse `ExecutionAgent` via `agent_manager.submit_task(agent_type="execution", context={role, system_prompt})` — no new agent classes (DRY).
- 正方 + 反方 submitted back-to-back, both run async, `wait_for_task` each → effectively parallel.
- N rounds: each side sees the other's prior argument (rebuttal).
- 裁判 = third execution task synthesizing a verdict over both arguments.
- Preset resolved from `config_id`; explicit config fields override preset.

### Review
- 观点论证推理 (debate) 模式已落地，对应 config `341c3477-1adc-4f67-b896-525ee05f2191`。
- 正方/反方复用 ExecutionAgent (role + system_prompt)，无新增 agent 类。
- 支持多轮反驳 + 裁判综合裁决。
- 接线校验全通过 (preset 解析 / 初始化 / 覆盖 / 校验 / 输出抽取 / route import)。
- 未做端到端 LLM 运行：环境未配置 DASHSCOPE_API_KEY；配好 key 后即可 `POST /api/scenario` 创建 `debate` 类型并 start。

---

## hiclaw 适配层（调度/执行 Agent 构造与执行）

### ✅ Completed

基于 `docs/agentteams-hiclaw.md` 落地自包含 Python 适配层 `src/hiclaw/`，离线可验证（无 Docker、无 LLM Key）。

- [x] `object_store.py` — `ObjectStore` 抽象 + `LocalFileStore`（镜像 `agents/<name>/`、`shared/tasks/<id>/`、`manager/` 前缀，路径穿越防护），可替换为 MinIO/S3。
- [x] `crd.py` — `WorkerSpec`/`ManagerSpec`/`TeamSpec`/`HumanSpec` + `ResourceRegistry`（持久化到 `_crd/<kind>/<name>.json`，对应 controller 调和）。
- [x] `soul.py` — `Soul` + `SOUL.md` 渲染；Manager soul = 波调度契约（转译自 `scheduling_agent.py` 不变量）。
- [x] `skills.py` + `skills_builtin/` — `SKILL.md` frontmatter 解析 + 物化进 workspace（task-coordination/file-sync/task-progress/mcporter）。
- [x] `room.py` — in-process `Room`/`RoomService`（Matrix 抽象，单一时间线）。
- [x] `worker.py` — `HiclawWorker`（执行 Agent）：构造写 SOUL+skills+Room；执行写 spec.md/result.md/meta.json + Room 通知；LLM 缺失时确定性回退。
- [x] `scheduler.py` — 纯函数波调度（normalize/build_waves/inject_upstream/环检测），语义对齐现有实现，去 DB 耦合。
- [x] `manager.py` — `HiclawManager`（调度 Agent）：分解→波调度→派发→收集→上游注入→失败传播；LLM 缺失启发式回退。
- [x] `mcp.py` — `McpGateway`（Higress MCP 抽象 + 动态权限授予/撤销，撤销返回 403）。
- [x] `cli.py`/`__main__.py` — `agt` CLI：`create worker/manager`、`get workers/managers`、`run --goal`。

### 验证

- `tests/test_hiclaw.py` 24 项全通过（含 `test_demo_self_check` 端到端）。
- CLI 端到端冒烟通过：CRD 持久化 + SOUL/skills 物化 + 波调度 + `shared/tasks/*/spec.md|result.md|meta.json` 落盘。
- 全量非 websockets 测试 145 项通过；8 项 collection 错误为环境缺 `websockets` 包（既有，与本次无关）。

### 与现有实现关系

- 与 `src/core/agents/` 并存；现有为 `local` 运行时，本包为 `hiclaw` 运行时。
- 复用 `llm_client`（无 Key 自动回退）、`logger`；波调度逻辑抽为纯函数避免拖入 `TaskRepository`。
- 未触任何 `core/` 文件，零侵入。

### 执行 Agent 底层重建为 hiclaw 驱动（无 runtime 标记）

- [x] `HiclawWorker` 扩展沙箱执行：`_process` 分派 script/command → 复用 `core.sandbox.Sandbox`；返回 `returncode`/`stderr`。
- [x] `ExecutionAgent` 重建为 hiclaw facade：`initialize` 物化 per-agent `HiclawWorker`（role/system_prompt → SOUL，共享 sandbox）；`run` 委托 `worker.execute`，归一化为 legacy 契约；发 `task.execution_started/planned/completed/failed` 事件。
- [x] 调用方零改动：`task_runner` / `central_dispatcher` / `agent_manager` 仍用 `ExecutionAgent()` + `initialize({role, system_prompt})`，无 `runtime` 配置。

### 验证（全量）

- `TestExecutionAgent` 7 项原失败用例转绿（sandbox/returncode/events/_plan_execution 契约）。
- `test_integration.py::test_full_script_execution_flow` 转绿（补 `task.execution_planned` 事件）。
- `tests/test_hiclaw.py` 24 项全绿；`test_agents.py` 全绿。
- 全量 248 passed / 2 failed：2 项为 `test_ws_dispatcher` 的 `schedule_forward` 签名漂移（既有，与本次无关，stash 验证）。
- 2 项 collection error 亦为既有：`test_central_dispatcher` 缺 `GLOBAL_CONSUMER_ID` 符号、`test_execution_server_routing` 缺 `gradio` 包。

### 观点论证推理 (debate) 场景端到端验证

- [x] 新增 `tests/test_debate_scenario.py`：构造 debate 场景（正方/反方/裁判三执行 Agent），经 `scenario_manager` 创建+启动，轮询至终态。
- [x] 验证项：场景状态 completed；正方/反方论证 + 裁判裁决均非空；正反内容相异；多轮(rounds=2)正常运行。
- [x] 全程离线：无 DASHSCOPE_API_KEY，ExecutionAgent(hiclaw 底层) 走确定性回退产出论证内容。
- [x] 3 项全通过（单轮输出校验 + 多轮反驳 + 端到端完成）。未触任何源码，仅新增测试。
