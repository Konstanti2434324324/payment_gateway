from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.redis_client import get_redis, close_redis
from app.api.v1 import merchant, payments, webhooks


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_redis()
    yield
    await close_redis()


app = FastAPI(title="Payment Gateway", lifespan=lifespan)

app.include_router(merchant.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
