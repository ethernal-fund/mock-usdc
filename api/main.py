from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import insert, select, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from web3 import Web3

from .config import settings, NETWORK_CONFIGS
from .database import init_db, create_schema_and_tables, close_db, get_db
from .faucet_service import FaucetService
from .models import FaucetRequest as DBFaucetRequest, BlockedAddress, FaucetStats
from .rate_limiter import RateLimiter

# Sentry 
if settings.SENTRY_ENABLED and settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        environment=settings.ENVIRONMENT,
    )

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# CORS 
_PRODUCTION_ORIGINS = [
    "https://www.ethernal.fund",
    "https://ethernal.fund",
]

def _build_allowed_origins() -> list[str]:
    origins = list(_PRODUCTION_ORIGINS)
    if settings.CORS_ORIGINS_STR:
        for o in settings.CORS_ORIGINS_STR.split(","):
            o = o.strip()
            if o and o not in origins:
                origins.append(o)
    if settings.ENVIRONMENT != "production":
        origins += [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    return origins

# Servicios (instanciados una sola vez)
# FaucetService se crea dentro del lifespan para que un fallo de conexión no
# impida que FastAPI arranque y pueda responder con un error útil.
faucet_service: Optional[FaucetService] = None
rate_limiter   = RateLimiter()

# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    global faucet_service

    logger.info("Iniciando %s v%s", settings.APP_NAME, settings.APP_VERSION)
    settings.validate_startup()

    try:
        faucet_service = FaucetService()
        logger.info("Redes activas: %s", faucet_service.active_networks)
    except Exception as exc:
        logger.error("FaucetService falló al inicializar: %s", exc)
        # Arrancar de todas formas — los endpoints retornarán 503

    logger.info("Orígenes CORS: %s", _build_allowed_origins())

    if settings.ENABLE_DB:
        init_db()
        await create_schema_and_tables()
        logger.info("Base de datos lista")
    else:
        logger.warning("DB deshabilitada — rate limiting solo en memoria/Redis")

    yield

    if settings.ENABLE_DB:
        await close_db()
    logger.info("Shutdown completo")

# App 
app = FastAPI(
    title=settings.APP_NAME,
    description="Faucet multi-red de USDC y ETH para testnet — Ethernal",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_allowed_origins(),
    allow_origin_regex=(
        r"https://[\w\-]+-ethernalllc-funds-projects\.vercel\.app"
        r"|https://ethernal\.fund"
        r"|https://www\.ethernal\.fund"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_NETWORKS = list(NETWORK_CONFIGS.keys())

# Schemas 
class FaucetRequestModel(BaseModel):
    address:         str           = Field(..., description="Dirección Ethereum (0x...)")
    network:         str           = Field(..., description=f"Red destino: {SUPPORTED_NETWORKS}")
    turnstile_token: Optional[str] = Field(None, description="Token Cloudflare Turnstile")

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not Web3.is_address(v):
            raise ValueError("Dirección Ethereum inválida")
        return Web3.to_checksum_address(v)

    @field_validator("network")
    @classmethod
    def validate_network(cls, v: str) -> str:
        if v not in SUPPORTED_NETWORKS:
            raise ValueError(f"Red no soportada. Opciones: {SUPPORTED_NETWORKS}")
        return v

class FaucetResponse(BaseModel):
    success:     bool
    message:     str
    network:     Optional[str]   = None
    tx_hash:     Optional[str]   = None
    eth_tx_hash: Optional[str]   = None
    amount:      Optional[float] = None
    eth_amount:  Optional[float] = None
    balance:     Optional[float] = None
    wait_time:   Optional[int]   = None

class BlockAddressRequest(BaseModel):
    address_type:     str           = Field(..., description="'wallet' | 'ip'")
    address_value:    str           = Field(..., description="Dirección o IP a bloquear")
    reason:           str           = Field(..., description="Motivo del bloqueo")
    expires_in_hours: Optional[int] = Field(None, description="Duración en horas (null = permanente)")

    @field_validator("address_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("wallet", "ip"):
            raise ValueError("address_type debe ser 'wallet' o 'ip'")
        return v

class UnblockAddressRequest(BaseModel):
    address_value: str = Field(..., description="Dirección o IP a desbloquear")

# Helpers
async def verify_admin_key(request: Request) -> bool:
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail="Admin API no configurada")
    api_key = request.headers.get(settings.API_KEY_HEADER)
    if api_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="API key inválida")
    return True

def _get_client_ip(request: Request) -> str:
    """
    Extrae la IP real del usuario.
    - X-Real-IP: propagada por el backend cuando actúa de proxy.
    - X-Forwarded-For: seteada por Render u otros reverse proxies.
    - Fallback: request.client.host (conexión directa o dev local).
    """
    real_ip   = request.headers.get("X-Real-IP", "").strip()
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if real_ip:
        return real_ip
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _require_faucet() -> FaucetService:
    """Dependency que lanza 503 si FaucetService no pudo inicializarse."""
    if faucet_service is None:
        raise HTTPException(
            status_code=503,
            detail="Servicio no disponible — faucet no inicializado. Revisar logs.",
        )
    return faucet_service

async def _update_daily_stats(
    success:        bool,
    rate_limited:   bool          = False,
    usdc_amount:    float         = 0.0,
    eth_amount:     float         = 0.0,
    wallet_address: Optional[str] = None,
    ip_address:     Optional[str] = None,
) -> None:
    """
    Upsert atómico en faucet_stats para el día UTC actual.
    Incrementa contadores según el resultado de la request.
    Falla silenciosamente para no afectar el flujo principal.
    """
    if not settings.ENABLE_DB:
        return
    try:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with get_db() as db:
            # Crear fila del día si no existe
            await db.execute(
                pg_insert(FaucetStats)
                .values(
                    date=today,
                    total_requests=0,
                    successful_requests=0,
                    failed_requests=0,
                    rate_limited_requests=0,
                    total_usdc_distributed=0,
                    total_eth_distributed=0,
                    unique_wallets=0,
                    unique_ips=0,
                )
                .on_conflict_do_nothing(index_elements=["date"])
            )

            values: dict = {
                "total_requests": FaucetStats.total_requests + 1,
                "updated_at":     datetime.utcnow(),
            }
            if rate_limited:
                values["rate_limited_requests"] = FaucetStats.rate_limited_requests + 1
            elif success:
                values["successful_requests"]    = FaucetStats.successful_requests + 1
                values["total_usdc_distributed"] = FaucetStats.total_usdc_distributed + usdc_amount
                values["total_eth_distributed"]  = FaucetStats.total_eth_distributed + eth_amount
            else:
                values["failed_requests"] = FaucetStats.failed_requests + 1

            # Únicos del día (contados desde faucet_requests para exactitud)
            uw = await db.execute(
                select(func.count(func.distinct(DBFaucetRequest.wallet_address)))
                .where(DBFaucetRequest.created_at >= today)
            )
            ui = await db.execute(
                select(func.count(func.distinct(DBFaucetRequest.ip_address)))
                .where(DBFaucetRequest.created_at >= today)
            )
            values["unique_wallets"] = uw.scalar() or 0
            values["unique_ips"]     = ui.scalar() or 0

            await db.execute(
                update(FaucetStats).where(FaucetStats.date == today).values(**values)
            )
    except Exception as exc:
        logger.error("Error actualizando faucet_stats: %s", exc, exc_info=True)

async def _mark_failed(wallet_address: str, error: str) -> None:
    """Marca la request en curso como fallida en la DB."""
    try:
        async with get_db() as db:
            await db.execute(
                update(DBFaucetRequest)
                .where(DBFaucetRequest.wallet_address == wallet_address)
                .where(DBFaucetRequest.status == "processing")
                .values(status="failed", error_message=error)
            )
        await _update_daily_stats(success=False)
    except Exception as exc:
        logger.error("No se pudo marcar como fallida la request de %s: %s", wallet_address, exc)


async def _is_blocked(db, address: str, ip: str) -> bool:
    """Verifica si la wallet o IP están en la lista de bloqueos activos y vigentes."""
    now    = datetime.utcnow()
    result = await db.execute(
        select(BlockedAddress).where(
            BlockedAddress.is_active == True,
            BlockedAddress.address_value.in_([address.lower(), ip]),
            (BlockedAddress.expires_at == None) | (BlockedAddress.expires_at > now),
        )
    )
    return result.scalars().first() is not None


# Endpoints públicos 

@app.get("/", tags=["info"])
async def root():
    active = faucet_service.active_networks if faucet_service else []
    return {
        "name":               settings.APP_NAME,
        "version":            settings.APP_VERSION,
        "environment":        settings.ENVIRONMENT,
        "supported_networks": SUPPORTED_NETWORKS,
        "active_networks":    active,
        "features": {
            "database":  settings.ENABLE_DB,
            "redis":     settings.ENABLE_REDIS,
            "turnstile": settings.TURNSTILE_ENABLED,
        },
    }

@app.get("/health", tags=["info"])
async def health_check():
    try:
        if faucet_service is None:
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "error": "FaucetService no inicializado"},
            )
        networks_status = {}
        overall_ok = True
        for net_key in faucet_service.active_networks:
            client       = faucet_service.get_client(net_key)
            connected    = client.w3.is_connected()
            usdc_balance = client.get_usdc_balance(settings.FAUCET_ADDRESS)
            eth_balance  = client.get_eth_balance(settings.FAUCET_ADDRESS)
            net_ok       = connected and usdc_balance > 0
            networks_status[net_key] = {
                "name":          client.name,
                "rpc_connected": connected,
                "usdc_balance":  usdc_balance,
                "eth_balance":   eth_balance,
                "healthy":       net_ok,
            }
            if not net_ok:
                overall_ok = False
        return {
            "status":           "healthy" if overall_ok else "degraded",
            "networks":         networks_status,
            "redis_available":  rate_limiter.use_redis,
            "database_enabled": settings.ENABLE_DB,
            "timestamp":        datetime.utcnow().isoformat(),
        }
    except Exception as exc:
        logger.error("Health check fallido: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(exc)},
        )

@app.get("/networks", tags=["info"])
async def get_networks():
    """Devuelve las redes disponibles y su estado actual."""
    active = faucet_service.active_networks if faucet_service else []
    result = {}
    for net_key in active:
        cfg = NETWORK_CONFIGS[net_key]
        result[net_key] = {
            "name":         cfg["name"],
            "chain_id":     cfg["chain_id"],
            "explorer_url": cfg["explorer_url"],
        }
    return {"networks": result}

@app.get("/balance/{network}/{address}", tags=["faucet"])
async def get_balance(
    network: str,
    address: str,
    svc: FaucetService = Depends(_require_faucet),
):
    if network not in SUPPORTED_NETWORKS:
        raise HTTPException(status_code=400, detail=f"Red inválida. Opciones: {SUPPORTED_NETWORKS}")
    if not Web3.is_address(address):
        raise HTTPException(status_code=400, detail="Dirección inválida")
    checksum = Web3.to_checksum_address(address)
    try:
        return {
            "address":      checksum,
            "network":      network,
            "usdc_balance": svc.get_balance(checksum, network),
            "eth_balance":  svc.get_eth_balance(checksum, network),
            "symbol":       "USDC",
            "decimals":     6,
        }
    except Exception as exc:
        logger.error("Balance check fallido para %s en %s: %s", address, network, exc)
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/stats", tags=["info"])
async def get_stats():
    try:
        stats        = rate_limiter.get_stats()
        networks_bal = {}
        if faucet_service:
            for net_key in faucet_service.active_networks:
                client = faucet_service.get_client(net_key)
                networks_bal[net_key] = {
                    "usdc_balance":    client.get_usdc_balance(settings.FAUCET_ADDRESS),
                    "eth_balance":     client.get_eth_balance(settings.FAUCET_ADDRESS),
                    "eth_per_request": client.eth_amount,
                }
        return {
            "faucet_wallet":    settings.FAUCET_ADDRESS,
            "networks":         networks_bal,
            "total_requests":   stats["total_requests"],
            "unique_wallets":   stats["unique_wallets"],
            "unique_ips":       stats["unique_ips"],
            "usdc_per_request": settings.FAUCET_AMOUNT,
            "using_redis":      stats["using_redis"],
            "rate_limits": {
                "per_ip_seconds":     settings.RATE_LIMIT_IP_SECONDS,
                "per_wallet_seconds": settings.RATE_LIMIT_WALLET_SECONDS,
            },
        }
    except Exception as exc:
        logger.error("Stats fallidas: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

# Endpoint principal 

@app.post("/faucet", response_model=FaucetResponse, tags=["faucet"])
async def request_tokens(
    request:    Request,
    faucet_req: FaucetRequestModel,
    svc:        FaucetService = Depends(_require_faucet),
):
    client_ip = _get_client_ip(request)
    address   = faucet_req.address
    network   = faucet_req.network

    try:
        client = svc.get_client(network)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 1. Bloqueos manuales (DB)
    if settings.ENABLE_DB:
        async with get_db() as db:
            if await _is_blocked(db, address, client_ip):
                logger.warning("Request bloqueada | wallet=%s ip=%s", address[:10], client_ip)
                raise HTTPException(status_code=403, detail="Dirección o IP bloqueada.")

    # 2. Rate limiting atómico 
    # Las claves incluyen la red para que cada chain tenga su propio cupo.
    # check_and_reserve_* hace SET NX EX en una sola operación atómica:
    # si la clave ya existe el request queda bloqueado sin crear ventanas de
    # race condition entre check y record (problema de la versión anterior).
    if settings.RATE_LIMIT_ENABLED:
        ip_key     = f"{client_ip}:{network}"
        wallet_key = f"{address.lower()}:{network}"

        ip_ok, ip_wait = rate_limiter.check_and_reserve_ip(ip_key)
        if not ip_ok:
            await _update_daily_stats(
                success=False, rate_limited=True, ip_address=client_ip,
            )
            return FaucetResponse(
                success=False,
                network=network,
                message=(
                    f"Demasiadas solicitudes desde tu IP en {client.name}. "
                    f"Esperá {ip_wait} segundos."
                ),
                wait_time=ip_wait,
            )

        wallet_ok, wallet_wait = rate_limiter.check_and_reserve_wallet(wallet_key)
        if not wallet_ok:
            # La IP ya fue reservada arriba; si la wallet falla la liberamos
            # para no consumir el cupo de IP sin enviar tokens.
            rate_limiter.release_ip(ip_key)
            await _update_daily_stats(
                success=False, rate_limited=True,
                wallet_address=address, ip_address=client_ip,
            )
            return FaucetResponse(
                success=False,
                network=network,
                message=(
                    f"Esta wallet ya recibió tokens en {client.name}. "
                    f"Esperá {wallet_wait} segundos."
                ),
                wait_time=wallet_wait,
            )

    # 3. Turnstile 
    if settings.TURNSTILE_ENABLED and not faucet_req.turnstile_token:
        raise HTTPException(status_code=400, detail="Token Turnstile requerido")

    # 4. Verificar balance del faucet 
    faucet_usdc = client.get_usdc_balance(settings.FAUCET_ADDRESS)
    if faucet_usdc < settings.FAUCET_AMOUNT:
        logger.warning("[%s] Balance bajo: %s USDC", client.name, faucet_usdc)
        raise HTTPException(
            status_code=503,
            detail=f"Faucet de {client.name} sin fondos — contactar al equipo de Ethernal",
        )

    # 5. Registrar request como "processing" 
    if settings.ENABLE_DB:
        async with get_db() as db:
            await db.execute(
                insert(DBFaucetRequest).values(
                    wallet_address=address,
                    ip_address=client_ip,
                    amount=settings.FAUCET_AMOUNT,
                    eth_amount=client.eth_amount,
                    status="processing",
                )
            )

    # 6. Enviar USDC
    try:
        tx_hash = svc.send_tokens(address, settings.FAUCET_AMOUNT, network)
        logger.info("[%s] USDC → %s | tx: %s | ip: %s", client.name, address[:10], tx_hash, client_ip)
    except Exception as exc:
        logger.error("[%s] Error USDC → %s: %s", client.name, address[:10], exc)
        if settings.ENABLE_DB:
            await _mark_failed(address, str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    # 7. Enviar ETH (best-effort)
    eth_tx_hash: Optional[str] = None
    eth_amount = client.eth_amount
    try:
        eth_tx_hash = svc.send_eth(address, eth_amount, network)
        logger.info("[%s] ETH → %s | tx: %s", client.name, address[:10], eth_tx_hash)
    except Exception as exc:
        logger.error("[%s] ETH send FALLÓ para %s: %s", client.name, address[:10], exc)
        # No se relanza — el USDC ya fue enviado, el ETH es secundario

    # 8. Actualizar contadores 
    if settings.RATE_LIMIT_ENABLED:
        rate_limiter.record_stats(client_ip, address)

    if settings.ENABLE_DB:
        async with get_db() as db:
            await db.execute(
                update(DBFaucetRequest)
                .where(DBFaucetRequest.wallet_address == address)
                .where(DBFaucetRequest.status == "processing")
                .values(
                    status="completed",
                    tx_hash=tx_hash,
                    eth_tx_hash=eth_tx_hash,
                    completed_at=datetime.utcnow(),
                )
            )
        await _update_daily_stats(
            success=True,
            usdc_amount=settings.FAUCET_AMOUNT,
            eth_amount=eth_amount if eth_tx_hash else 0.0,
            wallet_address=address,
            ip_address=client_ip,
        )

    # 9. Respuesta 
    new_balance = svc.get_balance(address, network)
    return FaucetResponse(
        success=True,
        network=network,
        message=(
            f"¡Recibiste {settings.FAUCET_AMOUNT:,.0f} USDC "
            f"y {eth_amount} ETH en {client.name}!"
        ),
        tx_hash=tx_hash,
        eth_tx_hash=eth_tx_hash,
        amount=settings.FAUCET_AMOUNT,
        eth_amount=eth_amount,
        balance=new_balance,
    )

@app.get("/admin/stats", dependencies=[Depends(verify_admin_key)], tags=["admin"])
async def admin_stats():
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")
    async with get_db() as db:
        total_result  = await db.execute(select(func.count(DBFaucetRequest.id)))
        total         = total_result.scalar()
        by_status_res = await db.execute(
            select(DBFaucetRequest.status, func.count(DBFaucetRequest.id))
            .group_by(DBFaucetRequest.status)
        )
        by_status = dict(by_status_res.all())

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_res   = await db.execute(select(FaucetStats).where(FaucetStats.date == today))
        today_stats = today_res.scalars().first()

    networks_bal = {}
    if faucet_service:
        for net_key in faucet_service.active_networks:
            client = faucet_service.get_client(net_key)
            networks_bal[net_key] = {
                "usdc_balance": client.get_usdc_balance(settings.FAUCET_ADDRESS),
                "eth_balance":  client.get_eth_balance(settings.FAUCET_ADDRESS),
            }

    return {
        "total_requests": total,
        "by_status":      by_status,
        "networks":       networks_bal,
        "today": {
            "total_requests":         today_stats.total_requests         if today_stats else 0,
            "successful_requests":    today_stats.successful_requests    if today_stats else 0,
            "failed_requests":        today_stats.failed_requests        if today_stats else 0,
            "rate_limited_requests":  today_stats.rate_limited_requests  if today_stats else 0,
            "total_usdc_distributed": float(today_stats.total_usdc_distributed) if today_stats else 0.0,
            "total_eth_distributed":  float(today_stats.total_eth_distributed)  if today_stats else 0.0,
            "unique_wallets":         today_stats.unique_wallets         if today_stats else 0,
            "unique_ips":             today_stats.unique_ips             if today_stats else 0,
        } if today_stats else None,
    }

@app.get("/admin/daily-stats", dependencies=[Depends(verify_admin_key)], tags=["admin"])
async def admin_daily_stats(days: int = 30):
    """Retorna las estadísticas diarias de los últimos N días."""
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="'days' debe estar entre 1 y 365")
    cutoff = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    async with get_db() as db:
        result = await db.execute(
            select(FaucetStats)
            .where(FaucetStats.date >= cutoff)
            .order_by(FaucetStats.date.desc())
        )
        rows = result.scalars().all()
    return {
        "days": days,
        "stats": [
            {
                "date":                   r.date.date().isoformat(),
                "total_requests":         r.total_requests,
                "successful_requests":    r.successful_requests,
                "failed_requests":        r.failed_requests,
                "rate_limited_requests":  r.rate_limited_requests,
                "total_usdc_distributed": float(r.total_usdc_distributed),
                "total_eth_distributed":  float(r.total_eth_distributed),
                "unique_wallets":         r.unique_wallets,
                "unique_ips":             r.unique_ips,
            }
            for r in rows
        ],
    }

@app.get("/admin/requests", dependencies=[Depends(verify_admin_key)], tags=["admin"])
async def admin_requests(limit: int = 50, offset: int = 0, status: Optional[str] = None):
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="'limit' debe estar entre 1 y 500")
    async with get_db() as db:
        q = select(DBFaucetRequest).order_by(DBFaucetRequest.created_at.desc())
        if status:
            q = q.where(DBFaucetRequest.status == status)
        q      = q.limit(limit).offset(offset)
        result = await db.execute(q)
        rows   = result.scalars().all()
    return {
        "requests": [
            {
                "id":             r.id,
                "wallet_address": r.wallet_address,
                "ip_address":     r.ip_address,
                "amount":         float(r.amount),
                "eth_amount":     float(r.eth_amount) if r.eth_amount else None,
                "tx_hash":        r.tx_hash,
                "eth_tx_hash":    r.eth_tx_hash,
                "status":         r.status,
                "error_message":  r.error_message,
                "created_at":     r.created_at.isoformat() if r.created_at else None,
                "completed_at":   r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in rows
        ],
        "limit":  limit,
        "offset": offset,
    }

@app.post("/admin/block", dependencies=[Depends(verify_admin_key)], tags=["admin"])
async def block_address(body: BlockAddressRequest):
    """Bloquea una wallet o IP. expires_in_hours=null es bloqueo permanente."""
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")
    expires_at    = None
    if body.expires_in_hours:
        expires_at = datetime.utcnow() + timedelta(hours=body.expires_in_hours)
    address_value = body.address_value.lower()
    try:
        async with get_db() as db:
            existing = await db.execute(
                select(BlockedAddress).where(BlockedAddress.address_value == address_value)
            )
            if existing.scalars().first():
                await db.execute(
                    update(BlockedAddress)
                    .where(BlockedAddress.address_value == address_value)
                    .values(is_active=True, reason=body.reason, expires_at=expires_at)
                )
                action = "reactivated"
            else:
                await db.execute(
                    insert(BlockedAddress).values(
                        address_type=body.address_type,
                        address_value=address_value,
                        reason=body.reason,
                        expires_at=expires_at,
                        is_active=True,
                    )
                )
                action = "created"
    except Exception as exc:
        logger.error("Error bloqueando %s: %s", address_value, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    logger.warning(
        "[ADMIN] Bloqueo %s | tipo: %s | valor: %s | motivo: %s",
        action, body.address_type, address_value, body.reason,
    )
    return {
        "action":        action,
        "address_type":  body.address_type,
        "address_value": address_value,
        "reason":        body.reason,
        "expires_at":    expires_at.isoformat() if expires_at else None,
        "permanent":     expires_at is None,
    }

@app.post("/admin/unblock", dependencies=[Depends(verify_admin_key)], tags=["admin"])
async def unblock_address(body: UnblockAddressRequest):
    """Desactiva el bloqueo de una wallet o IP."""
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")
    address_value = body.address_value.lower()
    async with get_db() as db:
        result = await db.execute(
            update(BlockedAddress)
            .where(BlockedAddress.address_value == address_value)
            .where(BlockedAddress.is_active == True)
            .values(is_active=False)
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró bloqueo activo para '{address_value}'",
            )
    logger.info("[ADMIN] Desbloqueado: %s", address_value)
    return {"unblocked": address_value}

@app.get("/admin/blocked", dependencies=[Depends(verify_admin_key)], tags=["admin"])
async def list_blocked(active_only: bool = True):
    """Lista todas las entradas de blocked_addresses."""
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")
    async with get_db() as db:
        q = select(BlockedAddress).order_by(BlockedAddress.created_at.desc())
        if active_only:
            q = q.where(BlockedAddress.is_active == True)
        result = await db.execute(q)
        rows   = result.scalars().all()
    return {
        "count": len(rows),
        "blocked": [
            {
                "id":            r.id,
                "address_type":  r.address_type,
                "address_value": r.address_value,
                "reason":        r.reason,
                "is_active":     r.is_active,
                "created_at":    r.created_at.isoformat() if r.created_at else None,
                "expires_at":    r.expires_at.isoformat() if r.expires_at else None,
                "permanent":     r.expires_at is None,
            }
            for r in rows
        ],
    }

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Excepción no manejada en %s: %s", request.url, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )