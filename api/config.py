"""
config.py — Ethernal Faucet API

NETWORK_CONFIGS se construye automáticamente leyendo la carpeta deployments/.
Al deployar en una nueva chain solo hay que:
  1. Commitear deployments/<chain>/<network>/MockUSDC.json  (con "address" y "chain_id")
  2. Agregar las variables de entorno correspondientes en Render/Railway/etc:
       <CHAIN>_<NETWORK>_RPC_URL         ej: BASE_MAINNET_RPC_URL
       <CHAIN>_<NETWORK>_CONTRACT_ADDRESS  (opcional — se lee del JSON si no está)
       <CHAIN>_<NETWORK>_ETH_AMOUNT        (opcional — default: FAUCET_DEFAULT_ETH_AMOUNT)

  Sin modificar ningún archivo Python.

Convención de env vars (derivada del path del deployment):
  deployments/arbitrum/sepolia/  →  ARBITRUM_SEPOLIA_*
  deployments/ethereum/sepolia/  →  ETHEREUM_SEPOLIA_*   (alias: SEPOLIA_*)
  deployments/base/mainnet/      →  BASE_MAINNET_*
  deployments/optimism/mainnet/  →  OPTIMISM_MAINNET_*
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import Field, validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Metadatos de chains conocidas 
# Solo se usa para enriquecer los datos (nombre legible, explorer).
# Si una chain no está aquí, se usan defaults razonables y el servicio igual funciona.

_CHAIN_METADATA: Dict[int, dict] = {
    # Testnets
    11155111: {"name": "Sepolia",          "explorer": "https://sepolia.etherscan.io"},
    421614:   {"name": "Arbitrum Sepolia", "explorer": "https://sepolia.arbiscan.io"},
    84532:    {"name": "Base Sepolia",     "explorer": "https://sepolia.basescan.org"},
    11155420: {"name": "OP Sepolia",       "explorer": "https://sepolia-optimism.etherscan.io"},
    80002:    {"name": "Amoy (Polygon)",   "explorer": "https://amoy.polygonscan.com"},
    # Mainnets
    1:        {"name": "Ethereum",         "explorer": "https://etherscan.io"},
    42161:    {"name": "Arbitrum One",     "explorer": "https://arbiscan.io"},
    8453:     {"name": "Base",             "explorer": "https://basescan.org"},
    10:       {"name": "Optimism",         "explorer": "https://optimistic.etherscan.io"},
    137:      {"name": "Polygon",          "explorer": "https://polygonscan.com"},
    43114:    {"name": "Avalanche",        "explorer": "https://snowtrace.io"},
}

# ETH defaults por tipo de red (gas mucho más barato en L2)
_ETH_DEFAULTS: Dict[str, float] = {
    "mainnet":  0.001,
    "sepolia":  0.05,
    "default":  0.01,
}

def _env_prefix(chain: str, network: str) -> str:
    """
    Convierte (chain, network) en el prefijo de variable de entorno.
    Ejemplos:
      arbitrum, sepolia  →  ARBITRUM_SEPOLIA
      ethereum, sepolia  →  ETHEREUM_SEPOLIA
      base,     mainnet  →  BASE_MAINNET
    """
    return f"{chain.upper()}_{network.upper()}".replace("-", "_")

def _network_key(chain: str, network: str) -> str:
    """
    Clave interna usada en NETWORK_CONFIGS y en las rutas de la API.
    Ejemplos:
      arbitrum, sepolia  →  arbitrum-sepolia
      base,     mainnet  →  base-mainnet
    """
    return f"{chain}-{network}"

def _discover_networks() -> Dict[str, dict]:
    """
    Lee deployments/<chain>/<network>/MockUSDC.json y construye NETWORK_CONFIGS.
    Cada entrada del dict tiene la forma que espera get_network_config() y FaucetService.
    """
    repo_root   = Path(__file__).parent.parent
    deployments = repo_root / "deployments"
    configs: Dict[str, dict] = {}

    if not deployments.exists():
        logger.warning(
            f"Directorio deployments/ no encontrado en {repo_root} — "
            "NETWORK_CONFIGS vacío, el servicio no podrá procesar requests."
        )
        return configs

    for json_path in sorted(deployments.rglob("MockUSDC.json")):
        # Espera estructura: deployments/<chain>/<network>/MockUSDC.json
        parts = json_path.relative_to(deployments).parts
        if len(parts) != 3:
            logger.warning(
                f"Estructura inesperada en {json_path} "
                f"(esperado: chain/network/MockUSDC.json) — ignorando"
            )
            continue
        chain, network, _ = parts
        key    = _network_key(chain, network)
        prefix = _env_prefix(chain, network)
        try:
            with open(json_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error leyendo {json_path}: {e} — ignorando")
            continue
        chain_id = data.get("chain_id")
        if not chain_id:
            logger.error(f"{json_path} no tiene 'chain_id' — ignorando")
            continue

        # Dirección del contrato: primero el JSON, override posible via env
        contract_from_json = data.get("address", "")

        # Metadatos de la chain
        meta         = _CHAIN_METADATA.get(chain_id, {})
        display_name = meta.get("name") or f"{chain.title()} {network.title()}"
        explorer_url = meta.get("explorer") or data.get("explorer", "")

        # ETH default según tipo de red
        eth_default = _ETH_DEFAULTS.get(network, _ETH_DEFAULTS["default"])

        configs[key] = {
            "chain":            chain,
            "network":          network,
            "name":             display_name,
            "chain_id":         chain_id,
            "explorer_url":     explorer_url,
            # Nombres de las env vars que get_network_config() resolverá en runtime
            "rpc_url_env":      f"{prefix}_RPC_URL",
            "contract_env":     f"{prefix}_CONTRACT_ADDRESS",
            "eth_amount_env":   f"{prefix}_ETH_AMOUNT",
            # Valor de contrato embebido como fallback (no requiere env var)
            "contract_address_default": contract_from_json,
            # ETH default si no está seteada la env var
            "eth_amount_default": eth_default,
            # Path al JSON para referencia y recarga de ABI
            "deployment_path":  str(json_path),
        }
        logger.debug(
            f"Red descubierta: {key} | chain_id={chain_id} | "
            f"env_prefix={prefix} | contrato={contract_from_json or '(solo env var)'}"
        )
    if configs:
        logger.info(f"Redes descubiertas desde deployments/: {sorted(configs)}")
    else:
        logger.error(
            "No se encontró ningún MockUSDC.json válido en deployments/ — "
            "verificar estructura: deployments/<chain>/<network>/MockUSDC.json"
        )

    return configs

# Se construye una sola vez al importar el módulo
NETWORK_CONFIGS: Dict[str, dict] = _discover_networks()


# Settings 
class Settings(BaseSettings):
    APP_NAME:    str = "Ethernal Faucet API"
    APP_VERSION: str = "3.0.0"
    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    DEBUG:       bool = Field(default=False,        env="DEBUG")

    # Wallet faucet 
    FAUCET_ADDRESS:     str = Field(default="", env="FAUCET_ADDRESS")
    FAUCET_PRIVATE_KEY: str = Field(default="", env="FAUCET_PRIVATE_KEY")

    # Montos 
    FAUCET_AMOUNT:            float = Field(default=10000.0, env="FAUCET_AMOUNT")
    FAUCET_MIN_BALANCE_ALERT: float = Field(default=10000.0, env="FAUCET_MIN_BALANCE_ALERT")
    # ETH default global si no hay env var específica por red
    FAUCET_DEFAULT_ETH_AMOUNT: float = Field(default=0.01,  env="FAUCET_DEFAULT_ETH_AMOUNT")

    # Rate limiting 
    RATE_LIMIT_IP_SECONDS:     int  = Field(default=3600,  env="RATE_LIMIT_IP_SECONDS")
    RATE_LIMIT_WALLET_SECONDS: int  = Field(default=86400, env="RATE_LIMIT_WALLET_SECONDS")
    RATE_LIMIT_ENABLED:        bool = Field(default=True,  env="RATE_LIMIT_ENABLED")

    # Base de datos 
    DATABASE_URL:    Optional[str] = Field(default=None,  env="DATABASE_URL")
    DB_POOL_SIZE:    int           = Field(default=10,    env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int           = Field(default=5,     env="DB_MAX_OVERFLOW")
    DB_ECHO:         bool          = Field(default=False,  env="DB_ECHO")

    # Redis
    REDIS_URL:              Optional[str] = Field(default=None, env="REDIS_URL")
    REDIS_MAX_CONNECTIONS:  int           = Field(default=10,   env="REDIS_MAX_CONNECTIONS")
    REDIS_DECODE_RESPONSES: bool          = Field(default=True, env="REDIS_DECODE_RESPONSES")

    # Celery 
    CELERY_BROKER_URL:     Optional[str] = Field(default=None, env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = Field(default=None, env="CELERY_RESULT_BACKEND")

    @validator("CELERY_BROKER_URL", always=True)
    def set_celery_broker(cls, v, values):
        return v or values.get("REDIS_URL")

    @validator("CELERY_RESULT_BACKEND", always=True)
    def set_celery_backend(cls, v, values):
        return v or values.get("REDIS_URL")

    # Turnstile 
    TURNSTILE_ENABLED:    bool          = Field(default=False, env="TURNSTILE_ENABLED")
    TURNSTILE_SECRET_KEY: Optional[str] = Field(default=None, env="TURNSTILE_SECRET_KEY")
    TURNSTILE_VERIFY_URL: str           = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    # Sentry 
    SENTRY_DSN:                Optional[str] = Field(default=None, env="SENTRY_DSN")
    SENTRY_ENABLED:            bool          = Field(default=False, env="SENTRY_ENABLED")
    SENTRY_TRACES_SAMPLE_RATE: float         = Field(default=1.0,  env="SENTRY_TRACES_SAMPLE_RATE")

    # Logging / CORS 
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

    # Servidor 
    API_HOST: str = Field(default="0.0.0.0", env="API_HOST")
    API_PORT: int = Field(default=8000,      env="PORT")
    WORKERS:  int = Field(default=2,         env="WORKERS")

    # Admin 
    API_KEY_HEADER: str           = "X-API-Key"
    ADMIN_API_KEY:  Optional[str] = Field(default=None, env="ADMIN_API_KEY")

    # Feature flags 
    ENABLE_DB:     bool = Field(default=True,  env="ENABLE_DB")
    ENABLE_REDIS:  bool = Field(default=False, env="ENABLE_REDIS")
    ENABLE_CELERY: bool = Field(default=False, env="ENABLE_CELERY")

    @validator("ENABLE_DB", always=True)
    def check_db_enabled(cls, v, values):
        return v and bool(values.get("DATABASE_URL"))

    @validator("ENABLE_REDIS", always=True)
    def check_redis_enabled(cls, v, values):
        return v and bool(values.get("REDIS_URL"))

    #  Network resolution
    def get_network_config(self, network: str) -> dict:
        """
        Devuelve la config completa y resuelta de una red.

        Resolución de valores en orden de prioridad:
          1. Variable de entorno específica de la red  (ej: ARBITRUM_SEPOLIA_RPC_URL)
          2. Valor embebido en el JSON de deployment   (solo para contract_address)
          3. Default global de Settings                (solo para eth_amount)

        Lanza ValueError si falta RPC URL (sin ella no hay nada que hacer).
        El contrato se puede tomar del JSON, así que no es obligatorio en env.
        """
        cfg = NETWORK_CONFIGS.get(network)
        if not cfg:
            raise ValueError(
                f"Red '{network}' no encontrada. "
                f"Verificar deployments/ o redes disponibles: {sorted(NETWORK_CONFIGS)}"
            )

        # RPC URL — obligatoria, no tiene fallback sensato
        rpc_url = os.getenv(cfg["rpc_url_env"], "").strip()
        if not rpc_url:
            raise ValueError(
                f"Falta variable de entorno {cfg['rpc_url_env']} "
                f"requerida para la red '{network}'"
            )

        # Contract address — env var tiene prioridad; fallback al valor del JSON
        contract_address = (
            os.getenv(cfg["contract_env"], "").strip()
            or cfg.get("contract_address_default", "")
        )
        if not contract_address:
            raise ValueError(
                f"No se encontró contract address para '{network}'. "
                f"Setear {cfg['contract_env']} o verificar el campo 'address' "
                f"en {cfg['deployment_path']}"
            )

        # ETH amount — env var → default del JSON → default global
        eth_amount_str = os.getenv(cfg["eth_amount_env"], "").strip()
        try:
            eth_amount = float(eth_amount_str) if eth_amount_str else cfg["eth_amount_default"]
        except ValueError:
            logger.warning(
                f"{cfg['eth_amount_env']} tiene valor inválido '{eth_amount_str}' "
                f"— usando default {cfg['eth_amount_default']}"
            )
            eth_amount = cfg["eth_amount_default"]

        return {
            **cfg,
            "rpc_url":          rpc_url,
            "contract_address": contract_address,
            "eth_amount":       eth_amount,
        }

    @property
    def supported_networks(self) -> List[str]:
        return sorted(NETWORK_CONFIGS.keys())

    class Config:
        env_file       = ".env"
        case_sensitive = True
        extra          = "ignore"

settings = Settings()