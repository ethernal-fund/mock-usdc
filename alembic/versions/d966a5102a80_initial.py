"""faucet schema inicial

Revision ID: d966a5102a80
Revises:
Create Date: 2026-04-08 00:46:09.242503

Crea el schema 'faucet' y todas sus tablas de forma aislada del schema 'public'
que usa la app principal. Esto garantiza que los servicios nunca colisionen.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision:       str                            = "d966a5102a80"
down_revision:  Union[str, Sequence[str], None] = None
branch_labels:  Union[str, Sequence[str], None] = None
depends_on:     Union[str, Sequence[str], None] = None

_SCHEMA = "faucet"


def upgrade() -> None:
    # ── 1. Crear schema propio ────────────────────────────────────────────────
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    # ── 2. faucet_requests ────────────────────────────────────────────────────
    op.create_table(
        "faucet_requests",
        sa.Column("id",                 sa.Integer(),                   primary_key=True, autoincrement=True),
        sa.Column("wallet_address",     sa.String(42),                  nullable=False),
        sa.Column("ip_address",         sa.String(45),                  nullable=True),
        sa.Column("user_agent",         sa.String(512),                 nullable=True),
        sa.Column("amount",             sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("eth_amount",         sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("tx_hash",            sa.String(66),                  nullable=True,  unique=True),
        sa.Column("eth_tx_hash",        sa.String(66),                  nullable=True),
        sa.Column("status",             sa.String(20),                  nullable=False, server_default="pending"),
        sa.Column("error_message",      sa.Text(),                      nullable=True),
        sa.Column("created_at",         sa.DateTime(timezone=True),     nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at",       sa.DateTime(timezone=True),     nullable=True),
        sa.Column("turnstile_verified", sa.Boolean(),                   nullable=False, server_default=sa.false()),
        sa.Column("risk_score",         sa.Integer(),                   nullable=False, server_default="0"),
        schema=_SCHEMA,
    )
    op.create_index("idx_faucet_wallet_address", "faucet_requests", ["wallet_address"], schema=_SCHEMA)
    op.create_index("idx_faucet_ip_address",     "faucet_requests", ["ip_address"],     schema=_SCHEMA)
    op.create_index("idx_faucet_tx_hash",        "faucet_requests", ["tx_hash"],        schema=_SCHEMA, unique=True)
    op.create_index("idx_faucet_status",         "faucet_requests", ["status"],         schema=_SCHEMA)
    op.create_index("idx_faucet_created_at",     "faucet_requests", ["created_at"],     schema=_SCHEMA)
    op.create_index("idx_faucet_wallet_created", "faucet_requests", ["wallet_address", "created_at"], schema=_SCHEMA)
    op.create_index("idx_faucet_ip_created",     "faucet_requests", ["ip_address",     "created_at"], schema=_SCHEMA)
    op.create_index("idx_faucet_status_created", "faucet_requests", ["status",         "created_at"], schema=_SCHEMA)

    # ── 3. blocked_addresses ──────────────────────────────────────────────────
    op.create_table(
        "blocked_addresses",
        sa.Column("id",            sa.Integer(),               primary_key=True, autoincrement=True),
        sa.Column("address_type",  sa.String(10),              nullable=False),
        sa.Column("address_value", sa.String(100),             nullable=False, unique=True),
        sa.Column("reason",        sa.Text(),                  nullable=False),
        sa.Column("blocked_by",    sa.String(42),              nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True), nullable=True,  server_default=sa.text("now()")),
        sa.Column("expires_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active",     sa.Boolean(),               nullable=False, server_default=sa.true()),
        schema=_SCHEMA,
    )
    op.create_index("idx_blocked_address_value", "blocked_addresses", ["address_value"], schema=_SCHEMA, unique=True)
    op.create_index("idx_blocked_is_active",     "blocked_addresses", ["is_active"],     schema=_SCHEMA)

    # ── 4. faucet_stats ───────────────────────────────────────────────────────
    op.create_table(
        "faucet_stats",
        sa.Column("id",                     sa.Integer(),                      primary_key=True, autoincrement=True),
        sa.Column("date",                   sa.DateTime(timezone=True),        nullable=False, unique=True),
        sa.Column("total_requests",         sa.Integer(),                      nullable=False, server_default="0"),
        sa.Column("successful_requests",    sa.Integer(),                      nullable=False, server_default="0"),
        sa.Column("failed_requests",        sa.Integer(),                      nullable=False, server_default="0"),
        sa.Column("rate_limited_requests",  sa.Integer(),                      nullable=False, server_default="0"),
        sa.Column("total_usdc_distributed", sa.Numeric(precision=18, scale=6), nullable=False, server_default="0"),
        sa.Column("total_eth_distributed",  sa.Numeric(precision=18, scale=8), nullable=False, server_default="0"),
        sa.Column("unique_wallets",         sa.Integer(),                      nullable=False, server_default="0"),
        sa.Column("unique_ips",             sa.Integer(),                      nullable=False, server_default="0"),
        sa.Column("updated_at",             sa.DateTime(timezone=True),        nullable=False, server_default=sa.text("now()")),
        schema=_SCHEMA,
    )
    op.create_index("idx_faucet_stats_date", "faucet_stats", ["date"], schema=_SCHEMA, unique=True)


def downgrade() -> None:
    op.drop_table("faucet_stats",      schema=_SCHEMA)
    op.drop_table("blocked_addresses", schema=_SCHEMA)
    op.drop_table("faucet_requests",   schema=_SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA}")