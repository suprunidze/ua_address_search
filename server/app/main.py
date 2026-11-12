"""UA Address Search proxy: one replica, persistent SQLite volume."""

from contextlib import asynccontextmanager
import time
import logging
import re
from typing import Optional
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
from .config import Config
from .schemas import ReverseInput, SearchInput
from .storage import AccessError, Store
from .visicom import ProviderError, Visicom

log = logging.getLogger("uaas")


def create_app(config: Optional[Config] = None, provider=None):
    settings = config or Config.from_env()
    store = Store(
        settings.db_path, settings.global_daily, settings.token_daily, settings.token_minute
    )

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=settings.timeout, follow_redirects=False) as client:
            app.state.provider = provider or Visicom(settings.api_key, client)
            yield

    app = FastAPI(title="UA Address Search", version="1.0", lifespan=lifespan)
    app.state.store = store
    if provider is not None:
        app.state.provider = provider
    bearer = HTTPBearer(auto_error=False)

    async def authenticate(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
        if credentials is None or len(credentials.credentials) > 512:
            raise AccessError("unauthorized", 401)
        return await run_in_threadpool(store.authenticate, credentials.credentials)

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        started = time.monotonic()
        size = request.headers.get("content-length", "0")
        if not size.isdigit() or int(size) > 4096:
            return JSONResponse({"error": {"code": "request_too_large"}}, status_code=413)
        # Buffer once to bound chunked bodies as well as Content-Length requests.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return JSONResponse({"error": {"code": "request_too_large"}}, status_code=413)
        request._body = bytes(body)
        response = await call_next(request)
        log.info(
            "request method=%s status=%s duration_ms=%d",
            request.method,
            response.status_code,
            int((time.monotonic() - started) * 1000),
        )
        return response

    @app.exception_handler(AccessError)
    async def access_error(request, exc):
        headers = (
            {"Retry-After": str(exc.retry_after)}
            if exc.status == 429
            else {"WWW-Authenticate": "Bearer"}
        )
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status, headers=headers)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        headers = {"Retry-After": "60"} if exc.status == 429 else {}
        log.warning("provider_failure code=%s", exc.code)
        return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"error": {"code": "invalid_request"}}, status_code=422)

    async def execute(digest, operation, *args):
        await run_in_threadpool(store.reserve, digest)
        return {"results": await operation(*args)}

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "api_version": 1}

    @app.post("/v1/search")
    async def search(data: SearchInput, digest=Depends(authenticate)):
        return await execute(digest, app.state.provider.search, data.text)

    @app.post("/v1/reverse")
    async def reverse(data: ReverseInput, digest=Depends(authenticate)):
        return await execute(digest, app.state.provider.reverse, data.longitude, data.latitude)

    @app.get("/v1/features/{identifier}")
    async def feature(identifier: str, digest=Depends(authenticate)):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            return JSONResponse({"error": {"code": "invalid_identifier"}}, status_code=422)
        return await execute(digest, app.state.provider.feature, identifier)

    @app.get("/v1/quota")
    async def quota(digest=Depends(authenticate)):
        await run_in_threadpool(store.reserve, digest, False)
        return await run_in_threadpool(store.quota, digest)

    return app
