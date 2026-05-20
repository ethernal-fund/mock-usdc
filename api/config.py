import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

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

_ETH_DEFAULTS: Dict[str, float] = {
    "mainnet": 0.001,
    "sepolia": 0.05,
    "default": 0.01,
}

# ── Aliases de RPC URL ────────────────────────────────────────────────────────
# Permite que env vars con nombres alternativos sean reconocidas automáticamente.
# Formato: { network_key: [alias1, alias2, ...] }
# La env var canónica (<PREFIX>_RPC_URL) siempre tiene prioridad.
_RPC_URL_ALIASES: Dict[str, List[str]] = {
    "ethereum-sepolia": ["SEPOLIA_RPC_URL"],
    "arbitrum-sepolia": ["ARBITRUM_SEPOLIA_RPC_URL"],  # ya es el canónico, solo por claridad
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

def _resolve_rpc_url(network_key: str, canonical_env: str) -> str:
    """
    Resuelve la RPC URL probando primero la env var canónica,
    luego los aliases definidos en _RPC_URL_ALIASES.
    Devuelve la primera que tenga valor, o "" si ninguna está seteada.
    """
    # 1. Env var canónica tiene siempre prioridad
    value = os.getenv(canonical_env, "").strip()
    if value:
        return value

    # 2. Aliases
    for alias in _RPC_URL_ALIASES.get(network_key, []):
        value = os.getenv(alias, "").strip()
        if value:
            logger.debug(
                f"[{network_key}] RPC URL resuelta desde alias '{alias}' "
                f"(canónica '{canonical_env}' no seteada)"
            )
            return value

    return ""

def _discover_networks() -> Dict[str, dict]:
    """
    Lee deployments/<chain>/<network>/MockUSDC.json y construye NETWORK_CONFIGS.
    Cada entrada del dict tiene la forma que espera get_network_config() y FaucetService.

    Estructura requerida:
      deployments/
        arbitrum/sepolia/MockUSDC.json   →  key: "arbitrum-sepolia"
        ethereum/sepolia/MockUSDC.json   →  key: "ethereum-sepolia"
        base/mainnet/MockUSDC.json       →  key: "base-mainnet"

    Archivos en paths con depth != 3 son ignorados con warning.
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
        parts = json_path.relative_to(deployments).parts
        if len(parts) != 3:
            logger.warning(
                f"Estructura inesperada en {json_path} "
                f"(esperado: deployments/<chain>/<network>/MockUSDC.json, "
                f"obtenido depth={len(parts)}) — ignorando"
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
        contract_from_json = data.get("address", "").strip()

        # Metadatos de la chain
        meta         = _CHAIN_METADATA.get(chain_id, {})
        display_name = meta.get("name") or f"{chain.title()} {network.title()}"
        explorer_url = meta.get("explorer") or data.get("explorer", "")
        eth_default  = _ETH_DEFAULTS.get(network, _ETH_DEFAULTS["default"])

        configs[key] = {
            "chain":            chain,
            "network":          network,
            "name":             display_name,
            "chain_id":         chain_id,
            "explorer_url":     explorer_url,
            "rpc_url_env":      f"{prefix}_RPC_URL",
            "contract_env":     f"{prefix}_CONTRACT_ADDRESS",
            "eth_amount_env":   f"{prefix}_ETH_AMOUNT",
            "contract_address_default": contract_from_json,
            "eth_amount_default": eth_default,
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

NETWORK_CONFIGS: Dict[str, dict] = _discover_networks()

class Settings(BaseSettings):
    APP_NAME:    str = "Ethernal Faucet API"
    APP_VERSION: str = "3.0.0"
    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    DEBUG:       bool = Field(default=False,        env="DEBUG")

    # Wallet faucet
    FAUCET_ADDRESS:     str = Field(default="", env="FAUCET_ADDRESS")
    FAUCET_PRIVATE_KEY: str = Field(default="", env="FAUCET_PRIVATE_KEY")

    # Montos
    FAUCET_AMOUNT:             float = Field(default=10000.0, env="FAUCET_AMOUNT")
    FAUCET_MIN_BALANCE_ALERT:  float = Field(default=10000.0, env="FAUCET_MIN_BALANCE_ALERT")
    FAUCET_DEFAULT_ETH_AMOUNT: float = Field(default=0.01,   env="FAUCET_DEFAULT_ETH_AMOUNT")

    # Rate limiting
    RATE_LIMIT_IP_SECONDS:     int  = Field(default=3600,  env="RATE_LIMIT_IP_SECONDS")
    RATE_LIMIT_WALLET_SECONDS: int  = Field(default=86400, env="RATE_LIMIT_WALLET_SECONDS")
    RATE_LIMIT_ENABLED:        bool = Field(default=True,  env="RATE_LIMIT_ENABLED")

    # Base de datos
    DATABASE_URL:    Optional[str] = Field(default=None, env="DATABASE_URL")
    DB_POOL_SIZE:    int           = Field(default=10,   env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int           = Field(default=5,    env="DB_MAX_OVERFLOW")
    DB_ECHO:         bool          = Field(default=False, env="DB_ECHO")

    # Redis
    REDIS_URL:              Optional[str] = Field(default=None, env="REDIS_URL")
    REDIS_MAX_CONNECTIONS:  int           = Field(default=10,   env="REDIS_MAX_CONNECTIONS")
    REDIS_DECODE_RESPONSES: bool          = Field(default=True, env="REDIS_DECODE_RESPONSES")

    # Celery
    CELERY_BROKER_URL:     Optional[str] = Field(default=None, env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = Field(default=None, env="CELERY_RESULT_BACKEND")

    @field_validator("CELERY_BROKER_URL", mode="before")
    @classmethod
    def set_celery_broker(cls, v: Optional[str], info: Any) -> Optional[str]:
        return v or (info.data.get("REDIS_URL") if info.data else None)

    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    @classmethod
    def set_celery_backend(cls, v: Optional[str], info: Any) -> Optional[str]:
        return v or (info.data.get("REDIS_URL") if info.data else None)

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

    @field_validator("ENABLE_DB", mode="before")
    @classmethod
    def check_db_enabled(cls, v: bool, info: Any) -> bool:
        return bool(v) and bool(info.data.get("DATABASE_URL") if info.data else False)

    @field_validator("ENABLE_REDIS", mode="before")
    @classmethod
    def check_redis_enabled(cls, v: bool, info: Any) -> bool:
        return bool(v) and bool(info.data.get("REDIS_URL") if info.data else False)

    def get_network_config(self, network: str) -> dict:
        """
        Devuelve la config completa y resuelta de una red.

        Resolución de valores en orden de prioridad:
          1. Variable de entorno canónica  (ej: ETHEREUM_SEPOLIA_RPC_URL)
          2. Alias de env var              (ej: SEPOLIA_RPC_URL para ethereum-sepolia)
          3. Valor embebido en el JSON     (solo para contract_address)
          4. Default global de Settings    (solo para eth_amount)

        Lanza ValueError si falta RPC URL — sin ella no hay nada que hacer.
        El contrato se puede tomar del JSON, así que no es obligatorio en env.
        """
        cfg = NETWORK_CONFIGS.get(network)
        if not cfg:
            raise ValueError(
                f"Red '{network}' no encontrada. "
                f"Verificar deployments/ o redes disponibles: {sorted(NETWORK_CONFIGS)}"
            )

        # RPC URL — canónica con fallback a aliases
        rpc_url = _resolve_rpc_url(network, cfg["rpc_url_env"])
        if not rpc_url:
            aliases = _RPC_URL_ALIASES.get(network, [])
            alias_hint = f" (aliases probados: {aliases})" if aliases else ""
            raise ValueError(
                f"Falta variable de entorno {cfg['rpc_url_env']} "
                f"requerida para la red '{network}'{alias_hint}"
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

    def validate_startup(self) -> None:
        """
        Validación de configuración al arranque.
        Llama a get_network_config() por cada red descubierta y loguea
        cuáles quedan activas y cuáles fallan por env vars faltantes.

        No lanza excepción — las redes con config incompleta se deshabilitan
        silenciosamente (igual que hace FaucetService). El objetivo es producir
        logs claros en Render antes de que FaucetService intente conectar.
        """
        if not NETWORK_CONFIGS:
            logger.error(
                "STARTUP: No hay redes configuradas. "
                "Commitear deployments/<chain>/<network>/MockUSDC.json al repo."
            )
            return

        ok:   List[str] = []
        fail: List[str] = []

        for network_key in sorted(NETWORK_CONFIGS):
            try:
                cfg = self.get_network_config(network_key)
                ok.append(network_key)
                logger.info(
                    f"STARTUP ✓ {network_key} | "
                    f"chain_id={cfg['chain_id']} | "
                    f"contract={cfg['contract_address'][:10]}… | "
                    f"rpc={cfg['rpc_url'][:40]}…"
                )
            except ValueError as e:
                fail.append(network_key)
                logger.warning(f"STARTUP ✗ {network_key} deshabilitada — {e}")
        if ok:
            logger.info(f"STARTUP: Redes activas → {ok}")
        if fail:
            logger.warning(f"STARTUP: Redes deshabilitadas (env vars faltantes) → {fail}")
        if not ok:
            logger.error(
                "STARTUP: Ninguna red pudo inicializarse. "
                "El servicio arrancará pero rechazará todos los requests. "
                "Verificar variables de entorno en Render."
            )

    @property
    def supported_networks(self) -> List[str]:
        return sorted(NETWORK_CONFIGS.keys())

    class Config:
        env_file       = ".env"
        case_sensitive = True
        extra          = "ignore"

settings = Settings()