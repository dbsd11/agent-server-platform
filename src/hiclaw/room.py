# Room —— Matrix 通信抽象（in-process 实现，可替换为 Tuwunel/Matrix client）
#
# Room 提供单一时间线：分配、进度、干预、完成通知共享同一消息流，对应 hiclaw
# 的 human-in-the-loop 可见性。RoomService 创建/查找 Room，模拟 Matrix homeserver。
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Message:
    sender: str
    text: str
    ts: float = field(default_factory=time.time)
    meta: Optional[dict] = None


class Room:
    """一个 Matrix Room：成员 + 消息时间线。"""

    def __init__(self, room_id: str, members: List[str]):
        self.room_id = room_id
        self.members = list(members)
        self.messages: List[Message] = []

    def join(self, member: str) -> None:
        if member not in self.members:
            self.members.append(member)

    def send(self, sender: str, text: str, meta: Optional[dict] = None) -> Message:
        msg = Message(sender=sender, text=text, meta=meta)
        self.messages.append(msg)
        return msg

    def timeline(self) -> List[dict]:
        return [{"sender": m.sender, "text": m.text, "ts": m.ts,
                 "meta": m.meta} for m in self.messages]

    def recent(self, since_ts: float = 0.0) -> List[Message]:
        return [m for m in self.messages if m.ts >= since_ts]


class RoomService:
    """Room 注册表，模拟 Matrix homeserver 的房间管理。"""

    def __init__(self):
        self._rooms: Dict[str, Room] = {}

    def create(self, room_id: str, members: List[str]) -> Room:
        room = Room(room_id, members)
        self._rooms[room_id] = room
        return room

    def get(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def get_or_create(self, room_id: str, members: List[str]) -> Room:
        room = self._rooms.get(room_id)
        if room is None:
            room = self.create(room_id, members)
        else:
            for m in members:
                room.join(m)
        return room

    def list(self) -> List[Room]:
        return list(self._rooms.values())

    @staticmethod
    def room_id_for(worker_name: str) -> str:
        """Worker 与 Manager 的共享 Room 命名。"""
        return f"!worker-{worker_name}"
