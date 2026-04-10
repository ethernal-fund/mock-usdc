import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from sqlalchemy import insert, select, update, func
from web3 import Web3

from .config import settings
from .database import init_db, create_schema_and_tables, close_db, get_db
from .faucet_service import FaucetService
from .models import FaucetRequest as DBFaucetRequest
from .rate_limiter import RateLimiter

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

_PRODUCTION_ORIGINS = [
    "https://www.ethernal.fund",
    "https://ethernal.fund",
]

_VERCEL_PATTERN = re.compile(
    r"^https://[\w\-]+-ethernalllc-funds-projects\.vercel\.app$"
)

def _build_allowed_origins() -> list[str]:
    origins = list(_PRODUCTION_ORIGINS)
    if settings.CORS_ORIGINS_STR:
        extra = [o.strip() for o in settings.CORS_ORIGINS_STR.split(",") if o.strip()]
        for o in extra:
            if o not in origins:
                origins.append(o)
    if settings.ENVIRONMENT != "production":
        origins += [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    return origins

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Iniciando {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Entorno: {settings.ENVIRONMENT} | Red: {settings.NETWORK_NAME}")
    logger.info(f"Orígenes CORS: {_build_allowed_origins()}")

    if settings.ENABLE_DB:
        init_db()
        await create_schema_and_tables()   # crea schema 'faucet' + tablas
        logger.info("Base de datos lista")
    else:
        logger.warning("DB deshabilitada — rate limiting solo en memoria")

    yield

    if settings.ENABLE_DB:
        await close_db()
    logger.info("Shutdown completo")

app = FastAPI(
    title=settings.APP_NAME,
    description="Faucet de USDC y ETH para práctica en testnet — Ethernal",
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

faucet_service = FaucetService()
rate_limiter   = RateLimiter()

class FaucetRequestModel(BaseModel):
    address:          str           = Field(..., description="Dirección Ethereum (0x...)")
    turnstile_token:  Optional[str] = Field(None, description="Token Cloudflare Turnstile")

    @validator("address")
    def validate_address(cls, v: str) -> str:
        if not Web3.is_address(v):
            raise ValueError("Dirección Ethereum inválida")
        return Web3.to_checksum_address(v)

class FaucetResponse(BaseModel):
    success:      bool
    message:      str
    tx_hash:      Optional[str]   = None
    eth_tx_hash:  Optional[str]   = None
    amount:       Optional[float] = None
    eth_amount:   Optional[float] = None
    balance:      Optional[float] = None
    wait_time:    Optional[int]   = None

async def verify_admin_key(request: Request) -> bool:
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail="Admin API no configurada")
    api_key = request.headers.get(settings.API_KEY_HEADER)
    if api_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="API key inválida")
    return True

@app.get("/", tags=["info"])
async def root():
    return {
        "name":        settings.APP_NAME,
        "version":     settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "network":     settings.NETWORK_NAME,
        "chain_id":    settings.CHAIN_ID,
        "contract":    settings.CONTRACT_ADDRESS,
        "features": {
            "database":  settings.ENABLE_DB,
            "redis":     settings.ENABLE_REDIS,
            "turnstile": settings.TURNSTILE_ENABLED,
        },
    }

@app.get("/health", tags=["info"])
async def health_check():
    try:
        is_connected    = faucet_service.w3.is_connected()
        faucet_usdc     = faucet_service.get_balance(settings.FAUCET_ADDRESS)
        faucet_eth      = faucet_service.get_eth_balance(settings.FAUCET_ADDRESS)
        status          = "healthy" if (is_connected and faucet_usdc > 0) else "degraded"

        return {
            "status":             status,
            "rpc_connected":      is_connected,
            "faucet_usdc_balance": faucet_usdc,
            "faucet_eth_balance":  faucet_eth,
            "redis_available":    rate_limiter.use_redis,
            "database_enabled":   settings.ENABLE_DB,
            "timestamp":          datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Health check fallido: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )

@app.post("/faucet", response_model=FaucetResponse, tags=["faucet"])
async def request_tokens(request: Request, faucet_req: FaucetRequestModel):
    client_ip = request.client.host
    address   = faucet_req.address
    if settings.RATE_LIMIT_ENABLED:
        ip_ok, ip_wait = rate_limiter.check_ip(client_ip)
        if not ip_ok:
            return FaucetResponse(
                success=False,
                message=f"Demasiadas solicitudes desde tu IP. Esperá {ip_wait} segundos.",
                wait_time=ip_wait,
            )

        wallet_ok, wallet_wait = rate_limiter.check_wallet(address)
        if not wallet_ok:
            return FaucetResponse(
                success=False,
                message=f"Esta wallet ya recibió tokens recientemente. Esperá {wallet_wait} segundos.",
                wait_time=wallet_wait,
            )

    if settings.TURNSTILE_ENABLED and not faucet_req.turnstile_token:
        raise HTTPException(status_code=400, detail="Token Turnstile requerido")

    faucet_usdc = faucet_service.get_balance(settings.FAUCET_ADDRESS)
    if faucet_usdc < settings.FAUCET_AMOUNT:
        logger.warning(f"Faucet con bajo balance: {faucet_usdc} USDC")
        raise HTTPException(
            status_code=503,
            detail="Faucet temporalmente sin fondos — contactar al equipo de Ethernal",
        )

    if settings.ENABLE_DB:
        async with get_db() as db:
            await db.execute(
                insert(DBFaucetRequest).values(
                    wallet_address=address,
                    ip_address=client_ip,
                    amount=settings.FAUCET_AMOUNT,
                    eth_amount=settings.FAUCET_ETH_AMOUNT,
                    status="processing",
                )
            )

    try:
        tx_hash = faucet_service.send_tokens(address, settings.FAUCET_AMOUNT)
        logger.info(f"USDC enviado → {address} | tx: {tx_hash} | IP: {client_ip}")
    except Exception as e:
        logger.error(f"Error enviando USDC a {address}: {e}")
        if settings.ENABLE_DB:
            await _mark_failed(address, str(e))
        raise HTTPException(status_code=500, detail=str(e))

    eth_tx_hash: Optional[str] = None
    eth_amount   = settings.FAUCET_ETH_AMOUNT
    try:
        eth_tx_hash = faucet_service.send_eth(address, eth_amount)
        logger.info(f"ETH enviado → {address} | tx: {eth_tx_hash}")
    except Exception as e:
        logger.warning(f"ETH send falló para {address} (no crítico): {e}")

    rate_limiter.record_request(client_ip, address)
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

    new_balance = faucet_service.get_balance(address)

    return FaucetResponse(
        success=True,
        message=f"¡Recibiste {settings.FAUCET_AMOUNT:,.0f} USDC y {eth_amount} ETH para practicar en Arbitrum Sepolia!",
        tx_hash=tx_hash,
        eth_tx_hash=eth_tx_hash,
        amount=settings.FAUCET_AMOUNT,
        eth_amount=eth_amount,
        balance=new_balance,
    )

@app.get("/balance/{address}", tags=["faucet"])
async def get_balance(address: str):
    if not Web3.is_address(address):
        raise HTTPException(status_code=400, detail="Dirección inválida")

    checksum = Web3.to_checksum_address(address)
    try:
        return {
            "address":     checksum,
            "usdc_balance": faucet_service.get_balance(checksum),
            "eth_balance":  faucet_service.get_eth_balance(checksum),
            "symbol":      "USDC",
            "decimals":    6,
        }
    except Exception as e:
        logger.error(f"Balance check fallido para {address}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stats", tags=["info"])
async def get_stats():
    try:
        stats = rate_limiter.get_stats()
        return {
            "faucet_usdc_balance":    faucet_service.get_balance(settings.FAUCET_ADDRESS),
            "faucet_eth_balance":     faucet_service.get_eth_balance(settings.FAUCET_ADDRESS),
            "total_requests":         stats["total_requests"],
            "unique_wallets":         stats["unique_wallets"],
            "unique_ips":             stats["unique_ips"],
            "amount_per_request":     settings.FAUCET_AMOUNT,
            "eth_amount_per_request": settings.FAUCET_ETH_AMOUNT,
            "using_redis":            stats["using_redis"],
            "rate_limits": {
                "per_ip_seconds":     settings.RATE_LIMIT_IP_SECONDS,
                "per_wallet_seconds": settings.RATE_LIMIT_WALLET_SECONDS,
            },
        }
    except Exception as e:
        logger.error(f"Stats fallidas: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/stats", dependencies=[Depends(verify_admin_key)], tags=["admin"])
async def admin_stats():
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")

    async with get_db() as db:
        total_result = await db.execute(select(func.count(DBFaucetRequest.id)))
        total        = total_result.scalar()

        by_status_result = await db.execute(
            select(DBFaucetRequest.status, func.count(DBFaucetRequest.id))
            .group_by(DBFaucetRequest.status)
        )
        by_status = dict(by_status_result.all())

    return {
        "total_requests":      total,
        "by_status":           by_status,
        "faucet_usdc_balance": faucet_service.get_balance(settings.FAUCET_ADDRESS),
        "faucet_eth_balance":  faucet_service.get_eth_balance(settings.FAUCET_ADDRESS),
    }


@app.get(
    "/admin/requests",
    dependencies=[Depends(verify_admin_key)],
    tags=["admin"],
)
async def admin_requests(limit: int = 50, offset: int = 0, status: Optional[str] = None):
    if not settings.ENABLE_DB:
        raise HTTPException(status_code=501, detail="DB no habilitada")

    async with get_db() as db:
        q = select(DBFaucetRequest).order_by(DBFaucetRequest.created_at.desc())
        if status:
            q = q.where(DBFaucetRequest.status == status)
        q = q.limit(limit).offset(offset)

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

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Excepción no manejada: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor"},
    )

async def _mark_failed(wallet_address: str, error: str) -> None:
    """Marca como fallida la solicitud en processing para un wallet dado."""
    try:
        async with get_db() as db:
            await db.execute(
                update(DBFaucetRequest)
                .where(DBFaucetRequest.wallet_address == wallet_address)
                .where(DBFaucetRequest.status == "processing")
                .values(status="failed", error_message=error)
            )
    except Exception as e:
        logger.error(f"No se pudo marcar como fallida la request de {wallet_address}: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )