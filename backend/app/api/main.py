from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.routers import agent, app as app_router, conversations, messages, self, status


def create_app() -> FastAPI:
    app = FastAPI(title="MaaAlibabaSupplier API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(self.router)
    app.include_router(status.router)
    app.include_router(conversations.router)
    app.include_router(messages.router)
    app.include_router(agent.router)
    app.include_router(app_router.router)

    # Serve the exported frontend (frontend/out) from the same origin when it
    # has been built with NEXT_EXPORT=1; dev uses the Next.js server instead.
    frontend_out = Path(__file__).resolve().parents[3] / "frontend" / "out"
    if frontend_out.is_dir():
        app.mount("/", StaticFiles(directory=frontend_out, html=True), name="frontend")
    return app


app = create_app()
