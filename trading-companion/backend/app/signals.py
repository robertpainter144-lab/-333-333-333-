import json

from fastapi import APIRouter, HTTPException, status

from .config import settings

router = APIRouter()


@router.get("")
async def list_signals() -> dict:
    """
    Returns the current regime-signal payload published by the research repo.
    Schema is owned by trading-system; this endpoint is a passthrough.
    """
    path = settings.signals_path
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no signals at {path}")
    try:
        with path.open() as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"bad signals json: {exc}")
