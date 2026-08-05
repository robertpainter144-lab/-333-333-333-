from fastapi import APIRouter, Depends, HTTPException, status

from .ibkr import IBKRClient, get_ibkr

router = APIRouter()


async def _first_account(client: IBKRClient) -> str:
    accounts = await client.accounts()
    if not accounts:
        raise HTTPException(status.HTTP_409_CONFLICT, "no IBKR account on this session")
    return accounts[0]


@router.get("/positions")
async def positions(client: IBKRClient = Depends(get_ibkr)) -> list[dict]:
    acct = await _first_account(client)
    return await client.positions(acct)


@router.get("/summary")
async def summary(client: IBKRClient = Depends(get_ibkr)) -> dict:
    acct = await _first_account(client)
    return await client.summary(acct)
