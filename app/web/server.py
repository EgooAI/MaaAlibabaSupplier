"""NiceGUI web server — entry point for the web interface."""

from __future__ import annotations

import os

from nicegui import app, ui

from app.shared.utils.env import load_workdir_env


def run() -> None:
    load_workdir_env()

    host = os.environ.get("MAA_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("MAA_WEB_PORT", "8787"))

    # Register pages — importing triggers @ui.page decorators.
    import app.web.pages.agent as agent_page
    import app.web.pages.card as card_page
    import app.web.pages.chat as chat_page
    import app.web.pages.index as index_page
    import app.web.pages.status as status_page

    index_page.create()
    card_page.create()
    chat_page.create()
    status_page.create()
    agent_page.create()

    ui.run(host=host, port=port, title="Alibaba Supplier Agent", language="zh-CN", show=False, reload=False)


if __name__ == "__main__":
    run()
