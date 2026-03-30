import asyncio
import random
import uuid

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock Provider")


class CreatePaymentRequest(BaseModel):
    external_invoice_id: str
    amount: str
    callback_url: str


@app.post("/api/v1/payments", status_code=201)
async def create_payment(request: CreatePaymentRequest):
    payment_id = str(uuid.uuid4())
    asyncio.create_task(send_webhook(request.callback_url, payment_id, request.external_invoice_id))
    return {
        "id": payment_id,
        "external_invoice_id": request.external_invoice_id,
        "amount": request.amount,
        "callback_url": request.callback_url,
        "status": "Created",
    }


async def send_webhook(callback_url: str, payment_id: str, external_invoice_id: str):
    await asyncio.sleep(random.uniform(2, 5))
    # ~70% Completed, ~30% Canceled per architecture spec
    status = random.choices(["Completed", "Canceled"], weights=[70, 30])[0]
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                callback_url,
                json={
                    "id": payment_id,
                    "external_invoice_id": external_invoice_id,
                    "status": status,
                },
                timeout=10.0,
            )
        except Exception as e:
            print(f"Webhook failed: {e}")
