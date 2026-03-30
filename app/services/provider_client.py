import httpx
from app.config import settings
from app.schemas.schemas import ProviderCreatePaymentRequest, ProviderCreatePaymentResponse


async def send_payment_to_provider(payload: ProviderCreatePaymentRequest) -> ProviderCreatePaymentResponse:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.PROVIDER_BASE_URL}/api/v1/payments",
            json=payload.model_dump(),
            timeout=30.0,
        )
        response.raise_for_status()
        return ProviderCreatePaymentResponse(**response.json())
