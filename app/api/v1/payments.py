from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_authenticated_merchant
from app.models.models import Merchant
from app.schemas.schemas import CreatePaymentRequest, CreatePaymentResponse
import app.services.payment_service as payment_service
from app.services.payment_service import create_payment
from app.database import get_db

router = APIRouter()


@router.post("/payments", response_model=CreatePaymentResponse, status_code=201)
async def create_payment_endpoint(
    payload: CreatePaymentRequest,
    background_tasks: BackgroundTasks,
    merchant: Merchant = Depends(get_authenticated_merchant),
    session: AsyncSession = Depends(get_db),
):
    payment = await create_payment(session, merchant, payload.amount)
    background_tasks.add_task(payment_service.process_payment_in_background, payment.id)
    return CreatePaymentResponse(
        id=payment.id,
        external_invoice_id=payment.external_invoice_id,
        amount=payment.amount,
        status=payment.status.value,
        created_at=payment.created_at,
    )
