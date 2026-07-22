# Execution Servers page — read-only view of execution-agent servers
# connected to the backend over WebSocket.
import json
import gradio as gr

from database.repositories.execution_server_repository import ExecutionServerRepository


def _parse_env(env_info):
    """Parse the env_info blob into (host_dict, commands_dict).

    Defensive against both the current nested shape ({commands, host}) and
    a legacy flat {cmd: bool} blob, so the page never crashes on old rows.
    """
    if not env_info:
        return {}, {}
    try:
        env = json.loads(env_info) if isinstance(env_info, str) else env_info
    except (json.JSONDecodeError, TypeError):
        return {}, {}

    if isinstance(env, dict) and "commands" in env and "host" in env:
        return env.get("host", {}) or {}, env.get("commands", {}) or {}
    # legacy flat shape: treat the whole thing as commands
    if isinstance(env, dict):
        return {}, env
    return {}, {}


def _command_summary(commands):
    """Compact 'bash✓ python✓ codex✗ ...' string."""
    if not commands:
        return "—"
    return "  ".join(f"{k}{'✓' if v else '✗'}" for k, v in commands.items())


def _load_servers():
    """Return server rows for the list dataframe + summary counts."""
    try:
        servers = ExecutionServerRepository().list_all()
    except Exception as e:
        return [], f"❌ 加载失败: {e}"

    rows = []
    connected = 0
    running = 0
    for s in servers:
        if s.connected:
            connected += 1
        if s.status == "running":
            running += 1

        host, commands = _parse_env(s.env_info)
        hb = str(s.last_heartbeat)[5:19] if s.last_heartbeat else "—"

        rows.append([
            s.server_id,
            s.name or "—",
            s.status,
            "✅ 在线" if s.connected else "❌ 离线",
            f"{s.running_count}/{s.total_quota}",
            host.get("hostname", "—"),
            host.get("ip", "—"),
            host.get("os", "—"),
            _command_summary(commands),
            hb,
        ])

    summary = (f"**共 {len(rows)} 个执行服务器** · 在线 {connected} · "
               f"运行中 {running}")
    return rows, summary


def _server_detail(server_id):
    """Return full server record as a dict for the JSON detail view."""
    if not server_id:
        return {}
    s = ExecutionServerRepository().find_by_server_id(server_id)
    if not s:
        return {"error": f"未找到服务器: {server_id}"}

    host, commands = _parse_env(s.env_info)

    return {
        "server_id": s.server_id,
        "name": s.name,
        "status": s.status,
        "connected": bool(s.connected),
        "total_quota": s.total_quota,
        "running_count": s.running_count,
        "host": host,
        "commands": commands,
        "last_heartbeat": s.last_heartbeat.isoformat() if s.last_heartbeat else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def create_page(global_state_component):
    """Create the execution-servers page."""
    with gr.Blocks() as page:
        gr.Markdown("# 执行 Agent 服务器 (Execution Servers)")
        gr.Markdown("*通过 WebSocket 接入的远程执行 Agent 服务器，状态由心跳实时同步*")

        # ZONE A: list
        with gr.Column(elem_id="server_list_content") as server_list_content:
            summary_md = gr.Markdown("")
            with gr.Row():
                refresh_btn = gr.Button("🔄 刷新", variant="secondary")

            server_list = gr.Dataframe(
                headers=["服务器ID", "名称", "状态", "连接", "运行/配额",
                         "宿主机", "IP", "操作系统", "命令支持", "最近心跳"],
                label="执行 Agent 服务器列表",
                wrap=True,
                interactive=False,
            )

            show_detail_trigger = gr.Textbox(value="", visible=False)

        # ZONE B: detail
        with gr.Column(elem_id="server_detail_content", visible=False) as server_detail_content:
            gr.Markdown("## 服务器详情")
            server_id_display = gr.Textbox(label="服务器ID", interactive=False)
            server_detail = gr.JSON(label="详细信息")

            with gr.Row():
                back_btn = gr.Button("返回列表", variant="secondary")
                refresh_detail_btn = gr.Button("刷新", variant="primary")

        # Auto-refresh every 5s (status/heartbeat change live)
        refresh_timer = gr.Timer(value=5, render=False)

        def _load_both():
            rows, summary = _load_servers()
            return rows, summary

        def _show_detail_view(server_id):
            if server_id:
                return (gr.update(visible=False), gr.update(visible=True),
                        server_id, _server_detail(server_id))
            return gr.update(), gr.update(), "", {}

        def _hide_detail():
            return gr.update(visible=True), gr.update(visible=False), "", {}

        def _handle_select(evt: gr.SelectData):
            # Only react to clicks on the first column (server_id)
            if evt.index[1] == 0:
                return evt.value
            return None

        # wiring
        refresh_timer.tick(_load_both, outputs=[server_list, summary_md])
        refresh_btn.click(_load_both, outputs=[server_list, summary_md])
        server_list.select(fn=_handle_select, outputs=[show_detail_trigger])
        show_detail_trigger.change(
            _show_detail_view,
            inputs=[show_detail_trigger],
            outputs=[server_list_content, server_detail_content,
                     server_id_display, server_detail],
        )
        back_btn.click(
            _hide_detail,
            outputs=[server_list_content, server_detail_content,
                     show_detail_trigger, server_detail],
        )
        refresh_detail_btn.click(
            _server_detail,
            inputs=[server_id_display],
            outputs=[server_detail, server_id_display],
        )

        page.load(_load_both, outputs=[server_list, summary_md])

    return page
