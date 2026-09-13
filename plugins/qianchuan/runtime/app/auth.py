from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from app.config import Settings


def require_local_api_key(request: Request, settings: Settings) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header.")
    if not hmac.compare_digest(token, settings.local_api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid local API key.")
    return request.headers.get("x-operator-id", "local-agent")[:64] or "local-agent"
