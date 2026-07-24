# CRD 声明式资源 —— 对应 hiclaw agentteams.io/v1beta1
#
# 四类资源：Worker / Manager / Team / Human。每类资源是一个 dataclass spec，
# 经 ResourceRegistry 物化并持久化到对象存储（_crd/<kind>/<name>.json），
# 对应 hiclaw controller 对 CRD 的调和。
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from .object_store import ObjectStore

API_VERSION = "agentteams.io/v1beta1"


class ResourceState(str, Enum):
    """资源运行态，对应 hiclaw Worker/Manager state。"""
    RUNNING = "Running"
    SLEEPING = "Sleeping"
    STOPPED = "Stopped"


@dataclass
class AccessEntry:
    """云凭据范围隔离项（对应 accessEntries）。"""
    provider: str
    scope: str = ""


@dataclass
class WorkerSpec:
    """执行 Agent 声明。每角色一个 Worker 容器，无状态。"""
    name: str
    model: str = "qwen-plus"
    runtime: str = "openclaw"          # openclaw / copaw / hermes
    image: str = "agentteams/worker-agent:latest"
    role: str = ""                     # 角色专长，写入 SOUL.md
    system_prompt: str = ""            # 角色系统提示词
    skills: List[str] = field(default_factory=list)         # 按需可分发 skills
    mcp_servers: List[str] = field(default_factory=list)
    state: ResourceState = ResourceState.RUNNING
    access_entries: List[AccessEntry] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "Worker"


@dataclass
class ManagerConfig:
    """Manager 运行配置（对应 config 字段）。"""
    heartbeat_interval: int = 30       # 秒
    worker_idle_timeout: int = 600     # 秒
    notify_channel: str = "#scheduler-notify"


@dataclass
class ManagerSpec:
    """调度 Agent 声明。承载目标分解 / 依赖波调度 / Worker 编排。"""
    name: str
    model: str = "qwen-plus"
    runtime: str = "openclaw"          # openclaw / qwenpaw
    image: str = "agentteams/manager:latest"
    skills: List[str] = field(default_factory=lambda: [
        "task-coordination", "task-management", "worker-management",
        "team-management", "channel-management", "file-sync-management",
        "mcporter", "mcp-server-management", "project-management",
    ])
    config: ManagerConfig = field(default_factory=ManagerConfig)
    state: ResourceState = ResourceState.RUNNING

    @property
    def kind(self) -> str:
        return "Manager"


@dataclass
class TeamSpec:
    """Team 声明：Leader + Workers。"""
    name: str
    leader: str = ""                   # leader worker name
    workers: List[str] = field(default_factory=list)
    admin: str = ""
    peer_mentions: bool = True

    @property
    def kind(self) -> str:
        return "Team"


@dataclass
class HumanSpec:
    """Human 参与者声明。"""
    name: str
    display_name: str = ""
    email: str = ""
    permission_level: str = "viewer"
    accessible_teams: List[str] = field(default_factory=list)
    accessible_workers: List[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "Human"


def _spec_to_dict(spec: Any) -> dict:
    """dataclass spec → 可持久化 dict（含 apiVersion/kind）。"""
    d = asdict(spec)
    # AccessEntry 已是 plain dict via asdict；state 转 str
    if "state" in d and isinstance(d["state"], ResourceState):
        d["state"] = d["state"].value
    if "config" in d and isinstance(d["config"], ManagerConfig):
        d["config"] = asdict(d["config"])
    return {"apiVersion": API_VERSION, "kind": spec.kind, "name": spec.name, "spec": d}


class ResourceRegistry:
    """资源注册表：物化 + 持久化 + 查询，对应 controller 调和结果。

    所有资源以 JSON 存于对象存储 _crd/<kind>/<name>.json。
    """

    def __init__(self, store: ObjectStore):
        self.store = store
        # 内存索引：kind -> {name: spec}
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load()

    # --- 持久化 -----------------------------------------------------------
    def _path(self, kind: str, name: str) -> str:
        return f"_crd/{kind.lower()}/{name}.json"

    def _load(self) -> None:
        for kind in ("worker", "manager", "team", "human"):
            prefix = f"_crd/{kind}/"
            for p in self.store.list(prefix):
                doc = self.store.get_json(p)
                if doc and doc.get("kind"):
                    self._index.setdefault(doc["kind"], {})[doc["name"]] = doc

    def _persist(self, spec: Any) -> None:
        doc = _spec_to_dict(spec)
        self.store.put_json(self._path(spec.kind, spec.name), doc)
        self._index.setdefault(spec.kind, {})[spec.name] = doc

    # --- 公共 API ---------------------------------------------------------
    def register(self, spec: Any) -> Any:
        """注册（创建或更新）一个资源。返回 spec 本身。"""
        self._persist(spec)
        return spec

    def get(self, kind: str, name: str) -> Optional[dict]:
        return self._index.get(kind, {}).get(name)

    def list(self, kind: str) -> List[dict]:
        return list(self._index.get(kind, {}).values())

    def delete(self, kind: str, name: str) -> bool:
        if name not in self._index.get(kind, {}):
            return False
        self.store.delete(self._path(kind, name))
        self._index[kind].pop(name, None)
        return True

    # 类型化便捷方法
    def create_worker(self, spec: WorkerSpec) -> WorkerSpec:
        self.register(spec)
        return spec

    def create_manager(self, spec: ManagerSpec) -> ManagerSpec:
        self.register(spec)
        return spec

    def list_workers(self) -> List[dict]:
        return self.list("Worker")

    def list_managers(self) -> List[dict]:
        return self.list("Manager")
