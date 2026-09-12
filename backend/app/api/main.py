from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routers import agent, conversations, messages, self, status


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
    return app


app = create_app()
