"""AI suggestion dialog — generates reply suggestions for a conversation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from nicegui import ui

from app.shared.backend.chat_ai import generate_reply_suggestions
from app.web.chat_presenter import conversation_for_suggestions


async def open_suggestion_dialog(
    conv,
    resolver,
    suggestion_state: dict,
    on_fill: Callable[[str], None],
) -> None:
    """Open a dialog that generates AI reply suggestions for *conv*.

    The dialog shows a loading spinner while generating, then displays each
    suggestion item with a "填充到输入框" button that calls *on_fill* with
    the reply text and closes the dialog.
    """
    suggestion_state["loading"] = True
    suggestion_state["error"] = None
    suggestion_state["items"] = []
    suggestion_state["buyer_language"] = ""

    with ui.dialog() as dialog, ui.card().classes("w-[520px] max-w-[90vw]"):
        ui.label("AI 建议").classes("text-base font-semibold")

        loading_ele = ui.row().classes("items-center gap-2 py-2")
        with loading_ele:
            ui.spinner(size="sm", color="primary")
            ui.label("生成中…").classes("text-xs text-gray-500")

        error_ele = ui.label().classes("text-xs text-red-600 py-1")
        error_ele.visible = False

        items_ele = ui.column().classes("w-full gap-2")

        async def _generate() -> None:
            loading_ele.visible = True
            error_ele.visible = False

            try:
                convo = conversation_for_suggestions(conv.messages, resolver)
                result = await asyncio.to_thread(
                    generate_reply_suggestions, convo
                )

                suggestion_state["items"] = list(result.items[:3])
                if result.buyer_language:
                    suggestion_state["buyer_language"] = result.buyer_language
            except Exception as exc:
                suggestion_state["error"] = str(exc)

            loading_ele.visible = False

            err = suggestion_state.get("error")
            if isinstance(err, str) and err.strip():
                error_ele.text = f"生成失败：{err.strip()}"
                error_ele.visible = True
                return

            buyer_lang = suggestion_state.get("buyer_language", "")

            items_ele.clear()
            with items_ele:
                for item in suggestion_state["items"]:
                    zh_text = getattr(item, "zh", "") or ""
                    reply_text = getattr(item, "reply", "") or ""
                    if not zh_text.strip() and not reply_text.strip():
                        continue

                    with ui.card().props("flat bordered").classes("w-full p-3"):
                        with ui.column().classes("w-full gap-1"):
                            if zh_text.strip():
                                with ui.column().classes("gap-0"):
                                    ui.label("中文").classes(
                                        "text-xs text-gray-400 font-medium"
                                    )
                                    ui.label(zh_text.strip()).classes("text-sm")
                            if reply_text.strip():
                                with ui.column().classes("gap-0"):
                                    lang_label = (
                                        buyer_lang.strip()
                                        if isinstance(buyer_lang, str)
                                        and buyer_lang.strip()
                                        else "买家语言"
                                    )
                                    ui.label(lang_label).classes(
                                        "text-xs text-gray-400 font-medium"
                                    )
                                    ui.label(reply_text.strip()).classes("text-sm")
                        ui.separator()
                        with ui.row().classes("w-full justify-end"):
                            def _fill(text=reply_text) -> None:
                                on_fill(text)
                                dialog.close()

                            ui.button(
                                "填充到输入框", icon="content_paste"
                            ).props("size=sm flat color=primary").on(
                                "click", _fill
                            )

    dialog.open()
    await asyncio.sleep(0)
    await _generate()
