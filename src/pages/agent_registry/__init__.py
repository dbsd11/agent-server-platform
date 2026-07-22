# Agent Registry page — read-only view of active agents (scenario-scoped)
import json
import gradio as gr

from database.repositories.agent_repository import AgentRepository


def create_page(global_state_component):
    """Create agent registry page — displays agents registered during scenario execution"""
    with gr.Blocks() as page:
        gr.Markdown("# Agent 注册表 (Agent Registry)")
        gr.Markdown("*场景运行时自动注册的底层 Agent，场景结束后自动清理*")

        # ZONE A: Filters + list (always visible by default)
        with gr.Column(elem_id="agent_list_content") as agent_list_content:
            with gr.Row():
                with gr.Column(scale=3):
                    with gr.Row():
                        type_filter = gr.Dropdown(
                            choices=["all", "scheduling", "execution"],
                            label="Agent 类型",
                            value="all"
                        )
                        status_filter = gr.Dropdown(
                            choices=["all", "active", "inactive", "error"],
                            label="状态",
                            value="all"
                        )
                with gr.Column(scale=1):
                    refresh_btn = gr.Button("刷新", variant="secondary")

            agent_list = gr.Dataframe(
                headers=["Agent ID", "场景ID", "类型", "名称", "角色/配置", "状态", "创建时间"],
                label="运行中的 Agent",
                wrap=True
            )

            # Hidden trigger for showing detail view
            show_detail_trigger = gr.Textbox(value="", visible=False)

        # ZONE B: Detail view (hidden by default)
        with gr.Column(elem_id="agent_detail_content", visible=False) as agent_detail_content:
            gr.Markdown("## Agent 详情")
            agent_id_display = gr.Textbox(label="Agent ID", interactive=False)
            agent_detail = gr.JSON(label="Agent 详细信息")

            with gr.Row():
                back_btn = gr.Button("返回列表", variant="secondary")
                refresh_detail_btn = gr.Button("刷新", variant="primary")

        # Auto-refresh timer (hidden background trigger)
        refresh_timer = gr.Timer(value=5, render=False)

        def load_agents(type_filter, status_filter):
            """Load agents with optional filters"""
            agent_repo = AgentRepository()

            if type_filter != "all" and status_filter != "all":
                agents = [a for a in agent_repo.find_by_type(type_filter) if a.status == status_filter]
            elif type_filter != "all":
                agents = agent_repo.find_by_type(type_filter)
            elif status_filter != "all":
                agents = [a for a in agent_repo.find_all(limit=200) if a.status == status_filter]
            else:
                agents = agent_repo.find_all(limit=200)

            rows = []
            for a in agents:
                # Extract role/config summary from JSON
                config_summary = ""
                if a.config:
                    try:
                        cfg = json.loads(a.config)
                        role = cfg.get("role", "")
                        sys_prompt = cfg.get("system_prompt", "")
                        if role:
                            config_summary = f"角色: {role}"
                        if sys_prompt:
                            config_summary += f" | {sys_prompt[:50]}"
                        if not config_summary:
                            config_summary = str(cfg)[:80]
                    except (json.JSONDecodeError, TypeError):
                        config_summary = str(a.config)[:80]

                scenario_display = "—"
                if a.scenario_id:
                    scenario_display = a.scenario_id[:12] + "..." if len(a.scenario_id) > 12 else a.scenario_id

                rows.append([
                    a.agent_id,
                    scenario_display,
                    a.agent_type,
                    a.name or "—",
                    config_summary,
                    a.status,
                    str(a.created_at) if a.created_at else "—",
                ])
            return rows

        def show_agent_detail(agent_id):
            """Show full agent details"""
            if not agent_id:
                return {}, ""

            agent_repo = AgentRepository()
            agent = agent_repo.find_by_agent_id(agent_id)
            if not agent:
                return {"error": "Agent not found"}, ""

            result = {
                "agent_id": agent.agent_id,
                "scenario_id": agent.scenario_id,
                "agent_type": agent.agent_type,
                "name": agent.name,
                "description": agent.description,
                "status": agent.status,
                "created_at": str(agent.created_at) if agent.created_at else None,
                "updated_at": str(agent.updated_at) if agent.updated_at else None,
            }
            # Parse config JSON for display
            if agent.config:
                try:
                    result["config"] = json.loads(agent.config)
                except (json.JSONDecodeError, TypeError):
                    result["config"] = agent.config

            return result, agent_id

        def show_detail_view(agent_id):
            """Show detail view and hide list"""
            if agent_id:
                detail, display_id = show_agent_detail(agent_id)
                return gr.update(visible=False), gr.update(visible=True), display_id, detail
            return gr.update(), gr.update(), "", {}

        def hide_detail():
            """Hide detail view and show list"""
            return gr.update(visible=True), gr.update(visible=False), "", {}

        def handle_agent_select(evt: gr.SelectData):
            """Handle agent list row selection - only first column"""
            col_index = evt.index[1]
            if col_index == 0:
                return evt.value
            return None

        # Event handlers
        refresh_timer.tick(
            load_agents,
            inputs=[type_filter, status_filter],
            outputs=[agent_list]
        )
        refresh_btn.click(load_agents, inputs=[type_filter, status_filter], outputs=[agent_list])

        # Handle agent list row selection
        agent_list.select(
            fn=handle_agent_select,
            outputs=[show_detail_trigger]
        )

        # Show detail when trigger changes
        show_detail_trigger.change(
            show_detail_view,
            inputs=[show_detail_trigger],
            outputs=[agent_list_content, agent_detail_content, agent_id_display, agent_detail]
        )

        # Back button
        back_btn.click(
            hide_detail,
            outputs=[agent_list_content, agent_detail_content, show_detail_trigger, agent_detail]
        )

        # Refresh detail
        refresh_detail_btn.click(
            show_agent_detail,
            inputs=[agent_id_display],
            outputs=[agent_detail, agent_id_display]
        )

        # Load on page load
        page.load(load_agents, inputs=[type_filter, status_filter], outputs=[agent_list])

    return page
