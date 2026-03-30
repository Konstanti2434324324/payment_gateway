import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator


class MerchantProfile(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    available_balance: Decimal
    total_balance: Decimal
    reserved_balance: Decimal

    model_config = {"from_attributes": True}


class CreatePaymentRequest(BaseModel):
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        # Check max 2 decimal places
        sign, digits, exponent = v.as_tuple()
        if isinstance(exponent, int) and exponent < -2:
            raise ValueError("amount must have at most 2 decimal places")
        return v


class CreatePaymentResponse(BaseModel):
    id: uuid.UUID
    external_invoice_id: str
    amount: Decimal
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ProviderWebhookPayload(BaseModel):
    id: str
    external_invoice_id: str
    status: str


class ProviderCreatePaymentRequest(BaseModel):
    external_invoice_id: str
    amount: str
    callback_url: str


class ProviderCreatePaymentResponse(BaseModel):
    id: str
    external_invoice_id: str
    amount: str
    callback_url: str
    status: str
