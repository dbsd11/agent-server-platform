# API route registration
from api.route.task import register_task_routes
from api.route.scenario import register_scenario_routes
from api.route.event import register_event_routes


def make_route(app):
    """Register all API routes"""
    register_task_routes(app)
    register_scenario_routes(app)
    register_event_routes(app)
