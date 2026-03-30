import asyncio
import hmac
import hashlib
import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

# Load .env.test before importing app modules so Settings picks up test values
load_dotenv(Path(__file__).parent.parent / ".env.test", override=True)

from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.models import Base, Merchant, Balance, Payment, PaymentStatus  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_setup())
    yield engine
    asyncio.run(_teardown())


@pytest_asyncio.fixture
async def test_session(test_engine):
    async_session_factory = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with async_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def mock_redis():
    """AsyncMock Redis client that simulates no cached data."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


@pytest_asyncio.fixture
async def client(test_session, mock_redis):
    """
    HTTP test client with overridden DB session and patched Redis.

    Redis is not a FastAPI dependency — it is fetched directly inside service/handler
    code via `await get_redis()`, so we must patch at the module level rather than
    using dependency_overrides.
    """
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    import app.core.redis_client as redis_module

    original_redis_client = redis_module.redis_client
    redis_module.redis_client = mock_redis

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    redis_module.redis_client = original_redis_client


@pytest_asyncio.fixture
async def client_with_engine(test_engine, mock_redis):
    """
    HTTP test client that creates a fresh session per-request.
    Used for concurrency tests where multiple requests share the same client but need
    independent DB transactions.
    """
    async_session_factory = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )

    async def override_get_db():
        async with async_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    import app.core.redis_client as redis_module

    original_redis_client = redis_module.redis_client
    redis_module.redis_client = mock_redis

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    redis_module.redis_client = original_redis_client


@pytest_asyncio.fixture
async def seeded_merchant(test_session):
    """Creates a Merchant + Balance directly in the DB and returns them."""
    merchant = Merchant(
        id=uuid.uuid4(),
        name="Test Merchant",
        email=f"test_{uuid.uuid4()}@example.com",
        api_token=f"test-token-{uuid.uuid4()}",
        secret_key="test-secret-key",
        created_at=datetime.utcnow(),
    )
    test_session.add(merchant)

    balance = Balance(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        amount=Decimal("1000.00"),
        reserved=Decimal("0.00"),
        updated_at=datetime.utcnow(),
    )
    test_session.add(balance)

    await test_session.commit()
    await test_session.refresh(merchant)
    await test_session.refresh(balance)

    return merchant, balance


@pytest.fixture
def merchant1_data():
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Merchant One",
        "email": "merchant1@example.com",
        "api_token": "token-merchant-1",
        "secret_key": "secret-merchant-1",
        "balance": Decimal("10000.00"),
    }


@pytest.fixture
def merchant2_data():
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Merchant Two",
        "email": "merchant2@example.com",
        "api_token": "token-merchant-2",
        "secret_key": "secret-merchant-2",
        "balance": Decimal("5000.00"),
    }


def make_signature(body: bytes, secret_key: str) -> str:
    """Compute HMAC-SHA256 signature matching app.core.security.generate_signature."""
    return hmac.new(secret_key.encode(), body, hashlib.sha256).hexdigest()
