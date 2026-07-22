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
