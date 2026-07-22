# Agent Server Platform

A universal agent-server platform built on Gradio + Flask, implementing a two-tier architecture for multi-agent orchestration: scenario-based collaboration, task scheduling with **dependency-aware serial/parallel execution**, a **decoupled execution-agent server connected over WebSocket**, sandboxed execution, agent-to-agent (A2A) messaging, and fault-tolerant watchdog recovery.

## Features

### Two-Tier Architecture

- **Layer 1 — Universal Agent Platform**: SchedulingAgent (ReAct), ExecutionAgent, AgentManager, A2A protocol, message queue, event bus, watchdog, sandbox.
- **Layer 2 — Scenario Collaboration Platform**: scenario state machine, BaseScenario, flow definitions, ScenarioManager.

### Decoupled Execution-Agent Server (WebSocket)

Execution is split out of the backend into an **independently-deployable execution-agent server** that connects to the backend over WebSocket:

- The backend consumes the message queue and **forwards** task messages to the target exec-server's WS connection; the exec-server constructs an `ExecutionAgent` and runs the task.
- Each exec-server reports **status** (`offline` / `idle` / `running`, total quota + current running count), **environment info** (probes `bash, sh, python, claude, codex, qwen, curl, wget, ls, mkdir, cat, sed` + host `hostname` / `IP` / `OS`), and **task execution events** (`execution_agent_created`, `task_started`, `task_result`) over WS.
- In **scenario/mode creation**, each execution-agent role gets a dropdown to bind it to a specific exec-server. Tasks for that role route to the selected server; **if no server is selected, execution falls back to the backend (local)**, preserving the original behavior.
- Disconnect handling: an in-flight task is **deferred and re-dispatched on reconnect** (bounded by the watchdog timeout) — no silent loss.

### Dependency-Aware Scheduling (Serial / Parallel)

The SchedulingAgent decomposes a goal into subtasks that form a **DAG** (the LLM emits `id` + `depends_on` per subtask). Execution runs in **waves**:

- Tasks with no dependencies run in parallel (wave 0).
- A dependent task runs only after its predecessors complete, with their results **injected into its context** (`upstream_results` / `upstream_outputs`) so it can build on them.
- If a predecessor **fails**, its dependents are **skipped** (marked failed) rather than running on missing input.
- Cycle detection prevents deadlocks; unknown/self dependency refs are dropped. Tasks without `depends_on` fall back to the legacy all-parallel behavior.

### Core Components

| Component | Module | Description |
|-----------|--------|-------------|
| State Machine | `core/state_machine.py` | Enum-based, task + scenario state machines with transition tables |
| Event Bus | `core/event_bus.py` | Queue-based events with DB persistence, wildcard subscriptions |
| Watchdog | `core/watchdog.py` | Timeout detection and recovery |
| Sandbox | `core/sandbox.py` | Subprocess-isolated task execution (evolves to Docker/K8s) |
| A2A Protocol | `core/a2a_protocol.py` | Queue-based agent-to-agent messaging |
| Message Queue | `core/message_queue.py` | DB-backed transport with per-consumer offsets |
| Central Dispatcher | `core/central_dispatcher.py` | Global consumer routing dispatch rows to exec-servers (WS) or local |
| WS Server | `core/ws_server.py` | Backend WebSocket server + exec-server registry + reply correlation |
| WS Protocol | `core/ws_protocol.py` | Frame envelope for backend ↔ exec-server communication |
| LLM Client | `core/llm_client.py` | DashScope (OpenAI-compatible) wrapper; dependency-aware decomposition |
| Agents | `core/agents/` | `BaseAgent`, `SchedulingAgent` (ReAct + wave scheduler), `ExecutionAgent` |
| Agent Manager | `agents/agent_manager.py` | Agent lifecycle and registry |
| Execution Server | `execution_server/` | Standalone WS client package (env probe, task runner, heartbeat) |
| Scenarios | `scenarios/` | `BaseScenario`, `ScenarioManager`, flow definitions, components |

### User Interface

- **Gradio Web UI** (7 pages): Home, Scenario Dashboard, Task Monitor, Agent Registry, **Execution Servers**, Event Log, User.
- **Flask REST API**: Task, Scenario, Event endpoints with Swagger docs (`flask-restx`).

### Database

- **SQLite** for development (zero setup).
- **MySQL/PostgreSQL** for production — seamless migration via `BaseModel` / `BaseRepository`.
- Tables: `users, agents, tasks, scenarios, events, messages, consumer_offsets, execution_servers`.

## Quick Start

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env — set DASHSCOPE_API_KEY and DB/server config
```

### Running the Platform

The backend runs **three** processes: Gradio (UI), Flask (API), and the WebSocket server + CentralDispatcher (exec-server routing). `--all` launches all three.

```bash
# Start Gradio + Flask + WS server (recommended)
python src/app.py --all

# Or start individually
python src/app.py --gradio-only   # Gradio UI only
python src/app.py --flask-only    # Flask API only
python src/app.py --ws-only       # WS server + CentralDispatcher only

# Override ports / host
python src/app.py --all --gradio-port 8080 --flask-port 5000 --ws-port 8765 --host 0.0.0.0
```

### Starting an Execution-Agent Server

Run one or more execution-agent servers (on the same host or remote) that connect to the backend over WebSocket:

```bash
# From the project root — PYTHONPATH=src so `core.*` / `execution_server.*` resolve
PYTHONPATH=src \
  BACKEND_WS_URL=ws://127.0.0.1:8765 \
  SERVER_ID=node-1 \
  SERVER_NAME="Node 1" \
  MAX_QUOTA=4 \
  HEARTBEAT_INTERVAL=5 \
  python -m execution_server
```

The exec-server reuses the shared `.env` for LLM config (`DASHSCOPE_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, …). On connect it registers with the backend; it then appears in the **Execution Servers** UI page and in the per-role server dropdown when creating a scenario.

### Access

- **Gradio UI**: http://localhost:8080
- **Flask API**: http://localhost:5000
- **Swagger Docs**: http://localhost:5000/docs
- **WS server**: `ws://localhost:8765` (exec-servers connect here)

## Testing

Full test suite lives in `tests/`:

```bash
pytest -v
# or the WebSocket / scheduling feature tests in isolation:
pytest tests/test_central_dispatcher.py tests/test_ws_dispatcher.py \
       tests/test_execution_server_package.py tests/test_scheduling_dependencies.py \
       tests/test_routing_integration.py tests/test_execution_server_routing.py -v
```

Covers: state machine, event bus, watchdog, sandbox, A2A protocol, message queue, agents, scenario components, flow definitions, the WS exec-server path (protocol, dispatcher, routing, env probe), dependency/wave scheduling, and end-to-end integration tests.

## Project Structure

```
agent-server-platform/
├── src/
│   ├── app.py                      # Main entry point (CLI + multiprocessing launch)
│   ├── core/                       # Agent platform core
│   │   ├── state_machine.py        # Enum-based state machine engine
│   │   ├── event_bus.py            # Queue-based event system (DB-persisted)
│   │   ├── watchdog.py             # Timeout detection + recovery
│   │   ├── sandbox.py              # Subprocess execution isolation
│   │   ├── a2a_protocol.py         # Agent-to-agent messaging
│   │   ├── message_queue.py        # DB-backed transport w/ consumer offsets
│   │   ├── central_dispatcher.py   # Global consumer → WS forward or local exec
│   │   ├── ws_server.py            # Backend WS server + exec-server registry
│   │   ├── ws_protocol.py          # WS frame envelope + (de)serialize
│   │   ├── llm_client.py           # DashScope client; DAG decomposition
│   │   └── agents/                 # BaseAgent, SchedulingAgent (wave scheduler), ExecutionAgent
│   ├── execution_server/           # Standalone exec-agent server (WS client)
│   │   ├── env_probe.py            # CLI tool + host (hostname/IP/OS) probe
│   │   ├── ws_client.py            # Connect/register/heartbeat/reconnect
│   │   ├── task_runner.py          # Build ExecutionAgent per task, emit events
│   │   └── server.py / __main__.py # Entry: python -m execution_server
│   ├── agents/                     # AgentManager
│   ├── scenarios/                  # BaseScenario, ScenarioManager, flow defs, components
│   ├── api/                        # Flask REST API (flask-restx)
│   │   └── route/                  # task / scenario / event namespaces
│   ├── pages/                      # Gradio UI pages (7)
│   ├── route/router.py             # Gradio multi-page router
│   ├── database/                   # Connection manager, models, repositories
│   ├── auth/                       # Authentication
│   └── logger/                     # Logging
├── tests/                          # Unit + integration tests
├── docs/                           # API.md, USER_GUIDE.md
├── knowledge/                      # Architecture decisions & patterns
├── tasks/                          # todo.md, lessons.md
├── requirements.txt
├── .env.example
└── CLAUDE.md                       # AI context
```

## API Examples

### Create Task

```bash
curl -X POST http://localhost:5000/api/task \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Process data analysis",
    "priority": 1,
    "timeout_seconds": 3600
  }'
```

### Get Task Status

```bash
curl http://localhost:5000/api/task/<task_id>
```

### Create + Start a Scenario (role bound to an exec-server)

```bash
# Create
SID=$(curl -s -X POST http://localhost:5000/api/scenario \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_type": "simple_qa",
    "name": "Demo",
    "description": "route to node-1",
    "config": {
      "agent_roles": {
        "scheduling_agent": {"role": "你是任务调度专家"},
        "execution_agents": [{"name": "代码执行专家", "role": "你是代码执行专家", "server_id": "node-1"}]
      },
      "question": "编写Python代码计算1+1并执行，给出结果",
      "timeout": 120
    }
  }' | python -c "import sys,json;print(json.load(sys.stdin)['scenario_id'])")

# Start (LLM decomposes into a dependency DAG; waves run serially on node-1)
curl -X POST http://localhost:5000/api/scenario/$SID
```

### List Events

```bash
curl http://localhost:5000/api/event?limit=10
```

See `docs/API.md` for the full endpoint reference and `docs/USER_GUIDE.md` for workflows.

## Architecture

### State Machines

**Task State Machine**:
- `PENDING → RUNNING → SUCCESS / FAILED / TIMEOUT / CANCELLED`
- `PENDING → CANCELLED`
- `RUNNING → WAITING → RUNNING` (retry loop)
- Tasks carry `depends_on` (JSON array of predecessor task_ids) for DAG execution.

**Scenario State Machine**:
- `INITIALIZING → RUNNING → COMPLETED / FAILED / CANCELLED`
- `CANCELLED → RUNNING` (a stopped scenario can be re-started from its config).

### Execution Flow

```
ScenarioManager ─► SchedulingAgent.run ─► _decompose_goal (LLM, DAG w/ id+depends_on)
        │                                          │
        │                          waves = _build_waves (topological)
        │                                          │
        │   per wave: mqs.dispatch_subtasks (writes dispatch rows, context.server_id + upstream_results)
        │                                   │
        │                          messages table (dispatch rows)
        │                                   │
        │   ┌───────────────────────────────┴───────────────────────────────┐
        │   │ WS-server process: CentralDispatcher (global consumer)         │
        │   │   server_id set + connected → WSDispatcher.forward_task ──WS──► exec-server
        │   │   server_id set + offline    → defer, drain on reconnect       │
        │   │   no server_id               → run_task_locally (legacy path)  │
        │   └───────────────────────────────┬───────────────────────────────┘
        │                                   │
        │   reply rows (same shape) → mqs.collect_replies (per wave) → next wave
```

### Event Types

- **Task**: `task.created`, `task.state_changed`, `task.execution_started/completed/failed`, `task.scheduled`, `task.skipped`, `task.execution_agent_created`
- **Scenario**: `scenario.created`, `scenario.state_changed`, `scenario.completed/failed`
- **Agent**: `agent.registered`, `agent.started`, `agent.stopped`
- **Watchdog**: `watchdog.timeout_detected`, `watchdog.recovery_attempted`

## Evolution Path

### Phase 1 — Core Infrastructure ✅

- SQLite database layer (`BaseModel`, `BaseRepository`, repositories for User/Agent/Task/Scenario/Event)
- Queue-based event bus with DB persistence
- Enum-based state machines (task + scenario)
- Watchdog timeout detection
- Gradio UI + Flask REST API

### Phase 2 — Agent System ✅

- `BaseAgent` interface
- `SchedulingAgent` (ReAct pattern, LLM-driven)
- `ExecutionAgent` (sandbox execution)
- `Sandbox` (subprocess-based)
- `AgentManager` (lifecycle + registry)
- A2A protocol (queue-based) + DB-backed message queue
- DashScope LLM client

### Phase 3 — Scenario System ✅

- `BaseScenario` interface + components
- `ScenarioManager`
- Flow definitions
- Scenario Gradio page + Flask API

### Phase 4 — Decoupled Execution + Dependency Scheduling ✅

- Execution agent split into a **standalone WebSocket-connected server** (`execution_server/`), with backend WS server + CentralDispatcher routing.
- Exec-server registry (`execution_servers` table): status, quota, running count, env probe (CLI tools + host info), heartbeat.
- Per-role **execution-server dropdown** in scenario creation; no selection → local fallback.
- **Dependency-aware scheduling**: LLM emits `id` + `depends_on`; wave-based serial/parallel execution with upstream-result injection and failure propagation.
- `cancelled → running` scenario restart; execution tasks now record agent name/role/duration.

### Phase 5 — Production (Next)

- ⏳ MySQL/PostgreSQL migration
- ⏳ Docker / Kubernetes sandbox
- ⏳ Redis event bus + message queue
- ⏳ Authentication hardening
- ⏳ Production deployment

## Documentation

- **API Reference**: `docs/API.md`
- **User Guide**: `docs/USER_GUIDE.md`
- **Knowledge Base**: `knowledge/README.md` — architecture decisions, patterns, guidelines
- **Task List**: `tasks/todo.md` — implementation progress
- **Lessons Learned**: `tasks/lessons.md` — insights and corrections

## License

Private — Agent Server Platform Team

## Contact

For questions or issues, contact the Agent Server Platform Team.

---

**Version**: 2.1.0
**Last Updated**: 2026/07/22
**Status**: Phase 4 Complete — Decoupled Execution Server + Dependency Scheduling
