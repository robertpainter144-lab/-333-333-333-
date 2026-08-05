import httpx

from .config import settings


class AlpacaClient:
    """
    Thin Alpaca brokerage client. Currently scoped to the family member's
    paper-trading account; live Alpaca would use https://api.alpaca.markets.
    """

    PAPER_BASE = "https://paper-api.alpaca.markets"

    def __init__(self, key: str, secret: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.PAPER_BASE,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=15.0,
        )

    async def positions(self) -> list[dict]:
        r = await self._client.get("/v2/positions")
        r.raise_for_status()
        return r.json()

    async def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        type_: str = "market",
        tif: str = "day",
    ) -> dict:
        r = await self._client.post(
            "/v2/orders",
            json={
                "symbol": symbol,
                "qty": qty,
                "side": side.lower(),
                "type": type_,
                "time_in_force": tif,
            },
        )
        r.raise_for_status()
        return r.json()

    async def close(self) -> None:
        await self._client.aclose()


_singleton: AlpacaClient | None = None


def get_alpaca() -> AlpacaClient:
    global _singleton
    if _singleton is None:
        if not settings.alpaca_paper_key or not settings.alpaca_paper_secret:
            raise RuntimeError("ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET must be set")
        _singleton = AlpacaClient(settings.alpaca_paper_key, settings.alpaca_paper_secret)
    return _singleton
