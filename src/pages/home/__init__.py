# Home page - Dashboard
import gradio as gr
from datetime import datetime

from database.repositories.agent_repository import AgentRepository
from database.repositories.task_repository import TaskRepository
from database.repositories.scenario_repository import ScenarioRepository
from database.repositories.event_repository import EventRepository


def create_page(global_state_component):
    """Create home page"""
    with gr.Blocks() as page:
        gr.Markdown("# Agent Server Platform - Dashboard")
        gr.Markdown("*Welcome to the universal agent-server platform*")

        # Statistics
        with gr.Row():
            with gr.Column():
                agent_count = gr.Number(label="Registered Agents", value=0)
            with gr.Column():
                task_count = gr.Number(label="Total Tasks", value=0)
            with gr.Column():
                running_tasks = gr.Number(label="Running Tasks", value=0)
            with gr.Column():
                scenario_count = gr.Number(label="Scenarios", value=0)

        # Recent events
        gr.Markdown("## Recent Events")
        event_log = gr.Dataframe(
            headers=["Timestamp", "Event Type", "Data"],
            label="Recent Events",
            wrap=True
        )

        # System info
        with gr.Accordion("System Information", open=False):
            gr.Markdown(f"""
            **Platform Version**: 1.0.0
            **Database**: SQLite
            **Started**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

            **Architecture**:
            - Layer 1: Universal Agent Platform (Scheduling Agent, Execution Agent, A2A, Events, Watchdog)
            - Layer 2: Scenario Mode Agent Collaboration Platform
            """)

        def load_stats():
            """Load statistics"""
            agent_repo = AgentRepository()
            task_repo = TaskRepository()
            scenario_repo = ScenarioRepository()
            event_repo = EventRepository()

            agents = agent_repo.find_all()
            tasks = task_repo.find_all()
            running = task_repo.find_by_state("running")
            scenarios = scenario_repo.find_all()

            return len(agents), len(tasks), len(running), len(scenarios)

        def load_recent_events():
            """Load recent events"""
            event_repo = EventRepository()
            events = event_repo.find_recent(limit=10)

            return [[e.timestamp, e.event_type, e.data] for e in events]

        # Load on page load
        page.load(load_stats, outputs=[agent_count, task_count, running_tasks, scenario_count])
        page.load(load_recent_events, outputs=[event_log])

    return page
