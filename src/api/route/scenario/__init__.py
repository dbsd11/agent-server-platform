# Scenario API endpoints
import uuid
import json
from datetime import datetime
from flask import request
from flask_restx import Resource, fields, Namespace

from database.repositories.scenario_repository import ScenarioRepository
from database.models.scenario import Scenario
from core.event_bus import event_bus
from scenarios.scenario_manager import scenario_manager
from scenarios.examples.simple_qa_scenario import SimpleQAScenario
from scenarios.examples.code_execution_scenario import CodeExecutionScenario
from scenarios.examples.debate_scenario import DebateScenario


def register_scenario_routes(app):
    """Register scenario API routes"""
    api = app.config.get('RESTX_API')

    scenario_ns = Namespace('scenario', description='Scenario management API')
    api.add_namespace(scenario_ns, path='/api/scenario')

    # Models
    create_scenario_model = api.model('CreateScenario', {
        'scenario_type': fields.String(required=True, description='Scenario type (simple_qa, code_execution)'),
        'name': fields.String(required=True, description='Scenario name'),
        'description': fields.String(description='Scenario description'),
        'config': fields.Raw(description='Scenario configuration (JSON)'),
    })

    scenario_model = api.model('Scenario', {
        'scenario_id': fields.String(description='Scenario ID'),
        'scenario_type': fields.String(description='Scenario type'),
        'name': fields.String(description='Scenario name'),
        'state': fields.String(description='Scenario state'),
        'created_at': fields.String(description='Created timestamp'),
    })

    @scenario_ns.route('')
    class ScenarioList(Resource):
        @scenario_ns.doc('list_scenarios')
        @scenario_ns.marshal_list_with(scenario_model)
        def get(self):
            """List all scenarios"""
            return scenario_manager.list_scenarios()

        @scenario_ns.doc('create_scenario')
        @scenario_ns.expect(create_scenario_model)
        def post(self):
            """Create new scenario"""
            data = request.get_json()
            scenario_type = data.get('scenario_type')
            name = data.get('name')
            description = data.get('description', '')
            config = data.get('config', {})

            if not scenario_type or not name:
                return {"error": "scenario_type and name are required"}, 400

            # Create scenario using ScenarioManager
            scenario_id = scenario_manager.create_scenario(
                scenario_type=scenario_type,
                name=name,
                description=description,
                config=config
            )

            return {"scenario_id": scenario_id, "status": "created"}, 201

    @scenario_ns.route('/<string:scenario_id>')
    class ScenarioInstance(Resource):
        @scenario_ns.doc('get_scenario')
        def get(self, scenario_id):
            """Get scenario details"""
            status = scenario_manager.get_scenario_status(scenario_id)

            if not status:
                return {"error": "Scenario not found"}, 404

            return status

        @scenario_ns.doc('start_scenario')
        def post(self, scenario_id):
            """Start scenario execution"""
            # Get scenario from database
            scenario_repo = ScenarioRepository()
            scenario = scenario_repo.find_by_scenario_id(scenario_id)

            if not scenario:
                return {"error": "Scenario not found"}, 404

            # Create scenario instance based on type
            scenario_type = scenario.scenario_type
            config = json.loads(scenario.config) if scenario.config else {}

            if scenario_type == "simple_qa":
                scenario_instance = SimpleQAScenario()
            elif scenario_type == "code_execution":
                scenario_instance = CodeExecutionScenario()
            elif scenario_type == "debate":
                scenario_instance = DebateScenario()
            else:
                return {"error": f"Unknown scenario type: {scenario_type}"}, 400

            # Start scenario
            success = scenario_manager.start_scenario(scenario_id, scenario_instance)

            if not success:
                return {"error": "Failed to start scenario"}, 500

            return {"status": "started", "scenario_id": scenario_id}

        @scenario_ns.doc('stop_scenario')
        def delete(self, scenario_id):
            """Stop scenario"""
            success = scenario_manager.stop_scenario(scenario_id)

            if not success:
                return {"error": "Scenario not found or already stopped"}, 404

            return {"status": "stopped", "scenario_id": scenario_id}
