"""Status page — system health monitoring dashboard."""

from __future__ import annotations

import asyncio

from loguru import logger
from nicegui import ui

from app.shared.backend.maafw_runner import run_node
from app.shared.backend.status import (
    check_mitm_proxy,
    check_mitm_receiver,
    check_user_status,
)
from app.task_queue import TaskStatus, get_task_queue
from app.web.components.nav import nav
from app.web.components.status import status_card


def create() -> None:
    """Register the /status page on the current NiceGUI app."""

    @ui.page("/status")
    async def status_page() -> None:
        ui.add_head_html(
            "<style>@media(min-width:993px){.drawer-toggle{display:none!important}}</style>"
        )
        drawer = ui.left_drawer(top_corner=True, bottom_corner=True).props("bordered")
        with drawer:
            nav("/status")

        with ui.column().classes("w-full max-w-2xl mx-auto p-6 gap-4"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.button(icon="menu", on_click=drawer.toggle).props(
                        "flat dense round"
                    ).classes("drawer-toggle")
                    ui.label("系统状态").classes("text-xl font-bold")
                refresh_btn = ui.button("刷新", icon="refresh").props(
                    "color=primary outline size=sm"
                )

            cards_ele = ui.column().classes("w-full gap-4")

            async def _refresh() -> None:
                logger.debug("Refreshing status cards")
                refresh_btn.disable()
                try:
                    user, proxy, receiver = await asyncio.gather(
                        asyncio.to_thread(check_user_status),
                        asyncio.to_thread(check_mitm_proxy),
                        asyncio.to_thread(check_mitm_receiver),
                    )
                except Exception:
                    logger.exception("Status refresh failed")
                    raise
                finally:
                    refresh_btn.enable()

                logger.debug(
                    "Status refreshed: user_ok={} proxy_ok={} receiver_ok={}",
                    user.has_key and bool(user.ali_id),
                    proxy.reachable,
                    receiver.reachable,
                )

                cards_ele.clear()
                with cards_ele:
                    status_card(
                        title="用户状态",
                        icon="person",
                        ok=user.has_key and bool(user.ali_id),
                        data=user,
                        rows=[
                            ("fingerprint", "Ali ID", user.ali_id or "未检测到"),
                            ("key", "DB Key", f"{user.source}" + (" ✓" if user.has_key else " ✗")),
                            ("storage", "加密 DB", "存在" if user.db_exists else "不存在"),
                        ],
                    )

                    status_card(
                        title="MITM 代理",
                        icon="shield",
                        ok=proxy.reachable,
                        data=proxy,
                        rows=[
                            ("router", "地址", f"{proxy.host}:{proxy.port}"),
                            ("speed", "延迟", f"{proxy.latency_ms} ms" if proxy.latency_ms is not None else "—"),
                            *([("error_outline", "错误", proxy.error)] if proxy.error else []),
                        ],
                    )

                    status_card(
                        title="MITM Receiver",
                        icon="sensors",
                        ok=receiver.reachable,
                        data=receiver,
                        rows=[
                            ("router", "地址", f"{receiver.host}:{receiver.port}"),
                            ("speed", "延迟", f"{receiver.latency_ms} ms" if receiver.latency_ms is not None else "—"),
                            *([("error_outline", "错误", receiver.error)] if receiver.error else []),
                        ],
                    )

            refresh_btn.on("click", lambda: _refresh())
            await _refresh()

            # MaaFW test card
            with ui.card().classes("w-full p-5 gap-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("precision_manufacturing").classes("text-xl")
                    ui.label("MaaFW 测试").classes("text-base font-semibold")

                test_status = ui.label("").classes("text-sm")

                async def _test_node(entry: str, label: str) -> None:
                    tq = get_task_queue()
                    snapshot = tq.enqueue(
                        fn=lambda e=entry: run_node(e),
                        description=f"MaaFW 测试: {label}",
                    )
                    test_status.text = f"正在执行 {label}…"
                    test_status.classes(replace="text-sm text-gray-500")

                    while True:
                        await asyncio.sleep(0.5)
                        snap = tq.get(snapshot.task_id)
                        if snap is None or snap.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
                            break

                    if snap and snap.result:
                        ok, msg = snap.result
                        if ok:
                            logger.debug("MaaFW test passed: {} — {}", label, msg)
                            test_status.text = f"{label}: {msg}"
                            test_status.classes(replace="text-sm text-green-600")
                        else:
                            logger.warning("MaaFW test failed: {} — {}", label, msg)
                            test_status.text = f"{label}: {msg}"
                            test_status.classes(replace="text-sm text-red-600")

                with ui.row().classes("gap-2"):
                    ui.button(
                        "测试 ChatInput 操作",
                        icon="edit",
                        on_click=lambda: asyncio.create_task(_test_node("ChatInput_GoToInput", "ChatInput")),
                    ).props("outline size=sm")
                    ui.button(
                        "测试 ContactSearch 操作",
                        icon="search",
                        on_click=lambda: asyncio.create_task(_test_node("ContactSearch_GoToSearch", "ContactSearch")),
                    ).props("outline size=sm")

            # MaaFW task queue status
            tq = get_task_queue()

            @ui.refreshable
            def queue_status_card() -> None:
                snapshots = tq.all_snapshots()
                with ui.card().classes("w-full p-5 gap-3"):
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("queue").classes("text-xl")
                            ui.label("MaaFW 任务队列").classes("text-base font-semibold")
                        pending = sum(1 for s in snapshots if s.status == TaskStatus.PENDING)
                        running = sum(1 for s in snapshots if s.status == TaskStatus.RUNNING)
                        ui.badge(f"{pending} 等待 / {running} 执行中").props("outline")

                    if not snapshots:
                        ui.label("暂无任务").classes("text-sm text-gray-400")
                    else:
                        for snap in snapshots[:20]:
                            with ui.row().classes("items-center gap-2 w-full"):
                                if snap.status == TaskStatus.PENDING:
                                    ui.icon("schedule", color="grey").classes("text-sm")
                                elif snap.status == TaskStatus.RUNNING:
                                    ui.spinner(size="xs", color="primary")
                                elif snap.status == TaskStatus.SUCCEEDED:
                                    ui.icon("check_circle", color="green").classes("text-sm")
                                elif snap.status == TaskStatus.FAILED:
                                    ui.icon("error", color="red").classes("text-sm")
                                ui.label(snap.description).classes("text-sm flex-grow")
                                ui.label(snap.status.value).classes("text-xs text-gray-500")

            queue_status_card()
            queue_timer = ui.timer(2.0, lambda: queue_status_card.refresh())
            ui.context.client.on_disconnect(lambda _client: queue_timer.cancel())
