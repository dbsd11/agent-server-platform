# Task repository
from typing import Optional, List
from datetime import datetime
from .base_repository import BaseRepository
from ..models.task import Task


class TaskRepository(BaseRepository[Task]):
    """Repository for Task model operations"""

    def __init__(self):
        super().__init__(Task)
        self._ensure_columns()

    def _ensure_columns(self):
        """
        Add columns that may be missing from older DBs (migration).

        ponytail: ALTER TABLE ADD COLUMN with error suppression.
        No-op if columns already exist or table doesn't exist yet.
        """
        from database.connection import get_connection_manager
        cols_to_add = {
            "topic_id": "TEXT",
            "idempotency_key": "TEXT",
            "depends_on": "TEXT",
            "agent_name": "TEXT",
            "agent_role": "TEXT",
            "execution_duration": "REAL",
        }
        try:
            conn_mgr = get_connection_manager()
            with conn_mgr.get_connection() as conn:
                cursor = conn.cursor()
                # Get existing columns (fails silently if table doesn't exist yet)
                try:
                    cursor.execute(f"PRAGMA table_info({self.table_name})")
                    existing = {row["name"] for row in cursor.fetchall()}
                except Exception:
                    return  # Table doesn't exist yet, migration will run after creation
                for col, col_type in cols_to_add.items():
                    if col not in existing:
                        try:
                            cursor.execute(
                                f"ALTER TABLE {self.table_name} ADD COLUMN {col} {col_type}"
                            )
                            conn.commit()
                        except Exception:
                            pass  # Column may already exist in race conditions
        except Exception:
            pass  # DB not initialized yet

    def find_by_task_id(self, task_id: str) -> Optional[Task]:
        """Find task by task_id (UUID)"""
        results = self.find_by_criteria({"task_id": task_id})
        return results[0] if results else None

    def find_by_idempotency_key(self, key: str) -> Optional[Task]:
        """Find task by idempotency_key (幂等控制 lookup)"""
        if not key:
            return None
        results = self.find_by_criteria({"idempotency_key": key})
        return results[0] if results else None

    def find_by_topic_id(self, topic_id: str) -> List[Task]:
        """Find all subtasks belonging to a topic (子主题 lookup)"""
        if not topic_id:
            return []
        return self.find_by_criteria({"topic_id": topic_id})

    def find_by_state(self, state: str) -> List[Task]:
        """Find tasks by state"""
        return self.find_by_criteria({"state": state})

    def find_by_scenario_id(self, scenario_id: str) -> List[Task]:
        """Find tasks by scenario_id"""
        return self.find_by_criteria({"scenario_id": scenario_id})

    def find_by_parent_task_id(self, parent_task_id: str) -> List[Task]:
        """Find all subtasks of a parent task"""
        return self.find_by_criteria({"parent_task_id": parent_task_id})

    def update_task_state(self, task_id: str, new_state: str) -> bool:
        """Update task state"""
        task = self.find_by_task_id(task_id)
        if not task:
            return False
        task.state = new_state
        task.updated_at = datetime.now()
        return self.update(task)

    def increment_retry_count(self, task_id: str) -> bool:
        """Increment task retry count"""
        task = self.find_by_task_id(task_id)
        if not task:
            return False
        task.retry_count = (task.retry_count or 0) + 1
        task.updated_at = datetime.now()
        return self.update(task)

    def mark_as_started(self, task_id: str) -> bool:
        """Mark task as started"""
        task = self.find_by_task_id(task_id)
        if not task:
            return False
        task.state = "running"
        task.started_at = datetime.now()
        task.updated_at = datetime.now()
        return self.update(task)

    def mark_as_completed(self, task_id: str, result: str = None, agent_name: str = None,
                          agent_role: str = None, execution_duration: float = None) -> bool:
        """Mark task as completed with agent information"""
        task = self.find_by_task_id(task_id)
        if not task:
            return False
        task.state = "success"
        task.result = result
        task.completed_at = datetime.now()
        task.updated_at = datetime.now()
        if agent_name is not None:
            task.agent_name = agent_name
        if agent_role is not None:
            task.agent_role = agent_role
        if execution_duration is not None:
            task.execution_duration = execution_duration
        return self.update(task)

    def mark_as_failed(self, task_id: str, error: str) -> bool:
        """Mark task as failed"""
        task = self.find_by_task_id(task_id)
        if not task:
            return False
        task.state = "failed"
        task.error = error
        task.completed_at = datetime.now()
        task.updated_at = datetime.now()
        return self.update(task)
