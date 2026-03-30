from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.schemas import ProviderWebhookPayload
from app.services.payment_service import process_webhook
from app.database import get_db

router = APIRouter()


@router.post("/webhooks/provider", status_code=200)
async def provider_webhook(
    payload: ProviderWebhookPayload,
    session: AsyncSession = Depends(get_db),
):
    await process_webhook(session, payload)
    return {"status": "ok"}
