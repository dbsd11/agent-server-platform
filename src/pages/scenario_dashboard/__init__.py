# Scenario Dashboard page
import json
import gradio as gr
import random
from functools import partial

from scenarios.scenario_manager import scenario_manager
from scenarios.examples.simple_qa_scenario import SimpleQAScenario
from scenarios.examples.code_execution_scenario import CodeExecutionScenario
from database.repositories.task_repository import TaskRepository
from database.repositories.scenario_repository import ScenarioRepository
from pages.scenario_dashboard.export_html import build_message_history, write_export_file

# ponytail: inline registry, add new scenarios here
SCENARIO_REGISTRY = {
    "simple_qa": SimpleQAScenario,
    "code_execution": CodeExecutionScenario,
}

# Scenario config field definitions
SCENARIO_CONFIG_FIELDS = {
    "simple_qa": [
        {"name": "question", "label": "问题 (Question)", "type": "text", "required": True, "placeholder": "What is 2+2?"},
        {"name": "timeout", "label": "超时时间 (秒)", "type": "number", "required": False, "value": 60},
    ],
    "code_execution": [
        {"name": "script", "label": "Python 脚本", "type": "code", "required": False, "placeholder": "print('Hello World')"},
        {"name": "code", "label": "代码片段", "type": "text", "required": False, "placeholder": "print('Hello World')"},
        {"name": "timeout", "label": "超时时间 (秒)", "type": "number", "required": False, "value": 300},
    ],
}


def create_page(global_state_component):
    """Create scenario dashboard page"""
    with gr.Blocks(
        css="""
        .action-btn-small {
            min-width: 0px !important;
            width: auto !important;
            max-width: 30px !important;
            padding: 2px 4px !important;
            font-size: 10px !important;
            border-radius: 3px !important;
            transition: all 0.2s !important;
            flex-shrink: 1 !important;
            line-height: 1 !important;
        }
        .action-btn-small:hover {
            transform: translateY(-1px) !important;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
        }
        .action-row {
            display: flex !important;
            flex-wrap: nowrap !important;
            gap: 2px !important;
            align-items: center !important;
            justify-content: flex-start !important;
            width: auto !important;
        }
        """
    ) as page:
        # Triggers for view switching (always in DOM)
        show_create_trigger = gr.Textbox(value="", visible=False)
        show_action_trigger = gr.Textbox(value="", visible=False)
        show_detail_trigger = gr.Textbox(value="", visible=False)
        show_clone_trigger = gr.Textbox(value="", visible=False)
        refresh_trigger = gr.Textbox(value="", visible=False)

        # ═══════════════════════════════════════════════════════════════
        # ZONE A: Filters + list (always visible by default)
        # ═══════════════════════════════════════════════════════════════
        with gr.Column(elem_id="scenario_list_content") as scenario_list_content:
            # Top action bar with title and new scenario button
            with gr.Row():
                with gr.Column(scale=3):
                    gr.Markdown("# 场景仪表板 (Scenario Dashboard)")
                    gr.Markdown("*创建和监控场景执行*")
                with gr.Column(scale=1):
                    new_scenario_btn = gr.Button("新建场景", variant="primary")

            # Filter row
            with gr.Row():
                with gr.Column(scale=2):
                    search_input = gr.Textbox(
                        placeholder="搜索场景名称...",
                        label="场景名称搜索",
                        value=""
                    )
                with gr.Column(scale=1):
                    state_filter = gr.Dropdown(
                        choices=["all", "pending", "running", "completed", "failed", "stopped"],
                        label="状态筛选",
                        value="all"
                    )
                with gr.Column(scale=1):
                    type_filter = gr.Dropdown(
                        choices=["all"] + list(SCENARIO_REGISTRY.keys()),
                        label="类型筛选",
                        value="all"
                    )
                with gr.Column(scale=1):
                    refresh_btn = gr.Button("刷新", variant="secondary")

            # Render scenario list dynamically with action buttons
            @gr.render(inputs=[search_input, state_filter, type_filter, refresh_trigger])
            def render_scenario_list(search_text="", state="all", type="all", trigger=""):
                scenarios = scenario_manager.list_scenarios()

                # Apply filters
                if search_text and search_text.strip():
                    search_lower = search_text.lower().strip()
                    scenarios = [s for s in scenarios if search_lower in s["name"].lower()]
                if state != "all":
                    scenarios = [s for s in scenarios if s["state"] == state]
                if type != "all":
                    scenarios = [s for s in scenarios if s["scenario_type"] == type]

                # Sort by created_at descending
                scenarios.sort(key=lambda x: x.get("created_at", ""), reverse=True)

                # Render as table
                gr.Markdown("### 场景列表")
                with gr.Row():
                    gr.Markdown("**场景ID**")
                    gr.Markdown("**类型**")
                    gr.Markdown("**名称**")
                    gr.Markdown("**状态**")
                    gr.Markdown("**创建时间**")
                    gr.Markdown("**操作**")

                for scenario in scenarios:
                    scenario_id = scenario["scenario_id"]
                    scenario_state = scenario["state"]

                    with gr.Row():
                        gr.Markdown(scenario_id)
                        gr.Markdown(scenario["scenario_type"])
                        gr.Markdown(scenario["name"])
                        gr.Markdown(scenario_state)
                        gr.Markdown(scenario["created_at"])

                        with gr.Column(scale=1, min_width=50):
                            # Hidden textbox to store scenario_id for this row
                            id_text = gr.Textbox(value=scenario_id, visible=False)

                            # Use HTML container for better control
                            with gr.Row(elem_classes=["action-row"]):
                                # Detail button (always available)
                                detail_btn = gr.Button("👁️", size="sm", elem_classes=["action-btn-small"])
                                detail_btn.click(
                                    fn=lambda id: gr.update(value=id),
                                    inputs=[id_text],
                                    outputs=[show_detail_trigger]
                                )

                                # Clone button (always available) - opens create view pre-filled from this scenario's config
                                clone_btn = gr.Button("📋", size="sm", elem_classes=["action-btn-small"])
                                clone_btn.click(
                                    fn=lambda id: gr.update(value=id),
                                    inputs=[id_text],
                                    outputs=[show_clone_trigger]
                                )

                                # Start button (only if not running)
                                if scenario_state not in ["running", "starting"]:
                                    start_btn = gr.Button("▶️", size="sm", variant="primary", elem_classes=["action-btn-small"])
                                    start_btn.click(
                                        fn=lambda id: gr.update(value=f"{id}@start"),
                                        inputs=[id_text],
                                        outputs=[show_action_trigger]
                                    )

                                # Stop button (only if running)
                                if scenario_state in ["running", "starting"]:
                                    stop_btn = gr.Button("⏹️", size="sm", variant="stop", elem_classes=["action-btn-small"])
                                    stop_btn.click(
                                        fn=lambda id: gr.update(value=f"{id}@stop"),
                                        inputs=[id_text],
                                        outputs=[show_action_trigger]
                                    )

        # ═══════════════════════════════════════════════════════════════
        # ZONE B: Create scenario view (hidden by default)
        # ═══════════════════════════════════════════════════════════════
        with gr.Column(elem_id="scenario_create_content", visible=False) as scenario_create_content:
            gr.Markdown("## 创建新场景")

            with gr.Row():
                scenario_type = gr.Dropdown(
                    choices=list(SCENARIO_REGISTRY.keys()),
                    label="场景类型",
                    value="simple_qa"
                )
                scenario_name = gr.Textbox(label="场景名称", placeholder="我的场景")
                scenario_desc = gr.Textbox(label="场景描述", placeholder="场景描述")

            gr.Markdown("### Agent 角色配置")

            # Scheduling agent role configuration
            with gr.Column():
                gr.Markdown("**调度 Agent 角色**")
                scheduling_agent_role = gr.Textbox(
                    label="调度 Agent 角色定义",
                    placeholder="例如：你是一个任务调度专家，负责分析目标并分解为可执行的子任务...",
                    lines=3,
                    value="你是一个任务调度专家，负责分析用户目标，将其分解为可执行的子任务，并协调执行 Agent 完成任务。"
                )

            # Execution agent roles configuration
            with gr.Column():
                gr.Markdown("**执行 Agent 角色**（可配置多个，最多 10 个）")

                # State to hold the count of roles
                execution_roles_count = gr.State(value=1)

                # Fixed input fields for up to 10 roles
                role_inputs = []
                for i in range(10):
                    with gr.Group(visible=(i == 0)) as role_group:
                        name_input = gr.Textbox(
                            label=f"角色 {i + 1} 名称",
                            value="代码执行专家" if i == 0 else "",
                            placeholder="例如：代码执行专家"
                        )
                        role_input = gr.Textbox(
                            label=f"角色 {i + 1} 定义",
                            value="你是一个代码执行专家，负责执行代码并返回结果。" if i == 0 else "",
                            placeholder="例如：你是一个代码执行专家，负责执行代码并返回结果。",
                            lines=2
                        )
                        role_inputs.append({
                            "group": role_group,
                            "name": name_input,
                            "role": role_input
                        })

                # Add/Remove buttons
                with gr.Row():
                    add_role_btn = gr.Button("➕ 添加执行 Agent 角色", variant="secondary", size="sm")
                    remove_role_btn = gr.Button("➖ 删除最后一个角色", variant="stop", size="sm")

            gr.Markdown("### 场景配置")

            # Simple QA fields
            with gr.Column(visible=True) as simple_qa_config:
                qa_question = gr.Textbox(
                    label="问题 (Question)",
                    placeholder="What is 2+2?",
                    lines=2
                )
                qa_timeout = gr.Number(
                    label="超时时间 (秒)",
                    value=60,
                    minimum=1
                )

            # Code Execution fields
            with gr.Column(visible=False) as code_execution_config:
                code_script = gr.Code(
                    label="Python 脚本",
                    language="python",
                    lines=10
                )
                code_code = gr.Textbox(
                    label="代码片段",
                    placeholder="print('Hello World')",
                    lines=3
                )
                code_timeout = gr.Number(
                    label="超时时间 (秒)",
                    value=300,
                    minimum=1
                )

            status_msg = gr.Markdown("")

            with gr.Row():
                create_btn = gr.Button("创建场景", variant="primary")
                cancel_create_btn = gr.Button("取消", variant="secondary")

        # ═══════════════════════════════════════════════════════════════
        # ZONE C: Scenario actions view (hidden by default)
        # ═══════════════════════════════════════════════════════════════
        with gr.Column(elem_id="scenario_action_content", visible=False) as scenario_action_content:
            gr.Markdown("## 场景操作")

            action_scenario_id = gr.Textbox(label="场景ID", interactive=False)
            action_status_msg = gr.Markdown("")

            with gr.Row():
                start_btn = gr.Button("启动", variant="primary")
                stop_btn = gr.Button("停止", variant="stop")
                back_to_list_btn = gr.Button("返回列表", variant="secondary")

        # ═══════════════════════════════════════════════════════════════
        # ZONE D: Scenario detail view (hidden by default)
        # ═══════════════════════════════════════════════════════════════
        with gr.Column(elem_id="scenario_detail_content", visible=False) as scenario_detail_content:
            gr.Markdown("## 场景详情")

            detail_id_input = gr.Textbox(label="场景ID", interactive=False)
            scenario_detail = gr.JSON(label="场景信息")

            message_history = gr.Dataframe(
                headers=["时间", "类型", "发送方", "接收方", "内容"],
                label="Agent 通信历史记录",
                wrap=True
            )

            with gr.Row():
                back_from_detail_btn = gr.Button("返回列表", variant="secondary")
                refresh_detail_btn = gr.Button("刷新", variant="primary")
                export_html_btn = gr.DownloadButton("📄 导出 HTML", variant="secondary")

            detail_refresh_timer = gr.Timer(value=5, render=False)

        # ═══════════════════════════════════════════════════════════════
        # Helper functions
        # ═══════════════════════════════════════════════════════════════

        def add_execution_role(current_count):
            """Show the next role input group"""
            if current_count is None:
                current_count = 0
            new_count = min(current_count + 1, 10)  # Max 10 roles
            # Return updates for all role groups
            updates = []
            for i in range(10):
                updates.append(gr.update(visible=(i < new_count)))
            return [new_count] + updates

        def remove_execution_role(current_count):
            """Hide the last role input group"""
            if current_count is None or current_count <= 1:
                current_count = 1
            new_count = max(current_count - 1, 1)  # Min 1 role
            # Return updates for all role groups
            updates = []
            for i in range(10):
                updates.append(gr.update(visible=(i < new_count)))
            return [new_count] + updates

        def toggle_config_fields(scenario_type):
            """Show/hide config fields based on scenario type"""
            return (
                gr.update(visible=(scenario_type == "simple_qa")),
                gr.update(visible=(scenario_type == "code_execution"))
            )

        def create_scenario(scenario_type, scenario_name, scenario_desc,
                           scheduling_agent_role, execution_roles_count,
                           # 10 execution roles expanded as name/role pairs
                           # (Gradio passes each role field positionally, not as a list)
                           r0_name, r0_role, r1_name, r1_role, r2_name, r2_role,
                           r3_name, r3_role, r4_name, r4_role, r5_name, r5_role,
                           r6_name, r6_role, r7_name, r7_role, r8_name, r8_role,
                           r9_name, r9_role,
                           qa_question, qa_timeout,
                           code_script, code_code, code_timeout):
            """Create a new scenario via ScenarioManager.

            Inputs: 30 (5 header + 20 role fields + 5 config).
            Outputs: 31 — status_msg + the 30 form fields. The form fields are
            returned as no-op gr.update() because the chained hide_create_view
            (.then) resets them and switches back to the list.
            """
            # 30 no-op updates for the form fields (everything after status_msg)
            noop = tuple(gr.update() for _ in range(30))

            try:
                # Build agent roles configuration
                agent_roles = {
                    "scheduling_agent": {
                        "role": scheduling_agent_role or "你是一个任务调度专家，负责分析用户目标，将其分解为可执行的子任务，并协调执行 Agent 完成任务。"
                    },
                    "execution_agents": []
                }

                # Flatten the 20 role params back into [name1, role1, name2, role2, ...]
                role_values = [
                    r0_name, r0_role, r1_name, r1_role, r2_name, r2_role,
                    r3_name, r3_role, r4_name, r4_role, r5_name, r5_role,
                    r6_name, r6_role, r7_name, r7_role, r8_name, r8_role,
                    r9_name, r9_role,
                ]

                # Parse execution agents: [name1, role1, name2, role2, ...]
                if execution_roles_count and execution_roles_count > 0 and role_values:
                    for i in range(min(execution_roles_count, 10)):
                        name_idx = i * 2
                        role_idx = i * 2 + 1
                        if name_idx < len(role_values) and role_idx < len(role_values):
                            name = role_values[name_idx]
                            role = role_values[role_idx]
                            if name and role:  # Only add if both name and role are provided
                                agent_roles["execution_agents"].append({
                                    "name": name,
                                    "role": role
                                })

                # Build config based on scenario type
                config = {
                    "agent_roles": agent_roles
                }

                if scenario_type == "simple_qa":
                    if not qa_question:
                        return ("❌ 错误: 问题不能为空",) + noop
                    config.update({
                        "question": qa_question,
                        "timeout": int(qa_timeout) if qa_timeout else 60
                    })
                elif scenario_type == "code_execution":
                    if not code_script and not code_code:
                        return ("❌ 错误: 脚本或代码不能为空",) + noop
                    config.update({
                        "script": code_script if code_script else "",
                        "code": code_code if code_code else "",
                        "timeout": int(code_timeout) if code_timeout else 300
                    })

                scenario_id = scenario_manager.create_scenario(
                    scenario_type=scenario_type,
                    name=scenario_name,
                    description=scenario_desc,
                    config=config,
                )

                return (f"✅ 已创建: {scenario_id}",) + noop

            except Exception as e:
                return (f"❌ 错误: {str(e)}",) + noop

        def show_create_view():
            """Show create view and hide list"""
            return gr.update(visible=False), gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

        def show_clone_view(scenario_id):
            """
            Open the create view pre-filled from an existing scenario's config.
            Maps stored config (agent_roles + type-specific fields) onto the
            create form so the user can tweak and re-create.
            """
            # Defaults: no-op (keeps current UI state when trigger is empty)
            list_upd = gr.update()
            create_upd = gr.update()
            action_upd = gr.update()
            detail_upd = gr.update()
            trigger_val = gr.update()
            type_val = gr.update()
            name_val = gr.update()
            desc_val = gr.update()
            sched_val = gr.update()
            count_val = gr.update()
            role_vals = [gr.update() for _ in range(20)]
            qa_q, qa_t = gr.update(), gr.update()
            code_s, code_c, code_t = gr.update(), gr.update(), gr.update()
            status = gr.update()

            if not scenario_id:
                return (list_upd, create_upd, action_upd, detail_upd,
                        trigger_val, type_val, name_val, desc_val, sched_val, count_val) + \
                       tuple(role_vals) + (qa_q, qa_t, code_s, code_c, code_t, status)

            scenario_repo = ScenarioRepository()
            scenario = scenario_repo.find_by_scenario_id(scenario_id)
            if not scenario:
                status = f"❌ 克隆失败: 场景未找到 {scenario_id}"
                return (gr.update(visible=False), gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
                        "", gr.update(), gr.update(), gr.update(), gr.update(), gr.update()) + \
                       tuple(role_vals) + (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), status)

            try:
                config = json.loads(scenario.config) if scenario.config else {}
            except (json.JSONDecodeError, TypeError):
                config = {}

            agent_roles = config.get("agent_roles", {}) or {}
            sched = agent_roles.get("scheduling_agent", {}).get("role", "")
            exec_agents = agent_roles.get("execution_agents", []) or []

            # Only pre-fill types the create form supports; fall back to simple_qa
            stype = scenario.scenario_type
            if stype not in SCENARIO_REGISTRY:
                stype = "simple_qa"

            count = max(1, min(len(exec_agents), 10))
            role_vals = []
            for i in range(10):
                if i < len(exec_agents):
                    role_vals.append(exec_agents[i].get("name", ""))
                    role_vals.append(exec_agents[i].get("role", ""))
                else:
                    role_vals.extend(["", ""])

            return (gr.update(visible=False), gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
                    "",
                    stype,
                    (scenario.name or "") + " (克隆)",
                    scenario.description or "",
                    sched,
                    count) + \
                   tuple(role_vals) + (
                       config.get("question", ""),
                       config.get("timeout", 60),
                       config.get("script", ""),
                       config.get("code", ""),
                       config.get("timeout", 300),
                       f"📋 已从场景 `{scenario_id[:8]}` 克隆配置，确认后点击「创建场景」",
                   )

        def hide_create_view():
            """Hide create view and show list"""
            # Reset all role inputs to default values
            role_reset_values = []
            for i in range(10):
                if i == 0:
                    role_reset_values.extend(["代码执行专家", "你是一个代码执行专家，负责执行代码并返回结果。"])
                else:
                    role_reset_values.extend(["", ""])

            return (gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
                    "", "simple_qa", "", "",
                    "你是一个任务调度专家，负责分析用户目标，将其分解为可执行的子任务，并协调执行 Agent 完成任务。",
                    1) + tuple(role_reset_values) + ("", 60, "", "", 300, "")

        def _start_guard(status):
            """
            Pre-flight check before starting a scenario.

            Only `initializing` can transition to `running` (see
            SCENARIO_STATE_MACHINE). Block re-start of running scenarios
            (double-click) and terminal scenarios (need re-creation) with a
            friendly message instead of a raw state-machine error.

            Returns (blocked: bool, msg: str). blocked=False → proceed.
            """
            if not status:
                return True, "❌ 场景未找到"
            state = status["state"]
            if state in ("running", "initializing"):
                return True, f"⏳ 场景正在运行中（{state}），无需重复启动"
            if state in ("completed", "failed", "cancelled"):
                return True, f"🔒 场景已结束（{state}），无法重启，请新建或克隆场景"
            return False, ""

        def show_action_view(trigger_value):
            """Show action view and execute action"""
            if trigger_value:
                scenario_id, action = trigger_value.split('@') if '@' in trigger_value else (trigger_value, '')
                status = scenario_manager.get_scenario_status(scenario_id)
                status_text = f"**当前状态**: {status['state'] if status else '未知'}"

                # Auto-execute action
                result_msg = ""
                if action == "start" and status:
                    blocked, msg = _start_guard(status)
                    if blocked:
                        result_msg = msg
                    else:
                        scenario_type = status["scenario_type"]
                        cls = SCENARIO_REGISTRY.get(scenario_type)
                        if cls:
                            instance = cls()
                            ok = scenario_manager.start_scenario(scenario_id, instance)
                            result_msg = f"✅ 已启动: {scenario_id}" if ok else f"❌ 启动失败: {scenario_id}"
                        else:
                            result_msg = f"❌ 未知场景类型: {scenario_type}"
                elif action == "stop":
                    ok = scenario_manager.stop_scenario(scenario_id)
                    result_msg = f"✅ 已停止: {scenario_id}" if ok else f"❌ 停止失败: {scenario_id}"

                return (gr.update(visible=False), gr.update(visible=False),
                        gr.update(visible=True), gr.update(visible=False),
                        scenario_id, status_text + ("\n\n" + result_msg if result_msg else ""))
            return gr.update(), gr.update(), gr.update(), gr.update(), "", ""

        def hide_action_view():
            """Hide action view and show list"""
            return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "", ""

        def _build_message_history(scenario_id, trace_id):
            """Build unified message timeline, sorted by time desc (latest on top).

            Delegates to the shared module-level builder (also used by the HTML
            export) and adapts to the 5-column row list the Dataframe expects.
            """
            entries = build_message_history(scenario_id, trace_id)
            rows = [
                [e["time"], e["type"], e["sender"], e["receiver"], e["content"]]
                for e in entries
            ]
            rows.sort(key=lambda r: r[0], reverse=True)
            return rows

        def show_scenario_detail(scenario_id):
            """Load scenario detail + message history."""
            if not scenario_id:
                return {}, [], ""

            status = scenario_manager.get_scenario_status(scenario_id)
            if not status:
                return {"error": "场景未找到"}, [], ""

            # Extract trace_id from scenario context
            scenario_repo = ScenarioRepository()
            scenario = scenario_repo.find_by_scenario_id(scenario_id)
            trace_id = ""
            if scenario and scenario.context:
                try:
                    ctx = json.loads(scenario.context)
                    trace_id = ctx.get("trace_id", "")
                except (json.JSONDecodeError, TypeError):
                    pass

            history = _build_message_history(scenario_id, trace_id)
            return status, history, scenario_id

        def show_detail_view(scenario_id):
            """Show detail view and hide list"""
            if scenario_id:
                detail, history, detail_id = show_scenario_detail(scenario_id)
                return (gr.update(visible=False), gr.update(visible=False),
                        gr.update(visible=False), gr.update(visible=True),
                        detail_id, detail, history)
            return gr.update(), gr.update(), gr.update(), gr.update(), "", {}, []

        def hide_detail_view():
            """Hide detail view and show list"""
            return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), "", {}, []

        # ═══════════════════════════════════════════════════════════════
        # Event handlers
        # ═══════════════════════════════════════════════════════════════

        # Refresh button
        refresh_btn.click(
            fn=lambda: gr.update(value=str(random.randint(1, 10000))),
            outputs=[refresh_trigger]
        )

        # New scenario button
        new_scenario_btn.click(
            show_create_view,
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content]
        )

        # Create view handlers
        scenario_type.change(
            fn=toggle_config_fields,
            inputs=[scenario_type],
            outputs=[simple_qa_config, code_execution_config]
        )

        create_btn.click(
            fn=create_scenario,
            inputs=[scenario_type, scenario_name, scenario_desc,
                    scheduling_agent_role, execution_roles_count] +
                    [item for r in role_inputs for item in [r["name"], r["role"]]] +
                    [qa_question, qa_timeout, code_script, code_code, code_timeout],
            outputs=[status_msg, scenario_type, scenario_name, scenario_desc,
                     scheduling_agent_role, execution_roles_count] +
                    [item for r in role_inputs for item in [r["name"], r["role"]]] +
                    [qa_question, qa_timeout, code_script, code_code, code_timeout]
        ).then(
            fn=hide_create_view,
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content,
                     show_create_trigger, scenario_type, scenario_name, scenario_desc,
                     scheduling_agent_role, execution_roles_count] +
                    [item for r in role_inputs for item in [r["name"], r["role"]]] +
                    [qa_question, qa_timeout, code_script, code_code, code_timeout, status_msg]
        )

        cancel_create_btn.click(
            fn=hide_create_view,
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content,
                     show_create_trigger, scenario_type, scenario_name, scenario_desc,
                     scheduling_agent_role, execution_roles_count] +
                    [item for r in role_inputs for item in [r["name"], r["role"]]] +
                    [qa_question, qa_timeout, code_script, code_code, code_timeout, status_msg]
        )

        # Add/Remove execution role buttons
        add_role_btn.click(
            fn=add_execution_role,
            inputs=[execution_roles_count],
            outputs=[execution_roles_count] + [r["group"] for r in role_inputs]
        )

        remove_role_btn.click(
            fn=remove_execution_role,
            inputs=[execution_roles_count],
            outputs=[execution_roles_count] + [r["group"] for r in role_inputs]
        )

        # Action view handlers
        def execute_start(scenario_id):
            """Start a scenario"""
            if not scenario_id:
                return "", "❌ 请输入场景ID"

            status = scenario_manager.get_scenario_status(scenario_id)
            blocked, msg = _start_guard(status)
            if blocked:
                return scenario_id, msg

            scenario_type = status["scenario_type"]
            cls = SCENARIO_REGISTRY.get(scenario_type)
            if not cls:
                return scenario_id, f"❌ 未知场景类型: {scenario_type}"

            instance = cls()
            ok = scenario_manager.start_scenario(scenario_id, instance)
            msg = f"✅ 已启动: {scenario_id}" if ok else f"❌ 启动失败: {scenario_id}"
            return scenario_id, msg

        def execute_stop(scenario_id):
            """Stop a scenario"""
            if not scenario_id:
                return "", "❌ 请输入场景ID"

            ok = scenario_manager.stop_scenario(scenario_id)
            msg = f"✅ 已停止: {scenario_id}" if ok else f"❌ 停止失败: {scenario_id}"
            return scenario_id, msg

        start_btn.click(
            fn=execute_start,
            inputs=[action_scenario_id],
            outputs=[action_scenario_id, action_status_msg]
        )

        stop_btn.click(
            fn=execute_stop,
            inputs=[action_scenario_id],
            outputs=[action_scenario_id, action_status_msg]
        )

        back_to_list_btn.click(
            fn=hide_action_view,
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content,
                     show_action_trigger, action_status_msg]
        )

        # Detail view handlers
        back_from_detail_btn.click(
            fn=hide_detail_view,
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content,
                     show_detail_trigger, scenario_detail, message_history]
        )

        refresh_detail_btn.click(
            lambda scenario_id: show_scenario_detail(scenario_id),
            inputs=[detail_id_input],
            outputs=[scenario_detail, message_history, detail_id_input]
        )

        # Export scenario to a self-contained offline HTML file
        export_html_btn.click(
            fn=write_export_file,
            inputs=[detail_id_input],
            outputs=[export_html_btn]
        )

        detail_refresh_timer.tick(
            lambda scenario_id: show_scenario_detail(scenario_id),
            inputs=[detail_id_input],
            outputs=[scenario_detail, message_history, detail_id_input]
        )

        # Trigger-based view switching
        show_detail_trigger.change(
            fn=show_detail_view,
            inputs=[show_detail_trigger],
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content,
                     detail_id_input, scenario_detail, message_history]
        )

        show_action_trigger.change(
            fn=show_action_view,
            inputs=[show_action_trigger],
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content,
                     action_scenario_id, action_status_msg]
        )

        show_clone_trigger.change(
            fn=show_clone_view,
            inputs=[show_clone_trigger],
            outputs=[scenario_list_content, scenario_create_content, scenario_action_content, scenario_detail_content,
                     show_create_trigger, scenario_type, scenario_name, scenario_desc,
                     scheduling_agent_role, execution_roles_count] +
                    [item for r in role_inputs for item in [r["name"], r["role"]]] +
                    [qa_question, qa_timeout, code_script, code_code, code_timeout, status_msg]
        )

    return page
