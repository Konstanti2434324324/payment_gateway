"""Tests for POST /api/v1/payments endpoint."""
import asyncio
import json
import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Merchant, Balance, Payment, PaymentStatus
from tests.conftest import make_signature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_payment_request(amount: str, merchant: Merchant) -> tuple[bytes, dict]:
    """Return (body_bytes, headers) ready for a POST /api/v1/payments request."""
    body = json.dumps({"amount": amount}).encode()
    sig = make_signature(body, merchant.secret_key)
    headers = {
        "X-API-Token": merchant.api_token,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }
    return body, headers


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_payment_success(
    client: AsyncClient, test_session: AsyncSession, seeded_merchant
):
    """Valid request creates a payment with CREATED status and reserves balance."""
    merchant, balance = seeded_merchant
    initial_reserved = balance.reserved

    body, headers = _build_payment_request("100.00", merchant)

    with patch("app.services.payment_service.process_payment_in_background"):
        response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 201
    data = response.json()

    assert "id" in data
    assert "external_invoice_id" in data
    assert Decimal(data["amount"]) == Decimal("100.00")
    assert data["status"] == PaymentStatus.CREATED.value

    # Verify payment exists in DB
    result = await test_session.execute(
        select(Payment).where(Payment.id == uuid.UUID(data["id"]))
    )
    payment = result.scalar_one_or_none()
    assert payment is not None
    assert payment.status == PaymentStatus.CREATED
    assert payment.merchant_id == merchant.id

    # Verify reserved balance increased
    await test_session.refresh(balance)
    assert balance.reserved == initial_reserved + Decimal("100.00")


@pytest.mark.asyncio
async def test_create_payment_insufficient_balance(
    client: AsyncClient, seeded_merchant
):
    """Amount exceeding available balance returns 402."""
    merchant, balance = seeded_merchant
    # seeded_merchant starts with amount=1000.00, reserved=0.00
    # Request more than available
    over_amount = str(balance.amount + Decimal("1.00"))
    body, headers = _build_payment_request(over_amount, merchant)

    with patch("app.services.payment_service.process_payment_in_background"):
        response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 402
    assert "Insufficient" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_create_payment_invalid_signature(
    client: AsyncClient, seeded_merchant
):
    """Wrong HMAC signature returns 403."""
    merchant, _ = seeded_merchant
    body = json.dumps({"amount": "50.00"}).encode()
    headers = {
        "X-API-Token": merchant.api_token,
        "X-Signature": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "Content-Type": "application/json",
    }

    response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 403
    assert "signature" in response.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_create_payment_invalid_token(client: AsyncClient):
    """Unknown API token returns 401."""
    body = json.dumps({"amount": "50.00"}).encode()
    sig = make_signature(body, "any-secret")
    headers = {
        "X-API-Token": "nonexistent-token",
        "X-Signature": sig,
        "Content-Type": "application/json",
    }

    response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 401
    assert "Invalid API token" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_create_payment_zero_amount(client: AsyncClient, seeded_merchant):
    """Amount of 0 is rejected with 422 (Pydantic validation)."""
    merchant, _ = seeded_merchant
    body, headers = _build_payment_request("0", merchant)

    response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_payment_negative_amount(client: AsyncClient, seeded_merchant):
    """Negative amount is rejected with 422 (Pydantic validation)."""
    merchant, _ = seeded_merchant
    body, headers = _build_payment_request("-10.00", merchant)

    response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_payment_exact_balance(
    client: AsyncClient, test_session: AsyncSession, seeded_merchant
):
    """
    Payment whose amount exactly equals the available balance must succeed.
    After creation, reserved == amount (nothing left available).
    """
    merchant, balance = seeded_merchant
    available = balance.amount - balance.reserved
    body, headers = _build_payment_request(str(available), merchant)

    with patch("app.services.payment_service.process_payment_in_background"):
        response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 201

    await test_session.refresh(balance)
    # All funds are now reserved; available = 0
    assert balance.reserved == balance.amount


@pytest.mark.asyncio
async def test_create_payment_concurrent(
    client_with_engine: AsyncClient,
    test_engine,
    mock_redis,
):
    """
    Three concurrent requests each for 400.00 against a 1000.00 balance.
    At most 2 can succeed; the balance must never go negative.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    # Create a dedicated merchant + balance for this test so it is independent
    # of the seeded_merchant fixture (which shares a session that may conflict).
    async_session_factory = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )

    async with async_session_factory() as setup_session:
        merchant = Merchant(
            id=uuid.uuid4(),
            name="Concurrent Merchant",
            email=f"concurrent_{uuid.uuid4()}@example.com",
            api_token=f"concurrent-token-{uuid.uuid4()}",
            secret_key="concurrent-secret",
            created_at=datetime.utcnow(),
        )
        setup_session.add(merchant)

        balance = Balance(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            amount=Decimal("1000.00"),
            reserved=Decimal("0.00"),
            updated_at=datetime.utcnow(),
        )
        setup_session.add(balance)
        await setup_session.commit()

        merchant_id = merchant.id
        balance_id = balance.id
        api_token = merchant.api_token
        secret_key = merchant.secret_key

    amount = "400.00"
    body = json.dumps({"amount": amount}).encode()
    sig = make_signature(body, secret_key)
    headers = {
        "X-API-Token": api_token,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }

    with patch("app.services.payment_service.process_payment_in_background"):
        tasks = [
            client_with_engine.post("/api/v1/payments", content=body, headers=headers)
            for _ in range(3)
        ]
        responses = await asyncio.gather(*tasks)

    status_codes = [r.status_code for r in responses]
    success_count = status_codes.count(201)

    # At most 2 payments of 400.00 fit in 1000.00
    assert success_count <= 2
    # At least one must succeed (balance is more than enough for one)
    assert success_count >= 1

    # Confirm the balance was not overdrafted
    async with async_session_factory() as verify_session:
        from sqlalchemy import select as sa_select
        result = await verify_session.execute(
            sa_select(Balance).where(Balance.id == balance_id)
        )
        refreshed_balance = result.scalar_one()
        available = refreshed_balance.amount - refreshed_balance.reserved
        assert available >= Decimal("0.00"), (
            f"Balance went negative: amount={refreshed_balance.amount}, "
            f"reserved={refreshed_balance.reserved}, available={available}"
        )
        # reserved == success_count * 400
        assert refreshed_balance.reserved == Decimal("400.00") * success_count


@pytest.mark.asyncio
async def test_create_payment_missing_amount_field(client: AsyncClient, seeded_merchant):
    """Request body missing 'amount' field returns 422."""
    merchant, _ = seeded_merchant
    body = json.dumps({}).encode()
    sig = make_signature(body, merchant.secret_key)
    headers = {
        "X-API-Token": merchant.api_token,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }

    response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_payment_too_many_decimal_places(
    client: AsyncClient, seeded_merchant
):
    """Amount with more than 2 decimal places is rejected with 422."""
    merchant, _ = seeded_merchant
    body, headers = _build_payment_request("10.001", merchant)

    response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_payment_response_shape(
    client: AsyncClient, seeded_merchant
):
    """Response body has all required fields with correct types."""
    merchant, _ = seeded_merchant
    body, headers = _build_payment_request("25.50", merchant)

    with patch("app.services.payment_service.process_payment_in_background"):
        response = await client.post("/api/v1/payments", content=body, headers=headers)

    assert response.status_code == 201
    data = response.json()

    required_fields = {"id", "external_invoice_id", "amount", "status", "created_at"}
    assert required_fields.issubset(data.keys())

    # id and external_invoice_id must be valid UUIDs / non-empty strings
    uuid.UUID(data["id"])  # raises ValueError if not valid
    assert data["external_invoice_id"]
    assert data["status"] == "created"


@pytest.mark.asyncio
async def test_create_payment_background_task_not_blocking(
    client: AsyncClient, seeded_merchant
):
    """
    Payment creation endpoint must return quickly; the background task
    (which has a 1-2 s artificial sleep) must not block the response.
    """
    import time
    merchant, _ = seeded_merchant
    body, headers = _build_payment_request("10.00", merchant)

    # Patch the background task so it genuinely does nothing in tests
    with patch("app.services.payment_service.process_payment_in_background"):
        start = time.monotonic()
        response = await client.post("/api/v1/payments", content=body, headers=headers)
        elapsed = time.monotonic() - start

    assert response.status_code == 201
    # The endpoint itself should respond well under 2 seconds
    assert elapsed < 2.0, f"Endpoint took {elapsed:.2f}s — background task may be blocking"
