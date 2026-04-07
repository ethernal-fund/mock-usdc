from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, validator
from web3 import Web3
import logging
import re

from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from .config import settings
from .database import init_db, create_tables, close_db, get_db
from .models import FaucetRequest as DBFaucetRequest
from .rate_limiter import RateLimiter
from .faucet_service import FaucetService

if settings.SENTRY_ENABLED and settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        environment=settings.ENVIRONMENT,
    )

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PRODUCTION_ORIGINS = [
    "https://www.ethernal.fund",
    "https://ethernal.fund",
]

VERCEL_PREVIEW_PATTERN = re.compile(
    r"^https://[\w\-]+-ethernalllc-funds-projects\.vercel\.app$"
)

def build_allowed_origins() -> list[str]:
    origins = list(PRODUCTION_ORIGINS)
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

def is_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    allowed = build_allowed_origins()
    if origin in allowed:
        return True
    if VERCEL_PREVIEW_PATTERN.match(origin):
        return True
    return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Allowed CORS origins: {build_allowed_origins()}")

    if settings.ENABLE_DB:
        init_db()
        await create_tables()
        logger.info("Database initialized")
    else:
        logger.warning("Database disabled - using in-memory rate limiting only")

    yield

    if settings.ENABLE_DB:
        await close_db()
    logger.info("Application shutdown complete")

app = FastAPI(
    title=settings.APP_NAME,
    description="Production-ready USDC testnet token distribution",
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=PRODUCTION_ORIGINS + [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
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
rate_limiter = RateLimiter()

class FaucetRequestModel(BaseModel):
    address: str = Field(..., description="Ethereum address")
    turnstile_token: Optional[str] = Field(None, description="Cloudflare Turnstile token")

    @validator('address')
    def validate_address(cls, v):
        if not Web3.is_address(v):
            raise ValueError('Invalid Ethereum address')
        return Web3.to_checksum_address(v)

class FaucetResponse(BaseModel):
    success: bool
    message: str
    tx_hash: Optional[str] = None
    eth_tx_hash: Optional[str] = None
    amount: Optional[float] = None
    eth_amount: Optional[float] = None
    balance: Optional[float] = None
    wait_time: Optional[int] = None

async def verify_admin_key(request: Request):
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail="Admin API not configured")
    api_key = request.headers.get(settings.API_KEY_HEADER)
    if api_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True

@app.get("/")
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "network": settings.NETWORK_NAME,
        "chain_id": settings.CHAIN_ID,
        "contract": settings.CONTRACT_ADDRESS,
        "features": {
            "database": settings.ENABLE_DB,
            "redis": settings.ENABLE_REDIS,
            "turnstile": settings.TURNSTILE_ENABLED,
        },
        "endpoints": {
            "faucet": "POST /faucet",
            "balance": "GET /balance/{address}",
            "health": "GET /health",
            "stats": "GET /stats",
            "admin": "GET /admin/*"
        }
    }

@app.get("/health")
async def health_check():
    try:
        is_connected = faucet_service.w3.is_connected()
        faucet_balance = faucet_service.get_balance(settings.FAUCET_ADDRESS)
        faucet_eth_balance = faucet_service.get_eth_balance(settings.FAUCET_ADDRESS)
        redis_ok = rate_limiter.use_redis
        status = "healthy" if (is_connected and faucet_balance > 0) else "degraded"

        return {
            "status": status,
            "rpc_connected": is_connected,
            "faucet_usdc_balance": faucet_balance,
            "faucet_eth_balance": faucet_eth_balance,
            "redis_available": redis_ok,
            "database_enabled": settings.ENABLE_DB,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )

@app.post("/faucet", response_model=FaucetResponse)
async def request_tokens(request: Request, faucet_req: FaucetRequestModel):
    client_ip = request.client.host
    address = faucet_req.address

    try:
        if settings.RATE_LIMIT_ENABLED:
            ip_allowed, ip_wait = rate_limiter.check_ip(client_ip)
            if not ip_allowed:
                return FaucetResponse(
                    success=False,
                    message=f"Rate limit exceeded. Try again in {ip_wait} seconds.",
                    wait_time=ip_wait
                )

            wallet_allowed, wallet_wait = rate_limiter.check_wallet(address)
            if not wallet_allowed:
                return FaucetResponse(
                    success=False,
                    message=f"Wallet already received tokens recently. Try again in {wallet_wait} seconds.",
                    wait_time=wallet_wait
                )

        if settings.TURNSTILE_ENABLED and faucet_req.turnstile_token:
            pass

        faucet_balance = faucet_service.get_balance(settings.FAUCET_ADDRESS)
        if faucet_balance < settings.FAUCET_AMOUNT:
            logger.warning(f"Faucet running low: {faucet_balance} USDC")
            raise HTTPException(
                status_code=503,
                detail="Faucet temporarily unavailable - insufficient USDC balance"
            )

        if settings.ENABLE_DB:
            async with get_db() as db:
                from sqlalchemy import insert
                stmt = insert(DBFaucetRequest).values(
                    wallet_address=address,
                    ip_address=client_ip,
                    amount=settings.FAUCET_AMOUNT,
                    status="processing",
                )
                await db.execute(stmt)
                await db.commit()

        # Enviar USDC
        tx_hash = faucet_service.send_tokens(address, settings.FAUCET_AMOUNT)
        logger.info(f"Sent {settings.FAUCET_AMOUNT} USDC to {address} (IP: {client_ip}, Tx: {tx_hash})")

        # Enviar ETH
        eth_tx_hash = None
        eth_amount = settings.FAUCET_ETH_AMOUNT
        try:
            eth_tx_hash = faucet_service.send_eth(address, eth_amount)
            logger.info(f"Sent {eth_amount} ETH to {address} (Tx: {eth_tx_hash})")
        except Exception as e:
            logger.warning(f"ETH send failed for {address}: {e}")
            eth_tx_hash = None

        rate_limiter.record_request(client_ip, address)

        if settings.ENABLE_DB:
            async with get_db() as db:
                from sqlalchemy import update
                stmt = (
                    update(DBFaucetRequest)
                    .where(DBFaucetRequest.wallet_address == address)
                    .where(DBFaucetRequest.status == "processing")
                    .values(
                        status="completed",
                        tx_hash=tx_hash,
                        completed_at=datetime.utcnow(),
                    )
                )
                await db.execute(stmt)
                await db.commit()

        new_balance = faucet_service.get_balance(address)

        return FaucetResponse(
            success=True,
            message=f"Successfully sent {settings.FAUCET_AMOUNT} USDC and {eth_amount} ETH",
            tx_hash=tx_hash,
            eth_tx_hash=eth_tx_hash,
            amount=settings.FAUCET_AMOUNT,
            eth_amount=eth_amount,
            balance=new_balance,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Faucet request failed for {address}: {e}")
        if settings.ENABLE_DB:
            try:
                async with get_db() as db:
                    from sqlalchemy import update
                    stmt = (
                        update(DBFaucetRequest)
                        .where(DBFaucetRequest.wallet_address == address)
                        .where(DBFaucetRequest.status == "processing")
                        .values(status="failed", error_message=str(e))
                    )
                    await db.execute(stmt)
                    await db.commit()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/balance/{address}")
async def get_balance(address: str):
    try:
        if not Web3.is_address(address):
            raise HTTPException(status_code=400, detail="Invalid address")
        checksum_address = Web3.to_checksum_address(address)
        balance = faucet_service.get_balance(checksum_address)
        eth_balance = faucet_service.get_eth_balance(checksum_address)
        return {
            "address": checksum_address,
            "balance": balance,
            "symbol": "USDC",
            "decimals": 6,
            "eth_balance": eth_balance,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Balance check failed for {address}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats():
    try:
        stats = rate_limiter.get_stats()
        faucet_balance = faucet_service.get_balance(settings.FAUCET_ADDRESS)
        faucet_eth_balance = faucet_service.get_eth_balance(settings.FAUCET_ADDRESS)
        return {
            "faucet_usdc_balance": faucet_balance,
            "faucet_eth_balance": faucet_eth_balance,
            "total_requests": stats["total_requests"],
            "unique_wallets": stats["unique_wallets"],
            "unique_ips": stats["unique_ips"],
            "amount_per_request": settings.FAUCET_AMOUNT,
            "eth_amount_per_request": settings.FAUCET_ETH_AMOUNT,
            "using_redis": stats["using_redis"],
            "rate_limits": {
                "per_ip_seconds": settings.RATE_LIMIT_IP_SECONDS,
                "per_wallet_seconds": settings.RATE_LIMIT_WALLET_SECONDS,
            },
        }
    except Exception as e:
        logger.error(f"Stats retrieval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/stats", dependencies=[Depends(verify_admin_key)])
async def admin_stats():
    try:
        if not settings.ENABLE_DB:
            raise HTTPException(status_code=501, detail="Database not enabled")
        async with get_db() as db:
            from sqlalchemy import select, func
            stmt = select(func.count(DBFaucetRequest.id))
            result = await db.execute(stmt)
            total = result.scalar()

            stmt = select(
                DBFaucetRequest.status,
                func.count(DBFaucetRequest.id),
            ).group_by(DBFaucetRequest.status)
            result = await db.execute(stmt)
            by_status = dict(result.all())

            return {
                "total_requests": total,
                "by_status": by_status,
                "faucet_usdc_balance": faucet_service.get_balance(settings.FAUCET_ADDRESS),
                "faucet_eth_balance": faucet_service.get_eth_balance(settings.FAUCET_ADDRESS),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin stats failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
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