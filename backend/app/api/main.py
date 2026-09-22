from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.app.api.account_scope import is_account_path, request_epoch, response_epoch
from backend.app.api.auth import LOGIN_LIMITER, AuthMiddleware, SessionStore, validate_auth_config
from backend.app.api.envelope import AppError, err, user_message
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.utils.settings import FRONTEND_DEV_ORIGINS, resolve_repo_root
from backend.app.shared.utils.log_context import bind_log_context

from backend.app.api.routers import agent, app as app_router, auth, conversations, messages, outbox, self, settings, status


def create_app() -> FastAPI:
    store = SessionStore(validate_auth_config())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(store.initialize)
        yield

    app = FastAPI(title="MaaAlibabaSupplier API", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.auth_store = store
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.state.request_id
        if is_account_path(request.url.path):
            context = await run_in_threadpool(get_account_context)
            supplied = request.headers.get("X-Account-Epoch")
            expected = supplied if supplied is not None else context.epoch
            if (request.method not in {"GET", "HEAD", "OPTIONS"} and not supplied) or expected != context.epoch:
                response = JSONResponse(status_code=409, content=err("请求缺少账号版本或账号已切换，请刷新后重试。"))
                response.headers["X-Account-Epoch"] = context.epoch
            else:
                token = request_epoch.set(expected)
                marker = [expected]
                response_token = response_epoch.set(marker)
                try:
                    try:
                        with bind_log_context(account_epoch=context.epoch):
                            response = await call_next(request)
                    except Exception:
                        logger.exception("Unhandled account API error")
                        response = JSONResponse(status_code=500, content=err("服务器内部错误", 1))
                    current = await run_in_threadpool(get_account_context)
                    if current.epoch != marker[0]:
                        response = JSONResponse(status_code=409, content=err("处理请求期间账号已切换，请刷新后重试。"))
                    response.headers["X-Account-Epoch"] = current.epoch
                finally:
                    response_epoch.reset(response_token)
                    request_epoch.reset(token)
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Account-Epoch", "X-Request-ID", "Retry-After"],
    )
    app.add_middleware(AuthMiddleware, store=store, limiter=LOGIN_LIMITER)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        # The error response is already recorded by AuthMiddleware's http.access
        # (status, route, duration); no separate rejection record is emitted.
        return JSONResponse(status_code=exc.status_code, content=err(user_message(exc.message), exc.code))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=err("请求参数错误", 1))

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=err(str(exc.detail), 1), headers=exc.headers)

    @app.exception_handler(OverflowError)
    async def overflow_error_handler(request: Request, exc: OverflowError) -> JSONResponse:
        return JSONResponse(status_code=429, content=err(str(exc) or "任务队列已满", 1))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "-")
        with bind_log_context(request_id=request_id):
            logger.opt(exception=exc).error("Unhandled API error")
        return JSONResponse(status_code=500, content=err("服务器内部错误", 1))

    app.include_router(auth.router, tags=["auth"])
    app.include_router(self.router, tags=["self"])
    app.include_router(status.router, tags=["status"])
    app.include_router(conversations.router, tags=["conversations"])
    app.include_router(messages.router, tags=["messages"])
    app.include_router(outbox.router, tags=["outbox"])
    app.include_router(agent.router, tags=["agent"])
    app.include_router(app_router.router, tags=["app"])
    app.include_router(settings.router, tags=["settings"])

    # Serve the exported frontend (frontend/out) from the same origin when it
    # has been built with NEXT_EXPORT=1; dev uses the Next.js server instead.
    frontend_out = resolve_repo_root() / "frontend" / "out"
    if frontend_out.is_dir():
        app.mount("/", StaticFiles(directory=frontend_out, html=True), name="frontend")
    return app


app = create_app()
