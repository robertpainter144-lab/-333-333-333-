import httpx

from .config import settings


class IBKRClient:
    """Thin wrapper around the IBKR Client Portal Web API exposed by ibeam."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ibeam_base_url).rstrip("/")
        # ibeam ships with a self-signed cert; family-only deployment, so verify=False.
        self._client = httpx.AsyncClient(base_url=self.base_url, verify=False, timeout=15.0)

    async def auth_status(self) -> dict:
        r = await self._client.post("/v1/api/iserver/auth/status")
        r.raise_for_status()
        return r.json()

    async def reauthenticate(self) -> dict:
        r = await self._client.post("/v1/api/iserver/reauthenticate")
        r.raise_for_status()
        return r.json()

    async def accounts(self) -> list[dict]:
        r = await self._client.get("/v1/api/iserver/accounts")
        r.raise_for_status()
        return r.json().get("accounts", [])

    async def positions(self, account_id: str) -> list[dict]:
        r = await self._client.get(f"/v1/api/portfolio/{account_id}/positions/0")
        r.raise_for_status()
        return r.json()

    async def summary(self, account_id: str) -> dict:
        r = await self._client.get(f"/v1/api/portfolio/{account_id}/summary")
        r.raise_for_status()
        return r.json()

    async def place_order(self, account_id: str, order: dict) -> dict:
        r = await self._client.post(
            f"/v1/api/iserver/account/{account_id}/orders",
            json={"orders": [order]},
        )
        r.raise_for_status()
        return r.json()

    async def close(self) -> None:
        await self._client.aclose()


_singleton: IBKRClient | None = None


def get_ibkr() -> IBKRClient:
    global _singleton
    if _singleton is None:
        _singleton = IBKRClient()
    return _singleton
