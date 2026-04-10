from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime,
    Boolean, Text, Index,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

_SCHEMA = "faucet"

class FaucetRequest(Base):
    __tablename__ = "faucet_requests"
    __table_args__ = (
        Index("idx_faucet_wallet_created", "wallet_address", "created_at"),
        Index("idx_faucet_ip_created",     "ip_address",     "created_at"),
        Index("idx_faucet_status_created", "status",         "created_at"),
        {"schema": _SCHEMA},
    )

    id             = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(42),  nullable=False, index=True)
    ip_address     = Column(String(45),  nullable=True,  index=True)   # IPv6-compatible
    user_agent     = Column(String(512), nullable=True)
    amount         = Column(Numeric(precision=18, scale=6), nullable=False)
    eth_amount     = Column(Numeric(precision=18, scale=6), nullable=True)
    tx_hash        = Column(String(66), nullable=True, unique=True, index=True)
    eth_tx_hash    = Column(String(66), nullable=True)
    status         = Column(String(20), nullable=False, default="pending", index=True)
    error_message  = Column(Text, nullable=True)

    created_at     = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    completed_at   = Column(DateTime(timezone=True), nullable=True)
    turnstile_verified = Column(Boolean, default=False, nullable=False)
    risk_score         = Column(Integer, default=0,     nullable=False)  # 0-100

    def __repr__(self) -> str:
        return (
            f"<FaucetRequest(id={self.id}, wallet={self.wallet_address}, "
            f"status={self.status})>"
        )

class BlockedAddress(Base):
    """Wallets o IPs bloqueadas manualmente por el admin."""

    __tablename__ = "blocked_addresses"
    __table_args__ = {"schema": _SCHEMA}

    id            = Column(Integer, primary_key=True, autoincrement=True)
    address_type  = Column(String(10),  nullable=False)           # 'wallet' | 'ip'
    address_value = Column(String(100), nullable=False, unique=True, index=True)
    reason        = Column(Text,        nullable=False)
    blocked_by    = Column(String(42),  nullable=True)            # admin wallet
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    expires_at    = Column(DateTime(timezone=True), nullable=True) # null = permanente
    is_active     = Column(Boolean, default=True, nullable=False, index=True)

    def __repr__(self) -> str:
        return (
            f"<BlockedAddress(type={self.address_type}, "
            f"value={self.address_value})>"
        )

class FaucetStats(Base):
    """Estadísticas diarias del faucet (para dashboard admin)."""

    __tablename__ = "faucet_stats"
    __table_args__ = {"schema": _SCHEMA}

    id   = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime(timezone=True), nullable=False, unique=True, index=True)
    total_requests          = Column(Integer, default=0, nullable=False)
    successful_requests     = Column(Integer, default=0, nullable=False)
    failed_requests         = Column(Integer, default=0, nullable=False)
    rate_limited_requests   = Column(Integer, default=0, nullable=False)
    total_usdc_distributed  = Column(Numeric(precision=18, scale=6), default=0, nullable=False)
    total_eth_distributed   = Column(Numeric(precision=18, scale=8), default=0, nullable=False)
    unique_wallets          = Column(Integer, default=0, nullable=False)
    unique_ips              = Column(Integer, default=0, nullable=False)

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<FaucetStats(date={self.date}, requests={self.total_requests})>"