# Hiclaw 适配层 —— 自包含的 AgentTeams (hiclaw) 概念实现
#
# 依据 docs/agentteams-hiclaw.md，将 hiclaw 的 Manager/Worker/CRD/Skills/
# Matrix/MinIO 抽象落地为纯 Python 可离线运行的适配层，不依赖外部 Docker 栈
# 与 LLM Key（LLM 缺失时回退到启发式，保证可验证）。
#
# 与现有 src/core/agents 并存：现有实现为进程内 local 运行时，本包为 hiclaw
# 运行时，二者共用 agent_roles / Scenario 配置语义。

from .object_store import ObjectStore, LocalFileStore
from .crd import (
    WorkerSpec, ManagerSpec, TeamSpec, HumanSpec,
    ResourceState, ResourceRegistry,
)
from .soul import Soul, render_soul
from .skills import Skill, SkillLoader
from .room import Room, RoomService
from .worker import HiclawWorker
from .mcp import McpGateway
from .scheduler import build_waves, normalize_subtasks, inject_upstream, WaveError
from .manager import HiclawManager

__all__ = [
    "ObjectStore", "LocalFileStore",
    "WorkerSpec", "ManagerSpec", "TeamSpec", "HumanSpec",
    "ResourceState", "ResourceRegistry",
    "Soul", "render_soul",
    "Skill", "SkillLoader",
    "Room", "RoomService",
    "HiclawWorker", "HiclawManager", "McpGateway",
    "build_waves", "normalize_subtasks", "inject_upstream", "WaveError",
]
