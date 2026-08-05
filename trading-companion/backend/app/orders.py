from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, status

from . import risk
from .config import settings
from .ibkr import IBKRClient, get_ibkr

router = APIRouter()


class OrderRequest(BaseModel):
    symbol: str = Field(..., examples=["AAPL"])
    side: str = Field(..., pattern="^(BUY|SELL)$")
    quantity: int = Field(..., gt=0)
    order_type: str = Field("LMT", pattern="^(MKT|LMT)$")
    limit_price: float | None = None
    tif: str = Field("DAY", pattern="^(DAY|GTC|IOC)$")


async def _first_account(client: IBKRClient) -> str:
    accounts = await client.accounts()
    if not accounts:
        raise HTTPException(status.HTTP_409_CONFLICT, "no IBKR account on this session")
    return accounts[0]


@router.get("/limits")
async def limits() -> dict:
    return risk.limits_snapshot()


@router.post("")
async def place(order: OrderRequest, client: IBKRClient = Depends(get_ibkr)) -> dict:
    payload = {
        "symbol": order.symbol,
        "side": order.side,
        "quantity": order.quantity,
        "order_type": order.order_type,
        "limit_price": order.limit_price,
        "tif": order.tif,
    }
    notional = risk.preflight(payload)

    if settings.trading_mode != "live":
        return {
            "status": "logged-not-submitted",
            "mode": settings.trading_mode,
            "notional_usd": notional,
        }

    acct = await _first_account(client)
    result = await client.place_order(
        acct,
        {
            "ticker": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "orderType": order.order_type,
            "price": order.limit_price,
            "tif": order.tif,
        },
    )
    return {"status": "submitted", "notional_usd": notional, "ibkr": result}
