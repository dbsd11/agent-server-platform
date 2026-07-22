# Message repository
from typing import List
from .base_repository import BaseRepository
from ..models.message import Message


class MessageRepository(BaseRepository[Message]):
    """Repository for Message model operations"""

    def __init__(self):
        super().__init__(Message)

    def find_by_scenario_id(self, scenario_id: str, limit: int = 200) -> List[Message]:
        """Find all messages for a scenario, ordered by timestamp desc"""
        if not scenario_id:
            return []
        return self.find_by_criteria({"scenario_id": scenario_id}, limit=limit)

    def find_pending_messages(self, consumer_id: str, scenario_id: str,
                              message_type: str, limit: int = 100) -> List[Message]:
        """Find unconsumed messages for a BOT (id > consumer's offset), ordered by id ASC."""
        from .consumer_offset_repository import ConsumerOffsetRepository
        offset_repo = ConsumerOffsetRepository()
        offset = offset_repo.get_offset(consumer_id)

        criteria = {
            "scenario_id": scenario_id,
            "message_type": message_type,
            "id": {"operator": ">", "condition": offset},
        }
        return self.find_by_criteria(criteria, order_by="id ASC", limit=limit)

    def find_pending_dispatch_global(self, consumer_id: str, limit: int = 100) -> List[Message]:
        """Find unconsumed dispatch messages across ALL scenarios (global consumer).

        Used by the CentralDispatcher: queries dispatch rows with id > the
        global consumer offset, ordered id ASC, regardless of scenario_id.
        """
        from .consumer_offset_repository import ConsumerOffsetRepository
        offset_repo = ConsumerOffsetRepository()
        offset = offset_repo.get_offset(consumer_id)

        criteria = {
            "message_type": "dispatch",
            "id": {"operator": ">", "condition": offset},
        }
        return self.find_by_criteria(criteria, order_by="id ASC", limit=limit)

    def max_message_id(self) -> int:
        """Return MAX(id) over the messages table, or 0 if empty."""
        from ..connection import get_connection_manager
        cm = get_connection_manager()
        with cm.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COALESCE(MAX(id), 0) AS m FROM {self.table_name}")
            row = cursor.fetchone()
            return int(row["m"]) if row else 0
