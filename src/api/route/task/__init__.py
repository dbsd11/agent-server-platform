# Task API endpoints
import uuid
import json
from datetime import datetime
from flask import request
from flask_restx import Resource, fields, Namespace

from database.repositories.task_repository import TaskRepository
from database.models.task import Task
from core.state_machine import TaskState
from core.event_bus import event_bus
from agents.agent_manager import agent_manager


def register_task_routes(app):
    """Register task API routes"""
    api = app.config.get('RESTX_API')

    task_ns = Namespace('task', description='Task management API')
    api.add_namespace(task_ns, path='/api/task')

    # Models
    create_task_model = api.model('CreateTask', {
        'goal': fields.String(required=True, description='Task goal'),
        'scenario_id': fields.String(description='Scenario ID'),
        'priority': fields.Integer(description='Task priority', default=0),
        'timeout_seconds': fields.Integer(description='Timeout in seconds', default=3600),
        'context': fields.Raw(description='Task context (JSON)'),
    })

    task_model = api.model('Task', {
        'task_id': fields.String(description='Task ID'),
        'goal': fields.String(description='Task goal'),
        'state': fields.String(description='Task state'),
        'priority': fields.Integer(description='Priority'),
        'retry_count': fields.Integer(description='Retry count'),
        'created_at': fields.String(description='Created timestamp'),
    })

    @task_ns.route('')
    class TaskList(Resource):
        @task_ns.doc('list_tasks')
        @task_ns.marshal_list_with(task_model)
        def get(self):
            """List all tasks"""
            task_repo = TaskRepository()
            tasks = task_repo.find_all(limit=100)
            return [t.to_dict() for t in tasks]

        @task_ns.doc('create_task')
        @task_ns.expect(create_task_model)
        def post(self):
            """Create and submit new task"""
            data = request.get_json()
            goal = data.get('goal')
            agent_type = data.get('agent_type', 'scheduling')
            scenario_id = data.get('scenario_id')
            priority = data.get('priority', 0)
            timeout_seconds = data.get('timeout_seconds', 3600)
            context = data.get('context', {})

            if not goal:
                return {"error": "Goal is required"}, 400

            # Submit task to agent_manager (creates task and starts execution)
            task_id = agent_manager.submit_task(
                goal=goal,
                agent_type=agent_type,
                scenario_id=scenario_id,
                priority=priority,
                timeout_seconds=timeout_seconds,
                context=context
            )

            return {"task_id": task_id, "status": "created"}, 201

    @task_ns.route('/<string:task_id>')
    class TaskInstance(Resource):
        @task_ns.doc('get_task')
        def get(self, task_id):
            """Get task details"""
            task_repo = TaskRepository()
            task = task_repo.find_by_task_id(task_id)

            if not task:
                return {"error": "Task not found"}, 404

            return task.to_dict()

        @task_ns.doc('cancel_task')
        def delete(self, task_id):
            """Cancel task"""
            task_repo = TaskRepository()
            task = task_repo.find_by_task_id(task_id)

            if not task:
                return {"error": "Task not found"}, 404

            task_repo.update_task_state(task_id, TaskState.CANCELLED.value)

            event_bus.emit("task.cancelled", {"task_id": task_id})

            return {"status": "cancelled"}
