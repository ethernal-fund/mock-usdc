from datetime import datetime
from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, Index, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class FaucetRequest(Base):
    __tablename__ = "faucet_requests"
    
    id = Column(Integer, primary_key=True, index=True)

    wallet_address = Column(String(42), nullable=False, index=True)
    ip_address = Column(String(45), nullable=True, index=True)  # IPv6 compatible
    user_agent = Column(String(512), nullable=True)

    amount = Column(Numeric(precision=18, scale=6), nullable=False)
    tx_hash = Column(String(66), nullable=True, index=True, unique=True)

    status = Column(String(20), nullable=False, default="pending", index=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    turnstile_verified = Column(Boolean, default=False)
    risk_score = Column(Integer, default=0)  # 0-100, higher = more suspicious

    __table_args__ = (
        Index('idx_wallet_created', 'wallet_address', 'created_at'),
        Index('idx_ip_created', 'ip_address', 'created_at'),
        Index('idx_status_created', 'status', 'created_at'),
    )
    
    def __repr__(self):
        return (
            f"<FaucetRequest(id={self.id}, wallet={self.wallet_address}, "
            f"status={self.status})>"
        )


class BlockedAddress(Base):
    __tablename__ = "blocked_addresses"
    
    id = Column(Integer, primary_key=True, index=True)

    address_type = Column(String(10), nullable=False)  # 'wallet' or 'ip'
    address_value = Column(String(100), nullable=False, unique=True, index=True)

    reason = Column(Text, nullable=False)
    blocked_by = Column(String(42), nullable=True)  # admin wallet

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)  # null = permanent
    
    is_active = Column(Boolean, default=True, index=True)
    
    def __repr__(self):
        return (
            f"<BlockedAddress(type={self.address_type}, "
            f"value={self.address_value})>"
        )

class FaucetStats(Base):
    __tablename__ = "faucet_stats"
    
    id = Column(Integer, primary_key=True, index=True)

    date = Column(DateTime(timezone=True), nullable=False, unique=True, index=True)

    total_requests = Column(Integer, default=0)
    successful_requests = Column(Integer, default=0)
    failed_requests = Column(Integer, default=0)
    rate_limited_requests = Column(Integer, default=0)
    
    total_amount_distributed = Column(Numeric(precision=18, scale=6), default=0)
    unique_wallets = Column(Integer, default=0)
    unique_ips = Column(Integer, default=0)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )
    
    def __repr__(self):
        return f"<FaucetStats(date={self.date}, requests={self.total_requests})>"