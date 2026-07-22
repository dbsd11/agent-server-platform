# Agent Server Platform

A universal agent-server platform built on Gradio + Flask, implementing a two-tier architecture for multi-agent orchestration: scenario-based collaboration, task scheduling, sandboxed execution, agent-to-agent (A2A) messaging, and fault-tolerant watchdog recovery.

## Features

### Two-Tier Architecture

- **Layer 1 — Universal Agent Platform**: SchedulingAgent (ReAct), ExecutionAgent, AgentManager, A2A protocol, message queue, event bus, watchdog, sandbox.
- **Layer 2 — Scenario Collaboration Platform**: scenario state machine, BaseScenario, flow definitions, ScenarioManager.

### Core Components

| Component | Module | Description |
|-----------|--------|-------------|
| State Machine | `core/state_machine.py` | Enum-based, task + scenario state machines with transition tables |
| Event Bus | `core/event_bus.py` | Queue-based events with DB persistence, wildcard subscriptions |
| Watchdog | `core/watchdog.py` | Timeout detection and recovery |
| Sandbox | `core/sandbox.py` | Subprocess-isolated task execution (evolves to Docker/K8s) |
| A2A Protocol | `core/a2a_protocol.py` | Queue-based agent-to-agent messaging |
| Message Queue | `core/message_queue.py` | DB-backed transport with per-consumer offsets |
| LLM Client | `core/llm_client.py` | DashScope (OpenAI-compatible) wrapper |
| Agents | `core/agents/` | `BaseAgent`, `SchedulingAgent` (ReAct), `ExecutionAgent` |
| Agent Manager | `agents/agent_manager.py` | Agent lifecycle and registry |
| Scenarios | `scenarios/` | `BaseScenario`, `ScenarioManager`, flow definitions, components |

### User Interface

- **Gradio Web UI** (6 pages): Home, Agent Registry, Task Monitor, Scenario Dashboard, Event Log, User.
- **Flask REST API**: Task, Scenario, Event endpoints with Swagger docs (`flask-restx`).

### Database

- **SQLite** for development (zero setup).
- **MySQL/PostgreSQL** for production — seamless migration via `BaseModel` / `BaseRepository`.

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

```bash
# Start both Gradio and Flask servers
python src/app.py --all

# Or start individually
python src/app.py --gradio-only   # Gradio UI only
python src/app.py --flask-only    # Flask API only

# Override ports / host
python src/app.py --all --gradio-port 8080 --flask-port 5000 --host 0.0.0.0
```

### Access

- **Gradio UI**: http://localhost:8080
- **Flask API**: http://localhost:5000
- **Swagger Docs**: http://localhost:5000/docs

## Testing

Full test suite lives in `tests/`:

```bash
pytest -v
```

Covers: state machine, event bus, watchdog, sandbox, A2A protocol, message queue, agents, scenario components, flow definitions, and an end-to-end integration test.

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
│   │   ├── llm_client.py           # DashScope (OpenAI-compatible) client
│   │   └── agents/                 # BaseAgent, SchedulingAgent, ExecutionAgent
│   ├── agents/                     # AgentManager
│   ├── scenarios/                  # BaseScenario, ScenarioManager, flow defs, components
│   ├── api/                        # Flask REST API (flask-restx)
│   │   └── route/                  # task / scenario / event namespaces
│   ├── pages/                      # Gradio UI pages (6)
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

**Scenario State Machine**:
- `INITIALIZING → RUNNING → COMPLETED / FAILED / CANCELLED`

### Event Types

- **Task**: `task.created`, `task.state_changed`, `task.execution_started/completed/failed`
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

### Phase 4 — Production (Next)

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

**Version**: 2.0.0
**Last Updated**: 2026/07/22
**Status**: Phase 3 Complete
