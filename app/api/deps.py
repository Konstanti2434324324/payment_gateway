from fastapi import Header, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.models import Merchant


async def get_current_merchant(
    request: Request,
    x_api_token: str = Header(...),
    session: AsyncSession = Depends(get_db),
) -> Merchant:
    result = await session.execute(select(Merchant).where(Merchant.api_token == x_api_token))
    merchant = result.scalar_one_or_none()
    if not merchant:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return merchant


async def get_authenticated_merchant(
    request: Request,
    x_api_token: str = Header(...),
    x_signature: str = Header(...),
    session: AsyncSession = Depends(get_db),
) -> Merchant:
    # Get raw body (already read, stored in request.state)
    body = await request.body()
    result = await session.execute(select(Merchant).where(Merchant.api_token == x_api_token))
    merchant = result.scalar_one_or_none()
    if not merchant:
        raise HTTPException(status_code=401, detail="Invalid API token")
    from app.core.security import verify_signature
    if not verify_signature(body, merchant.secret_key, x_signature):
        raise HTTPException(status_code=403, detail="Invalid request signature")
    return merchant
