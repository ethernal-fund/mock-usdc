import os
from typing import List, Optional, Dict
from pydantic_settings import BaseSettings
from pydantic import Field, validator

NETWORK_CONFIGS: Dict[str, dict] = {
    "sepolia": {
        "name":         "Sepolia",
        "chain_id":     11155111,
        "explorer_url": "https://sepolia.etherscan.io",
        "rpc_url_env":  "SEPOLIA_RPC_URL",
        "contract_env": "SEPOLIA_CONTRACT_ADDRESS",
    },
    "arbitrum-sepolia": {
        "name":         "Arbitrum Sepolia",
        "chain_id":     421614,
        "explorer_url": "https://sepolia.arbiscan.io",
        "rpc_url_env":  "ARB_SEPOLIA_RPC_URL",
        "contract_env": "ARB_SEPOLIA_CONTRACT_ADDRESS",
    },
}

class Settings(BaseSettings):
    APP_NAME:    str = "Ethernal Faucet API"
    APP_VERSION: str = "3.0.0"
    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    DEBUG:       bool = Field(default=False,       env="DEBUG")

    # ── Variables por red (una por cada chain) ────────────────────────────────
    SEPOLIA_RPC_URL:             str = Field(default="", env="SEPOLIA_RPC_URL")
    SEPOLIA_CONTRACT_ADDRESS:    str = Field(default="", env="SEPOLIA_CONTRACT_ADDRESS")

    ARB_SEPOLIA_RPC_URL:         str = Field(default="", env="ARB_SEPOLIA_RPC_URL")
    ARB_SEPOLIA_CONTRACT_ADDRESS: str = Field(default="", env="ARB_SEPOLIA_CONTRACT_ADDRESS")

    # ── Wallet faucet (compartida entre redes o una por red) ──────────────────
    # Una sola wallet puede operar en ambas redes si tiene saldo en cada una.
    FAUCET_ADDRESS:     str = Field(default="", env="FAUCET_ADDRESS")
    FAUCET_PRIVATE_KEY: str = Field(default="", env="FAUCET_PRIVATE_KEY")

    # ── Montos ────────────────────────────────────────────────────────────────
    FAUCET_AMOUNT:            float = Field(default=10000.0, env="FAUCET_AMOUNT")
    FAUCET_MIN_BALANCE_ALERT: float = Field(default=10000.0, env="FAUCET_MIN_BALANCE_ALERT")

    # ETH por red (el gas en Arbitrum es mucho más barato)
    SEPOLIA_ETH_AMOUNT:     float = Field(default=0.05, env="SEPOLIA_ETH_AMOUNT")
    ARB_SEPOLIA_ETH_AMOUNT: float = Field(default=0.01, env="ARB_SEPOLIA_ETH_AMOUNT")

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_IP_SECONDS:     int  = Field(default=3600,  env="RATE_LIMIT_IP_SECONDS")
    RATE_LIMIT_WALLET_SECONDS: int  = Field(default=86400, env="RATE_LIMIT_WALLET_SECONDS")
    RATE_LIMIT_ENABLED:        bool = Field(default=True,  env="RATE_LIMIT_ENABLED")

    # ── Base de datos ─────────────────────────────────────────────────────────
    DATABASE_URL:    Optional[str] = Field(default=None, env="DATABASE_URL")
    DB_POOL_SIZE:    int           = Field(default=10,   env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int           = Field(default=5,    env="DB_MAX_OVERFLOW")
    DB_ECHO:         bool          = Field(default=False, env="DB_ECHO")

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL:              Optional[str] = Field(default=None,  env="REDIS_URL")
    REDIS_MAX_CONNECTIONS:  int           = Field(default=10,    env="REDIS_MAX_CONNECTIONS")
    REDIS_DECODE_RESPONSES: bool          = Field(default=True,  env="REDIS_DECODE_RESPONSES")

    # ── Celery ────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL:    Optional[str] = Field(default=None, env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = Field(default=None, env="CELERY_RESULT_BACKEND")

    @validator("CELERY_BROKER_URL", always=True)
    def set_celery_broker(cls, v, values):
        return v or values.get("REDIS_URL")

    @validator("CELERY_RESULT_BACKEND", always=True)
    def set_celery_backend(cls, v, values):
        return v or values.get("REDIS_URL")

    # ── Turnstile ─────────────────────────────────────────────────────────────
    TURNSTILE_ENABLED:   bool          = Field(default=False, env="TURNSTILE_ENABLED")
    TURNSTILE_SECRET_KEY: Optional[str] = Field(default=None, env="TURNSTILE_SECRET_KEY")
    TURNSTILE_VERIFY_URL: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    # ── Sentry ────────────────────────────────────────────────────────────────
    SENTRY_DSN:                Optional[str] = Field(default=None, env="SENTRY_DSN")
    SENTRY_ENABLED:            bool          = Field(default=False, env="SENTRY_ENABLED")
    SENTRY_TRACES_SAMPLE_RATE: float         = Field(default=1.0,  env="SENTRY_TRACES_SAMPLE_RATE")

    # ── Logging / CORS ────────────────────────────────────────────────────────
    LOG_LEVEL:        str           = Field(default="INFO", env="LOG_LEVEL")
    CORS_ORIGINS_STR: Optional[str] = Field(default=None,  env="CORS_ORIGINS")

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
            return list(dict.fromkeys(extra + defaults))
        return defaults

    # ── Servidor ──────────────────────────────────────────────────────────────
    API_HOST: str = Field(default="0.0.0.0", env="API_HOST")
    API_PORT: int = Field(default=8000,      env="PORT")
    WORKERS:  int = Field(default=2,         env="WORKERS")

    # ── Admin ─────────────────────────────────────────────────────────────────
    API_KEY_HEADER: str           = "X-API-Key"
    ADMIN_API_KEY:  Optional[str] = Field(default=None, env="ADMIN_API_KEY")

    # ── Feature flags ─────────────────────────────────────────────────────────
    ENABLE_DB:     bool = Field(default=True,  env="ENABLE_DB")
    ENABLE_REDIS:  bool = Field(default=False, env="ENABLE_REDIS")
    ENABLE_CELERY: bool = Field(default=False, env="ENABLE_CELERY")

    @validator("ENABLE_DB", always=True)
    def check_db_enabled(cls, v, values):
        return v and bool(values.get("DATABASE_URL"))

    @validator("ENABLE_REDIS", always=True)
    def check_redis_enabled(cls, v, values):
        return v and bool(values.get("REDIS_URL"))

    def get_network_config(self, network: str) -> dict:
        """Devuelve la config completa de una red, inyectando los valores de env."""
        cfg = NETWORK_CONFIGS.get(network)
        if not cfg:
            raise ValueError(f"Red no soportada: '{network}'. Opciones: {list(NETWORK_CONFIGS)}")

        rpc_url  = getattr(self, cfg["rpc_url_env"].replace("SEPOLIA_RPC_URL", "SEPOLIA_RPC_URL"), None)
        # Resolvemos dinámicamente para no hardcodear cada campo
        rpc_url          = os.getenv(cfg["rpc_url_env"], "")
        contract_address = os.getenv(cfg["contract_env"], "")

        if not rpc_url:
            raise ValueError(f"Falta la variable de entorno {cfg['rpc_url_env']}")
        if not contract_address:
            raise ValueError(f"Falta la variable de entorno {cfg['contract_env']}")

        eth_amount_map = {
            "sepolia":          self.SEPOLIA_ETH_AMOUNT,
            "arbitrum-sepolia": self.ARB_SEPOLIA_ETH_AMOUNT,
        }

        return {
            **cfg,
            "rpc_url":          rpc_url,
            "contract_address": contract_address,
            "eth_amount":       eth_amount_map[network],
        }

    @property
    def supported_networks(self) -> List[str]:
        return list(NETWORK_CONFIGS.keys())
    class Config:
        env_file     = ".env"
        case_sensitive = True
        extra        = "ignore"

settings = Settings()