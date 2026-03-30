"""Initial migration: create tables and seed data

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TYPE payment_status AS ENUM ('created', 'processing', 'success', 'canceled')
    """))

    op.execute(sa.text("""
        CREATE TABLE merchants (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR(255) NOT NULL,
            email       VARCHAR(255) NOT NULL UNIQUE,
            api_token   VARCHAR(255) NOT NULL UNIQUE,
            secret_key  VARCHAR(255) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("CREATE INDEX ix_merchants_api_token ON merchants (api_token)"))

    op.execute(sa.text("""
        CREATE TABLE balances (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant_id UUID NOT NULL UNIQUE REFERENCES merchants(id) ON DELETE CASCADE,
            amount      NUMERIC(18,2) NOT NULL DEFAULT 0.00,
            reserved    NUMERIC(18,2) NOT NULL DEFAULT 0.00,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE payments (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant_id         UUID NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
            provider_payment_id VARCHAR(255),
            external_invoice_id VARCHAR(255) NOT NULL UNIQUE,
            amount              NUMERIC(18,2) NOT NULL,
            status              payment_status NOT NULL DEFAULT 'created',
            callback_url        TEXT NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("CREATE INDEX ix_payments_external_invoice_id ON payments (external_invoice_id)"))

    # Seed: Merchant One — balance 10 000.00
    op.execute(sa.text("""
        INSERT INTO merchants (id, name, email, api_token, secret_key)
        VALUES ('11111111-1111-1111-1111-111111111111',
                'Merchant One', 'merchant1@example.com',
                'token-merchant-1', 'secret-merchant-1')
    """))
    op.execute(sa.text("""
        INSERT INTO balances (merchant_id, amount, reserved)
        VALUES ('11111111-1111-1111-1111-111111111111', 10000.00, 0.00)
    """))

    # Seed: Merchant Two — balance 5 000.00
    op.execute(sa.text("""
        INSERT INTO merchants (id, name, email, api_token, secret_key)
        VALUES ('22222222-2222-2222-2222-222222222222',
                'Merchant Two', 'merchant2@example.com',
                'token-merchant-2', 'secret-merchant-2')
    """))
    op.execute(sa.text("""
        INSERT INTO balances (merchant_id, amount, reserved)
        VALUES ('22222222-2222-2222-2222-222222222222', 5000.00, 0.00)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS payments"))
    op.execute(sa.text("DROP TABLE IF EXISTS balances"))
    op.execute(sa.text("DROP TABLE IF EXISTS merchants"))
    op.execute(sa.text("DROP TYPE IF EXISTS payment_status"))
