from fastapi import Depends, FastAPI

from . import auth, orders, portfolio, signals
from .security import require_api_key

app = FastAPI(title="Trading Companion Backend", version="0.3.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router, prefix="/auth/ibkr", tags=["auth"])
app.include_router(
    portfolio.router,
    prefix="/portfolio",
    tags=["portfolio"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    signals.router,
    prefix="/signals",
    tags=["signals"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    orders.router,
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(require_api_key)],
)
