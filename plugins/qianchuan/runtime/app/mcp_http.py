from __future__ import annotations

import contextlib
import hmac

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from app.config import get_settings
from app.mcp_server import mcp


class LocalApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in {"/healthz", "/readyz"}:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        settings = get_settings()
        authorization = request.headers.get("authorization", "").strip()
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse({"error": "missing_or_invalid_authorization"}, status_code=401)
        if not hmac.compare_digest(token, settings.local_api_key):
            return JSONResponse({"error": "invalid_local_api_key"}, status_code=403)
        return await call_next(request)


async def healthz(_request: Request) -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "mcp_path": "/mcp",
            "write_enabled": settings.qianchuan_write_enabled,
            "has_oauth_gateway": bool(
                settings.oauth_gateway_base_url and settings.oauth_gateway_hmac_secret
            ),
        }
    )


async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route("/readyz", readyz, methods=["GET"]),
        Mount("/", mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)
app.add_middleware(LocalApiKeyMiddleware)


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.mcp_http:app",
        host=settings.mcp_host,
        port=settings.mcp_port,
        workers=1,
    )


if __name__ == "__main__":
    main()
