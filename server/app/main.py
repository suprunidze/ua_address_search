"""UA Address Search proxy: one replica, persistent SQLite volume."""

from contextlib import asynccontextmanager
import time
from collections import OrderedDict
import uuid
import re
from typing import Optional
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
from .config import Config
from .journal import configure
from .schemas import ReverseInput, SearchInput
from .storage import AccessError, Store
from .visicom import ProviderError, Visicom

log = configure()


def create_app(config: Optional[Config] = None, provider=None):
    settings = config or Config.from_env()
    store = Store(
        settings.db_path,
        settings.global_daily,
        settings.token_daily,
        settings.token_minute,
        settings.token_daily_overrides,
    )

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=settings.timeout, follow_redirects=False) as client:
            app.state.provider = provider or Visicom(settings.api_key, client)
            log.info("server_started", extra={"fields": {"api_version": 1}})
            yield
            log.info("server_stopped")

    app = FastAPI(title="UA Address Search", version="1.0", lifespan=lifespan)
    app.state.store = store
    if provider is not None:
        app.state.provider = provider
    bearer = HTTPBearer(auto_error=False)
    sessions = OrderedDict()

    async def authenticate(
        request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)
    ):
        if credentials is None or len(credentials.credentials) > 512:
            raise AccessError("unauthorized", 401)
        digest = await run_in_threadpool(store.authenticate, credentials.credentials)
        label = await run_in_threadpool(store.token_label, digest)
        request.state.token_label = label
        session_id = request.state.session_id
        identity = (digest, session_id)
        if session_id and identity not in sessions:
            sessions[identity] = None
            if len(sessions) > 10000:
                sessions.popitem(last=False)
            log.info(
                "client_connected",
                extra={
                    "fields": {
                        "session_id": session_id,
                        "token_label": label,
                        "request_id": request.state.request_id,
                    }
                },
            )
        return digest

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        started = time.monotonic()
        request.state.request_id = uuid.uuid4().hex
        raw_session = request.headers.get("X-UAAS-Session-ID", "")
        try:
            request.state.session_id = str(uuid.UUID(raw_session)) if raw_session else None
        except (ValueError, AttributeError):
            request.state.session_id = None
        request.state.token_label = None
        request.state.result_count = 0
        request.state.upstream_calls = 0
        request.state.error_code = None
        status = 500
        response = None
        try:
            size = request.headers.get("content-length", "0")
            if not size.isdigit() or int(size) > 4096:
                request.state.error_code = "request_too_large"
                response = JSONResponse({"error": {"code": "request_too_large"}}, status_code=413)
            else:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        break
                if len(body) > 4096:
                    request.state.error_code = "request_too_large"
                    response = JSONResponse(
                        {"error": {"code": "request_too_large"}}, status_code=413
                    )
                else:
                    request._body = bytes(body)
                    response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            path = request.url.path
            routes = {
                "/healthz": "health",
                "/v1/search": "search",
                "/v1/reverse": "reverse",
                "/v1/quota": "quota",
            }
            if path.startswith("/v1/features/"):
                route, operation = "/v1/features/{id}", "feature"
            else:
                route = path if path in routes else "unknown"
                operation = routes.get(path, "unknown")
            log.info(
                "request_completed",
                extra={
                    "fields": {
                        "request_id": request.state.request_id,
                        "session_id": request.state.session_id,
                        "token_label": request.state.token_label,
                        "method": request.method,
                        "route": route,
                        "operation": operation,
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "result_count": request.state.result_count,
                        "upstream_calls": request.state.upstream_calls,
                        "error_code": request.state.error_code,
                    }
                },
            )

    @app.exception_handler(AccessError)
    async def access_error(request, exc):
        request.state.error_code = exc.code
        headers = (
            {"Retry-After": str(exc.retry_after)}
            if exc.status == 429
            else {"WWW-Authenticate": "Bearer"}
        )
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status, headers=headers)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        request.state.error_code = exc.code
        headers = {"Retry-After": "60"} if exc.status == 429 else {}
        log.warning(
            "provider_failure",
            extra={"fields": {"code": exc.code, "request_id": request.state.request_id}},
        )
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        request.state.error_code = "invalid_request"
        return JSONResponse({"error": {"code": "invalid_request"}}, status_code=422)

    async def execute(request, digest, operation, *args):
        await run_in_threadpool(store.reserve, digest)
        request.state.upstream_calls = 1
        results = await operation(*args)
        request.state.result_count = len(results)
        return {"results": results}

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "api_version": 1}

    @app.post("/v1/search")
    async def search(request: Request, data: SearchInput, digest=Depends(authenticate)):
        return await execute(request, digest, app.state.provider.search, data.text)

    @app.post("/v1/reverse")
    async def reverse(request: Request, data: ReverseInput, digest=Depends(authenticate)):
        return await execute(
            request, digest, app.state.provider.reverse, data.longitude, data.latitude
        )

    @app.get("/v1/features/{identifier}")
    async def feature(request: Request, identifier: str, digest=Depends(authenticate)):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            request.state.error_code = "invalid_identifier"
            return JSONResponse({"error": {"code": "invalid_identifier"}}, status_code=422)
        return await execute(request, digest, app.state.provider.feature, identifier)

    @app.get("/v1/quota")
    async def quota(digest=Depends(authenticate)):
        await run_in_threadpool(store.reserve, digest, False)
        return await run_in_threadpool(store.quota, digest)

    return app
