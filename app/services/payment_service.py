import asyncio
import logging
import random
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.redis_client import get_redis, invalidate_profile_cache
from app.database import AsyncSessionLocal
from app.models.models import Balance, Merchant, Payment, PaymentStatus
from app.schemas.schemas import ProviderCreatePaymentRequest, ProviderWebhookPayload
from app.services.provider_client import send_payment_to_provider

logger = logging.getLogger(__name__)


async def create_payment(session: AsyncSession, merchant: Merchant, amount: Decimal) -> Payment:
    # SELECT balance FOR UPDATE
    result = await session.execute(
        select(Balance)
        .where(Balance.merchant_id == merchant.id)
        .with_for_update()
    )
    balance = result.scalar_one()

    available = balance.amount - balance.reserved
    if available < amount:
        raise HTTPException(status_code=402, detail="Insufficient available balance")

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        external_invoice_id=str(uuid.uuid4()),
        amount=amount,
        status=PaymentStatus.CREATED,
        callback_url=f"{settings.CALLBACK_BASE_URL}/api/v1/webhooks/provider",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    balance.reserved += amount
    balance.updated_at = datetime.utcnow()

    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    # Invalidate cache after balance mutation
    redis = await get_redis()
    await invalidate_profile_cache(redis, str(merchant.id))

    return payment


async def process_payment_in_background(payment_id: uuid.UUID):
    await asyncio.sleep(random.uniform(1, 2))

    async with AsyncSessionLocal() as session:
        try:
            # Get payment
            result = await session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
            payment = result.scalar_one_or_none()
            if not payment:
                logger.error(f"Payment {payment_id} not found in background task")
                return

            # Get merchant
            result = await session.execute(
                select(Merchant).where(Merchant.id == payment.merchant_id)
            )
            merchant = result.scalar_one_or_none()
            if not merchant:
                logger.error(f"Merchant for payment {payment_id} not found in background task")
                return

            provider_request = ProviderCreatePaymentRequest(
                external_invoice_id=payment.external_invoice_id,
                amount=str(payment.amount),
                callback_url=payment.callback_url,
            )

            response = await send_payment_to_provider(provider_request)

            payment.provider_payment_id = response.id
            payment.status = PaymentStatus.PROCESSING
            payment.updated_at = datetime.utcnow()

            await session.commit()

        except Exception as e:
            logger.error(f"Error processing payment {payment_id} in background: {e}")


async def process_webhook(session: AsyncSession, payload: ProviderWebhookPayload):
    # Find payment by external_invoice_id
    result = await session.execute(
        select(Payment).where(Payment.external_invoice_id == payload.external_invoice_id)
    )
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Idempotency check - if already in terminal state, skip processing
    if payment.status not in (PaymentStatus.CREATED, PaymentStatus.PROCESSING):
        return

    # SELECT balance FOR UPDATE
    result = await session.execute(
        select(Balance)
        .where(Balance.merchant_id == payment.merchant_id)
        .with_for_update()
    )
    balance = result.scalar_one()

    if payload.status == "Completed":
        balance.amount -= payment.amount
        balance.reserved -= payment.amount
        payment.status = PaymentStatus.SUCCESS
    elif payload.status == "Canceled":
        balance.reserved -= payment.amount
        payment.status = PaymentStatus.CANCELED

    payment.updated_at = datetime.utcnow()
    balance.updated_at = datetime.utcnow()

    await session.commit()

    # Invalidate Redis cache for merchant
    redis = await get_redis()
    await invalidate_profile_cache(redis, str(payment.merchant_id))
