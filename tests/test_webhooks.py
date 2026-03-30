"""Tests for POST /api/v1/webhooks/provider endpoint."""
import json
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Balance, Merchant, Payment, PaymentStatus


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

def _webhook_payload(external_invoice_id: str, status: str, provider_id: str | None = None) -> dict:
    return {
        "id": provider_id or str(uuid.uuid4()),
        "external_invoice_id": external_invoice_id,
        "status": status,
    }


@pytest_asyncio.fixture
async def merchant_with_reserved_payment(test_session: AsyncSession):
    """
    Creates a Merchant with:
      - balance.amount  = 500.00
      - balance.reserved = 200.00  (one PROCESSING payment of 200.00)

    Returns (merchant, balance, payment).
    """
    merchant = Merchant(
        id=uuid.uuid4(),
        name="Webhook Merchant",
        email=f"webhook_{uuid.uuid4()}@example.com",
        api_token=f"webhook-token-{uuid.uuid4()}",
        secret_key="webhook-secret",
        created_at=datetime.utcnow(),
    )
    test_session.add(merchant)

    balance = Balance(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        amount=Decimal("500.00"),
        reserved=Decimal("200.00"),
        updated_at=datetime.utcnow(),
    )
    test_session.add(balance)

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        external_invoice_id=str(uuid.uuid4()),
        amount=Decimal("200.00"),
        status=PaymentStatus.PROCESSING,
        callback_url="http://localhost:8000/api/v1/webhooks/provider",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    test_session.add(payment)

    await test_session.commit()
    await test_session.refresh(merchant)
    await test_session.refresh(balance)
    await test_session.refresh(payment)

    return merchant, balance, payment


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_completed_debits_balance(
    client: AsyncClient,
    test_session: AsyncSession,
    merchant_with_reserved_payment,
    mock_redis,
):
    """
    Completed webhook must:
    - decrease balance.amount by payment.amount
    - decrease balance.reserved by payment.amount
    - set payment.status = SUCCESS
    """
    merchant, balance, payment = merchant_with_reserved_payment

    initial_amount = balance.amount
    initial_reserved = balance.reserved

    payload = _webhook_payload(payment.external_invoice_id, "Completed")
    response = await client.post("/api/v1/webhooks/provider", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    await test_session.refresh(balance)
    await test_session.refresh(payment)

    assert balance.amount == initial_amount - payment.amount
    assert balance.reserved == initial_reserved - payment.amount
    assert payment.status == PaymentStatus.SUCCESS


@pytest.mark.asyncio
async def test_webhook_canceled_restores_reserved(
    client: AsyncClient,
    test_session: AsyncSession,
    merchant_with_reserved_payment,
    mock_redis,
):
    """
    Canceled webhook must:
    - leave balance.amount unchanged
    - decrease balance.reserved by payment.amount (release the reservation)
    - set payment.status = CANCELED
    """
    merchant, balance, payment = merchant_with_reserved_payment

    initial_amount = balance.amount
    initial_reserved = balance.reserved

    payload = _webhook_payload(payment.external_invoice_id, "Canceled")
    response = await client.post("/api/v1/webhooks/provider", json=payload)

    assert response.status_code == 200

    await test_session.refresh(balance)
    await test_session.refresh(payment)

    # Amount must be untouched
    assert balance.amount == initial_amount
    # Reserved released
    assert balance.reserved == initial_reserved - payment.amount
    assert payment.status == PaymentStatus.CANCELED


@pytest.mark.asyncio
async def test_webhook_unknown_payment(client: AsyncClient, mock_redis):
    """Webhook referencing an unknown external_invoice_id returns 404."""
    payload = _webhook_payload(str(uuid.uuid4()), "Completed")
    response = await client.post("/api/v1/webhooks/provider", json=payload)

    assert response.status_code == 404
    assert "Payment not found" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_webhook_idempotency(
    client: AsyncClient,
    test_session: AsyncSession,
    merchant_with_reserved_payment,
    mock_redis,
):
    """
    Sending the same Completed webhook twice must not debit the balance twice.
    The second call must be a no-op (idempotency guard).
    """
    merchant, balance, payment = merchant_with_reserved_payment

    payload = _webhook_payload(payment.external_invoice_id, "Completed")

    # First call
    r1 = await client.post("/api/v1/webhooks/provider", json=payload)
    assert r1.status_code == 200

    await test_session.refresh(balance)
    amount_after_first = balance.amount
    reserved_after_first = balance.reserved

    # Second call — must be idempotent
    r2 = await client.post("/api/v1/webhooks/provider", json=payload)
    assert r2.status_code == 200

    await test_session.refresh(balance)
    # Balance must not have changed further
    assert balance.amount == amount_after_first
    assert balance.reserved == reserved_after_first

    await test_session.refresh(payment)
    assert payment.status == PaymentStatus.SUCCESS


@pytest.mark.asyncio
async def test_webhook_completed_then_canceled(
    client: AsyncClient,
    test_session: AsyncSession,
    merchant_with_reserved_payment,
    mock_redis,
):
    """
    Once a payment reaches SUCCESS status via Completed webhook, a subsequent
    Canceled webhook must be ignored (idempotency guard covers terminal states).
    """
    merchant, balance, payment = merchant_with_reserved_payment

    # Mark as SUCCESS first
    completed_payload = _webhook_payload(payment.external_invoice_id, "Completed")
    r1 = await client.post("/api/v1/webhooks/provider", json=completed_payload)
    assert r1.status_code == 200

    await test_session.refresh(balance)
    await test_session.refresh(payment)
    assert payment.status == PaymentStatus.SUCCESS

    amount_after_completed = balance.amount
    reserved_after_completed = balance.reserved

    # Now send Canceled — must be ignored
    canceled_payload = _webhook_payload(payment.external_invoice_id, "Canceled")
    r2 = await client.post("/api/v1/webhooks/provider", json=canceled_payload)
    assert r2.status_code == 200

    await test_session.refresh(balance)
    await test_session.refresh(payment)

    # Status must remain SUCCESS
    assert payment.status == PaymentStatus.SUCCESS
    # Balance must not have changed
    assert balance.amount == amount_after_completed
    assert balance.reserved == reserved_after_completed


@pytest.mark.asyncio
async def test_webhook_invalidates_redis_cache(
    client: AsyncClient,
    test_session: AsyncSession,
    merchant_with_reserved_payment,
    mock_redis,
):
    """
    After a webhook is processed the handler must call redis.delete to
    invalidate the cached merchant profile.
    """
    merchant, balance, payment = merchant_with_reserved_payment

    mock_redis.delete.reset_mock()

    payload = _webhook_payload(payment.external_invoice_id, "Completed")
    response = await client.post("/api/v1/webhooks/provider", json=payload)
    assert response.status_code == 200

    # redis.delete must have been called with the merchant profile cache key
    mock_redis.delete.assert_called_once()
    call_args = mock_redis.delete.call_args
    cache_key = call_args[0][0]
    assert str(merchant.id) in cache_key
    assert "merchant:profile:" in cache_key


@pytest.mark.asyncio
async def test_webhook_missing_required_fields(client: AsyncClient, mock_redis):
    """Webhook payload missing required fields returns 422."""
    # Missing external_invoice_id
    incomplete_payload = {"id": str(uuid.uuid4()), "status": "Completed"}
    response = await client.post("/api/v1/webhooks/provider", json=incomplete_payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_webhook_completed_reduces_reserved_to_zero(
    client: AsyncClient,
    test_session: AsyncSession,
    mock_redis,
):
    """
    Edge case: single payment equal to full balance.
    After Completed: amount=0, reserved=0.
    """
    # Create a merchant where the only payment is the full balance
    merchant = Merchant(
        id=uuid.uuid4(),
        name="Edge Merchant",
        email=f"edge_{uuid.uuid4()}@example.com",
        api_token=f"edge-token-{uuid.uuid4()}",
        secret_key="edge-secret",
        created_at=datetime.utcnow(),
    )
    test_session.add(merchant)

    balance = Balance(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        amount=Decimal("100.00"),
        reserved=Decimal("100.00"),
        updated_at=datetime.utcnow(),
    )
    test_session.add(balance)

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        external_invoice_id=str(uuid.uuid4()),
        amount=Decimal("100.00"),
        status=PaymentStatus.PROCESSING,
        callback_url="http://localhost:8000/api/v1/webhooks/provider",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    test_session.add(payment)
    await test_session.commit()
    await test_session.refresh(balance)
    await test_session.refresh(payment)

    payload = _webhook_payload(payment.external_invoice_id, "Completed")
    response = await client.post("/api/v1/webhooks/provider", json=payload)
    assert response.status_code == 200

    await test_session.refresh(balance)
    assert balance.amount == Decimal("0.00")
    assert balance.reserved == Decimal("0.00")
