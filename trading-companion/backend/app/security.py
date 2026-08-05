import hmac

from fastapi import Header, HTTPException, status

from .config import settings


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, settings.companion_api_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad token")
