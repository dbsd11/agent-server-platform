# Home page - Dashboard + 场景管理 Chatbot 浮窗
#
# 主体：恢复改造前的静态看板（精简指标 + 最近事件 + 系统信息）
# 浮窗：左侧可折叠的场景管理助手，默认折叠；草稿预览仅在有草稿时显示
#
# 大脑逻辑见 pages.home.assistant；UI 仅做 Gradio 装配。
from datetime import datetime

import gradio as gr

from database.repositories.agent_repository import AgentRepository
from database.repositories.task_repository import TaskRepository
from database.repositories.scenario_repository import ScenarioRepository
from database.repositories.event_repository import EventRepository
from pages.home import assistant


# 浮窗样式：左下悬浮按钮(FAB) + 居中模态浮窗
_DRAWER_CSS = """
/* 左下悬浮按钮 */
.fab {
  position: fixed !important;
  left: 24px; bottom: 24px;
  z-index: 9999;
  width: 56px !important;
  height: 56px !important;
  border-radius: 50% !important;
  padding: 0 !important;
  font-size: 24px !important;
  box-shadow: 0 4px 14px rgba(0,0,0,0.25) !important;
}
/* 模态遮罩层（gradio Column 渲染为 div） */
.modal-backdrop {
  position: fixed !important;
  inset: 0 !important;
  z-index: 9998;
  background: rgba(15, 23, 42, 0.45);
  display: flex !important;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
/* 模态卡片 */
.modal-card {
  width: 560px;
  max-width: 96vw;
  max-height: 88vh;
  overflow-y: auto;
  background: #ffffff;
  border-radius: 14px;
  box-shadow: 0 12px 36px rgba(0,0,0,0.3);
  padding: 18px 20px !important;
}
"""


def create_page(global_state_component):
    """Create home page — Dashboard + 场景管理 Chatbot 浮窗(modal)"""
    with gr.Blocks() as page:
        # 嵌套 Blocks 的 css= 不会注入到页面，改用 <style> 标签确保生效
        gr.HTML(f"<style>{_DRAWER_CSS}</style>")
        gr.Markdown("# Agent Server Platform - Dashboard")
        gr.Markdown("*Welcome to the universal agent-server platform*")

        # ── 主体：静态看板（恢复改造前内容）─────────────────────────────
        with gr.Row():
            with gr.Column():
                agent_count = gr.Number(label="Registered Agents", value=0, interactive=False)
            with gr.Column():
                task_count = gr.Number(label="Total Tasks", value=0, interactive=False)
            with gr.Column():
                running_tasks = gr.Number(label="Running Tasks", value=0, interactive=False)
            with gr.Column():
                scenario_count = gr.Number(label="Scenarios", value=0, interactive=False)

        gr.Markdown("## Recent Events")
        event_log = gr.Dataframe(
            headers=["Timestamp", "Event Type", "Data"],
            label="Recent Events",
            wrap=True,
        )

        with gr.Accordion("System Information", open=False):
            gr.Markdown(f"""
            **Platform Version**: 1.0.0
            **Database**: SQLite
            **Started**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

            **Architecture**:
            - Layer 1: Universal Agent Platform (Scheduling Agent, Execution Agent, A2A, Events, Watchdog)
            - Layer 2: Scenario Mode Agent Collaboration Platform
            """)

        # ── 浮窗：悬浮按钮 + 模态浮窗（默认折叠）─────────────────────────
        modal_open = gr.State(value=False)  # 模态显隐状态
        pending_state = gr.State(value=None)      # 当前草稿 spec（保存后仍保留，便于继续修改）
        saved_id_state = gr.State(value=None)     # 草稿绑定的已落库 scenario_id；None=新建
        dirty_state = gr.State(value=False)       # 草稿是否有未保存修改（驱动操作按钮显隐）

        # 左下悬浮按钮：点击弹出模态
        fab_btn = gr.Button("💬", elem_classes="fab")

        with gr.Column(visible=False, elem_classes="modal-backdrop") as modal_backdrop:
            with gr.Column(elem_classes="modal-card"):
                with gr.Row():
                    gr.Markdown("#### 场景管理助手")
                    close_btn = gr.Button("✕", size="sm", scale=0)
                gr.Markdown("*对话管理场景：查看/总结进行中的场景，或多轮对话创建新场景（确认后才落库）。*")
                chatbot = gr.Chatbot(
                    label="助手对话",
                    type="messages",
                    height=420,
                    placeholder="对我说：查看进行中的场景 / 帮我创建一个辩论场景…",
                )
                with gr.Row():
                    user_input = gr.Textbox(
                        placeholder="例如：列出所有场景；总结场景 <id>；帮我创建一个问答场景…",
                        scale=4, show_label=False, autofocus=True,
                    )
                    send_btn = gr.Button("发送", variant="primary", scale=1)
                with gr.Row():
                    clear_btn = gr.Button("🗑 清空对话")

                # 草稿预览 + 操作按钮：默认隐藏，仅在有未保存草稿时显示
                preview_md = gr.Markdown(assistant.EMPTY_PREVIEW, visible=False)
                with gr.Row(visible=False) as draft_actions:
                    confirm_btn = gr.Button("✅ 确认保存到数据库", variant="primary")
                    discard_btn = gr.Button("放弃草稿")

        # ── 数据加载 ────────────────────────────────────────────────────
        def load_stats():
            agents = AgentRepository().find_all()
            tasks = TaskRepository().find_all()
            running = TaskRepository().find_by_state("running")
            scenarios = ScenarioRepository().find_all()
            return len(agents), len(tasks), len(running), len(scenarios)

        def load_recent_events():
            events = EventRepository().find_recent(limit=10)
            return [[e.timestamp, e.event_type, e.data] for e in events]

        page.load(load_stats, outputs=[agent_count, task_count, running_tasks, scenario_count])
        page.load(load_recent_events, outputs=[event_log])

        # ── 浮窗折叠/展开 ───────────────────────────────────────────────
        def on_open():
            return True, gr.update(visible=True)

        def on_close():
            return False, gr.update(visible=False)

        fab_btn.click(on_open, None, [modal_open, modal_backdrop])
        close_btn.click(on_close, None, [modal_open, modal_backdrop])

        # ── 对话 ────────────────────────────────────────────────────────
        # 操作按钮（确认/放弃）仅在「有草稿且有未保存修改」时显示；
        # 保存后 pending 保留、dirty=False，按钮隐藏；再次修改 dirty=True 才出现。
        def _draft_updates(pending, dirty):
            show_actions = bool(pending) and dirty
            return (
                gr.update(visible=bool(pending), value=assistant.render_preview(pending)),
                gr.update(visible=show_actions),
            )

        def on_send(user_text, chatbot, pending, dirty, saved_id):
            user_text = (user_text or "").strip()
            if not user_text:
                pv, act = _draft_updates(pending, dirty)
                return chatbot, pending, dirty, saved_id, pv, act, gr.update()
            history = list(chatbot or [])
            history.append({"role": "user", "content": user_text})
            assistant_text, new_pending, _, new_saved_id = assistant.reply(
                user_text, history, pending, saved_id)
            history.append({"role": "assistant", "content": assistant_text})
            # 载入已有场景 或 AI 产出新 spec 都视为有未保存修改（相对数据库）
            changed = new_pending is not None and new_pending != pending
            loaded_existing = bool(new_saved_id) and (new_saved_id != saved_id)
            new_dirty = dirty or changed or loaded_existing
            pv, act = _draft_updates(new_pending, new_dirty)
            return history, new_pending, new_dirty, new_saved_id, pv, act, ""

        def on_clear():
            """清空对话：重置草稿、绑定与脏标记，开始全新会话。"""
            pv, act = _draft_updates(None, False)
            return [], None, None, False, pv, act, ""

        def on_confirm(chatbot, pending, saved_id, dirty):
            history = list(chatbot or [])
            if not pending or not dirty:
                history.append({"role": "assistant",
                                "content": "⚠️ 当前没有未保存的修改。先通过对话调整场景吧。"})
                pv, act = _draft_updates(pending, dirty)
                return history, pending, saved_id, dirty, pv, act
            # saved_id 非空 → 更新已有场景；否则新建
            scenario_id, err = assistant.save_scene(pending, scenario_id=saved_id)
            if scenario_id:
                action = "已更新" if saved_id else "已创建"
                history.append({"role": "assistant",
                                "content": (f"✅ 场景{action}！`scenario_id={scenario_id}`\n\n"
                                            "可继续对话修改（会更新该场景），或到「Scenario Dashboard」启动。")})
                pv, act = _draft_updates(pending, False)
                return history, pending, scenario_id, False, pv, act
            history.append({"role": "assistant",
                            "content": (f"❌ 保存失败：{err}\n草稿已保留，可继续调整后再确认。")})
            pv, act = _draft_updates(pending, dirty)
            return history, pending, saved_id, dirty, pv, act

        def on_discard(chatbot, pending, saved_id):
            history = list(chatbot or [])
            if saved_id:
                # 已落库场景：放弃本次修改，回退到数据库中的版本
                reverted = assistant.load_scenario_spec(saved_id)
                msg = "已放弃本次修改，回退到已保存的版本。" if reverted else "已放弃本次修改。"
                history.append({"role": "assistant", "content": msg})
                pv, act = _draft_updates(reverted, False)
                return history, reverted, saved_id, False, pv, act
            # 全新草稿：直接丢弃
            history.append({"role": "assistant", "content": "已放弃当前草稿。"})
            pv, act = _draft_updates(None, False)
            return history, None, None, False, pv, act

        send_outputs = [chatbot, pending_state, dirty_state, saved_id_state, preview_md, draft_actions, user_input]
        send_inputs = [user_input, chatbot, pending_state, dirty_state, saved_id_state]
        user_input.submit(on_send, send_inputs, send_outputs)
        send_btn.click(on_send, send_inputs, send_outputs)
        clear_btn.click(on_clear, None,
                        [chatbot, pending_state, saved_id_state, dirty_state, preview_md, draft_actions, user_input])
        confirm_btn.click(on_confirm, [chatbot, pending_state, saved_id_state, dirty_state],
                          [chatbot, pending_state, saved_id_state, dirty_state, preview_md, draft_actions])
        discard_btn.click(on_discard, [chatbot, pending_state, saved_id_state],
                          [chatbot, pending_state, saved_id_state, dirty_state, preview_md, draft_actions])

    return page
