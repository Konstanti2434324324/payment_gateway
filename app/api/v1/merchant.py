from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_current_merchant
from app.models.models import Merchant, Balance
from app.schemas.schemas import MerchantProfile
from app.database import get_db
from app.core.redis_client import get_redis, get_cached_profile, set_cached_profile

router = APIRouter()


@router.get("/merchant/profile", response_model=MerchantProfile)
async def get_profile(
    merchant: Merchant = Depends(get_current_merchant),
    session: AsyncSession = Depends(get_db),
):
    redis = await get_redis()
    cached = await get_cached_profile(redis, str(merchant.id))
    if cached:
        return MerchantProfile(**cached)

    result = await session.execute(select(Balance).where(Balance.merchant_id == merchant.id))
    balance = result.scalar_one()

    profile = MerchantProfile(
        id=merchant.id,
        name=merchant.name,
        email=merchant.email,
        available_balance=balance.amount - balance.reserved,
        total_balance=balance.amount,
        reserved_balance=balance.reserved,
    )
    await set_cached_profile(redis, str(merchant.id), profile.model_dump(mode="json"))
    return profile
