from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from .ibkr import IBKRClient, get_ibkr
from .security import require_api_key

router = APIRouter()


@router.get("/start")
async def start() -> RedirectResponse:
    """
    Opened by the iOS app inside ASWebAuthenticationSession.

    Redirects the user to ibeam's IBKR SSO page. After they sign in, ibeam
    holds the Client Portal session and the iOS app can poll /status.
    """
    from .config import settings

    return RedirectResponse(f"{settings.ibeam_base_url}/", status_code=302)


@router.get("/status", dependencies=[Depends(require_api_key)])
async def status_(client: IBKRClient = Depends(get_ibkr)) -> dict:
    try:
        s = await client.auth_status()
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return {
        "authenticated": bool(s.get("authenticated")),
        "connected": bool(s.get("connected")),
        "competing": bool(s.get("competing")),
    }


@router.post("/reauthenticate", dependencies=[Depends(require_api_key)])
async def reauth(client: IBKRClient = Depends(get_ibkr)) -> dict:
    return await client.reauthenticate()
