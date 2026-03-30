"""Tests for GET /api/v1/merchant/profile endpoint."""
import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Merchant, Balance
from tests.conftest import make_signature


# ---------------------------------------------------------------------------
# Helper fixtures local to this module
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def merchant_with_balance(test_session):
    """Merchant with amount=500.00 / reserved=100.00 → available=400.00."""
    merchant = Merchant(
        id=uuid.uuid4(),
        name="Balance Test Merchant",
        email=f"balancetest_{uuid.uuid4()}@example.com",
        api_token=f"balancetest-token-{uuid.uuid4()}",
        secret_key="balance-secret",
        created_at=datetime.utcnow(),
    )
    test_session.add(merchant)

    balance = Balance(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        amount=Decimal("500.00"),
        reserved=Decimal("100.00"),
        updated_at=datetime.utcnow(),
    )
    test_session.add(balance)
    await test_session.commit()
    await test_session.refresh(merchant)
    await test_session.refresh(balance)
    return merchant, balance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_profile_success(client: AsyncClient, seeded_merchant):
    """Valid token returns 200 with correct profile fields."""
    merchant, balance = seeded_merchant

    response = await client.get(
        "/api/v1/merchant/profile",
        headers={"X-API-Token": merchant.api_token},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == str(merchant.id)
    assert data["name"] == merchant.name
    assert data["email"] == merchant.email
    assert "available_balance" in data
    assert "total_balance" in data
    assert "reserved_balance" in data

    assert Decimal(data["total_balance"]) == balance.amount
    assert Decimal(data["reserved_balance"]) == balance.reserved
    assert Decimal(data["available_balance"]) == balance.amount - balance.reserved


@pytest.mark.asyncio
async def test_get_profile_invalid_token(client: AsyncClient):
    """Unknown API token returns 401."""
    response = await client.get(
        "/api/v1/merchant/profile",
        headers={"X-API-Token": "completely-invalid-token"},
    )

    assert response.status_code == 401
    assert "Invalid API token" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_get_profile_missing_token(client: AsyncClient):
    """Missing X-API-Token header returns 422 (validation error)."""
    response = await client.get("/api/v1/merchant/profile")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_profile_returns_correct_balance(
    client: AsyncClient, merchant_with_balance
):
    """
    Merchant with amount=500.00 and reserved=100.00 must report
    available_balance=400.00, total_balance=500.00, reserved_balance=100.00.
    """
    merchant, balance = merchant_with_balance

    response = await client.get(
        "/api/v1/merchant/profile",
        headers={"X-API-Token": merchant.api_token},
    )

    assert response.status_code == 200
    data = response.json()

    assert Decimal(data["available_balance"]) == Decimal("400.00")
    assert Decimal(data["total_balance"]) == Decimal("500.00")
    assert Decimal(data["reserved_balance"]) == Decimal("100.00")


@pytest.mark.asyncio
async def test_get_profile_served_from_cache(client: AsyncClient, seeded_merchant, mock_redis):
    """If Redis returns cached data, it is used directly without hitting the DB."""
    import json
    merchant, balance = seeded_merchant

    cached_data = {
        "id": str(merchant.id),
        "name": merchant.name,
        "email": merchant.email,
        "available_balance": "999.99",
        "total_balance": "999.99",
        "reserved_balance": "0.00",
    }
    # Simulate Redis returning cached JSON
    mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))

    response = await client.get(
        "/api/v1/merchant/profile",
        headers={"X-API-Token": merchant.api_token},
    )

    assert response.status_code == 200
    data = response.json()
    # The cached value should be returned
    assert Decimal(data["available_balance"]) == Decimal("999.99")

    # Reset mock so subsequent tests start clean
    mock_redis.get = AsyncMock(return_value=None)


@pytest.mark.asyncio
async def test_get_profile_sets_cache_when_miss(client: AsyncClient, seeded_merchant, mock_redis):
    """On a cache miss the handler must call setex to populate the cache."""
    merchant, _ = seeded_merchant
    mock_redis.get = AsyncMock(return_value=None)

    response = await client.get(
        "/api/v1/merchant/profile",
        headers={"X-API-Token": merchant.api_token},
    )

    assert response.status_code == 200
    # setex should have been called once to cache the profile
    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    cache_key = call_args[0][0]
    assert str(merchant.id) in cache_key
