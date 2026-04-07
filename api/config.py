import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator

class Settings(BaseSettings):
    APP_NAME: str = "Ethernal Faucet API"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    DEBUG: bool = Field(default=False, env="DEBUG")

    NETWORK_NAME: str = Field(default="Arbitrum Sepolia", env="NETWORK_NAME")
    CHAIN_ID: int = Field(default=421614, env="CHAIN_ID")
    RPC_URL: str = Field(
        default="https://sepolia-rollup.arbitrum.io/rpc",
        env="RPC_URL"
    )
    EXPLORER_URL: str = Field(
        default="https://sepolia.arbiscan.io",
        env="EXPLORER_URL"
    )

    CONTRACT_ADDRESS: str = Field(default="", env="CONTRACT_ADDRESS")
    FAUCET_ADDRESS: str = Field(default="", env="FAUCET_ADDRESS")
    FAUCET_PRIVATE_KEY: str = Field(default="", env="FAUCET_PRIVATE_KEY")
    FAUCET_ETH_AMOUNT: float = Field(default=0.1, env="FAUCET_ETH_AMOUNT")

    @validator("CONTRACT_ADDRESS", "FAUCET_ADDRESS")
    def validate_addresses(cls, v):
        if v and not v.startswith("0x"):
            raise ValueError("Address must start with 0x")
        return v

    FAUCET_AMOUNT: float = Field(default=10000.0, env="FAUCET_AMOUNT")
    FAUCET_MIN_BALANCE_ALERT: float = Field(
        default=10000.0,
        env="FAUCET_MIN_BALANCE_ALERT"
    )

    RATE_LIMIT_IP_SECONDS: int = Field(default=3600, env="RATE_LIMIT_IP_SECONDS")
    RATE_LIMIT_WALLET_SECONDS: int = Field(
        default=86400,
        env="RATE_LIMIT_WALLET_SECONDS"
    )
    RATE_LIMIT_ENABLED: bool = Field(default=True, env="RATE_LIMIT_ENABLED")

    DATABASE_URL: Optional[str] = Field(default=None, env="DATABASE_URL")
    DB_POOL_SIZE: int = Field(default=10, env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int = Field(default=5, env="DB_MAX_OVERFLOW")
    DB_ECHO: bool = Field(default=False, env="DB_ECHO")

    REDIS_URL: Optional[str] = Field(default=None, env="REDIS_URL")
    REDIS_MAX_CONNECTIONS: int = Field(default=10, env="REDIS_MAX_CONNECTIONS")
    REDIS_DECODE_RESPONSES: bool = Field(default=True, env="REDIS_DECODE_RESPONSES")

    CELERY_BROKER_URL: Optional[str] = Field(default=None, env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = Field(
        default=None,
        env="CELERY_RESULT_BACKEND"
    )

    @validator("CELERY_BROKER_URL", always=True)
    def set_celery_broker(cls, v, values):
        return v or values.get("REDIS_URL")

    @validator("CELERY_RESULT_BACKEND", always=True)
    def set_celery_backend(cls, v, values):
        return v or values.get("REDIS_URL")

    TURNSTILE_ENABLED: bool = Field(default=False, env="TURNSTILE_ENABLED")
    TURNSTILE_SECRET_KEY: Optional[str] = Field(
        default=None,
        env="TURNSTILE_SECRET_KEY"
    )
    TURNSTILE_VERIFY_URL: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    SENTRY_DSN: Optional[str] = Field(default=None, env="SENTRY_DSN")
    SENTRY_ENABLED: bool = Field(default=False, env="SENTRY_ENABLED")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(
        default=1.0,
        env="SENTRY_TRACES_SAMPLE_RATE"
    )
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    CORS_ORIGINS_STR: Optional[str] = Field(default=None, env="CORS_ORIGINS")

    @property
    def CORS_ORIGINS(self) -> List[str]:
        defaults = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
        if self.CORS_ORIGINS_STR:
            extra = [o.strip() for o in self.CORS_ORIGINS_STR.split(",") if o.strip()]
            all_origins = list(dict.fromkeys(extra + defaults))
            return all_origins
        return defaults

    API_HOST: str = Field(default="0.0.0.0", env="API_HOST")
    API_PORT: int = Field(default=8000, env="PORT")
    WORKERS: int = Field(default=2, env="WORKERS")

    API_KEY_HEADER: str = "X-API-Key"
    ADMIN_API_KEY: Optional[str] = Field(default=None, env="ADMIN_API_KEY")

    ENABLE_DB: bool = Field(default=True, env="ENABLE_DB")
    ENABLE_REDIS: bool = Field(default=False, env="ENABLE_REDIS")
    ENABLE_CELERY: bool = Field(default=False, env="ENABLE_CELERY")

    @validator("ENABLE_DB", always=True)
    def check_db_enabled(cls, v, values):
        return v and bool(values.get("DATABASE_URL"))

    @validator("ENABLE_REDIS", always=True)
    def check_redis_enabled(cls, v, values):
        return v and bool(values.get("REDIS_URL"))

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

settings = Settings()
