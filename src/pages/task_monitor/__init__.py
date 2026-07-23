# Task Monitor page
import gradio as gr
from datetime import datetime

from database.repositories.task_repository import TaskRepository
from database.repositories.scenario_repository import ScenarioRepository


def create_page(global_state_component):
    """Create task monitor page"""
    with gr.Blocks() as page:
        gr.Markdown("# Task Monitor")
        gr.Markdown("*Monitor task execution and state transitions*")

        # ZONE A: Filters + list (always visible by default)
        with gr.Column(elem_id="task_list_content") as task_list_content:
            with gr.Row():
                with gr.Column(scale=3):
                    state_filter = gr.Dropdown(
                        choices=["all", "pending", "running", "waiting", "success", "failed", "timeout", "cancelled"],
                        label="Filter by State",
                        value="all"
                    )
                with gr.Column(scale=1):
                    refresh_btn = gr.Button("Refresh", variant="secondary")

            task_list = gr.Dataframe(
                headers=["Task ID", "Scenario ID", "Scenario Name", "Goal", "State", "Agent Name", "Agent Role", "Duration (s)", "Priority", "Retry Count", "Created At"],
                label="Tasks",
                wrap=True
            )

            # Hidden trigger for showing detail view
            show_detail_trigger = gr.Textbox(value="", visible=False)

        # ZONE B: Detail view (hidden by default)
        with gr.Column(elem_id="task_detail_content", visible=False) as task_detail_content:
            gr.Markdown("## Task Details")
            task_id_display = gr.Textbox(label="Task ID", interactive=False)
            task_detail = gr.JSON(label="Task Details")

            with gr.Row():
                back_btn = gr.Button("返回列表", variant="secondary")
                refresh_detail_btn = gr.Button("刷新", variant="primary")

        # Hidden timer for auto-refresh
        refresh_timer = gr.Timer(value=5, render=False)

        def load_tasks(state_filter):
            """Load tasks with optional state filter"""
            task_repo = TaskRepository()

            if state_filter == "all":
                tasks = task_repo.find_all(limit=100)
            else:
                tasks = task_repo.find_by_state(state_filter)

            # Resolve scenario names in one pass (one query, not N)
            scenario_names = {}
            scenario_ids = {t.scenario_id for t in tasks if t.scenario_id}
            if scenario_ids:
                for s in ScenarioRepository().find_all(limit=500):
                    scenario_names[s.scenario_id] = s.name

            rows = []
            for t in tasks:
                # Format execution duration
                duration_display = ""
                if t.execution_duration is not None:
                    duration_display = f"{t.execution_duration:.2f}"

                sid = t.scenario_id or ""
                rows.append([
                    t.task_id,
                    sid[:12] + "..." if len(sid) > 12 else (sid or "—"),
                    scenario_names.get(sid, "—"),
                    t.goal[:50] if t.goal else "",
                    t.state,
                    t.agent_name or "—",
                    t.agent_role or "—",
                    duration_display,
                    t.priority,
                    t.retry_count,
                    str(t.created_at)
                ])
            return rows

        def show_task_detail(task_id):
            """Show task details"""
            if not task_id:
                return {}, ""

            task_repo = TaskRepository()
            task = task_repo.find_by_task_id(task_id)

            if not task:
                return {"error": "Task not found"}, ""

            return task.to_dict(), task_id

        def show_detail_view(task_id):
            """Show detail view and hide list"""
            if task_id:
                detail, display_id = show_task_detail(task_id)
                return gr.update(visible=False), gr.update(visible=True), display_id, detail
            return gr.update(), gr.update(), "", {}

        def hide_detail():
            """Hide detail view and show list"""
            return gr.update(visible=True), gr.update(visible=False), "", {}

        def handle_task_select(evt: gr.SelectData):
            """Handle task list row selection - only first column"""
            col_index = evt.index[1]
            if col_index == 0:
                return evt.value
            return None

        # Event handlers
        refresh_timer.tick(lambda: load_tasks(state_filter.value), outputs=[task_list])
        refresh_btn.click(load_tasks, inputs=[state_filter], outputs=[task_list])

        # Handle task list row selection
        task_list.select(
            fn=handle_task_select,
            outputs=[show_detail_trigger]
        )

        # Show detail when trigger changes
        show_detail_trigger.change(
            show_detail_view,
            inputs=[show_detail_trigger],
            outputs=[task_list_content, task_detail_content, task_id_display, task_detail]
        )

        # Back button
        back_btn.click(
            hide_detail,
            outputs=[task_list_content, task_detail_content, show_detail_trigger, task_detail]
        )

        # Refresh detail
        refresh_detail_btn.click(
            show_task_detail,
            inputs=[task_id_display],
            outputs=[task_detail, task_id_display]
        )

        # Load on page load
        page.load(load_tasks, inputs=[state_filter], outputs=[task_list])

    return page
