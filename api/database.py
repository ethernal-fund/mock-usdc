import ssl
import logging
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool
from .config import settings
from .models import Base

logger = logging.getLogger(__name__)

engine              = None
async_session_maker = None

def _build_engine(db_url: str):
    # Normalizar URL a asyncpg
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    return create_async_engine(
        db_url,
        echo=settings.DB_ECHO,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        # NullPool en tests para evitar conexiones colgadas
        poolclass=NullPool if settings.ENVIRONMENT == "test" else None,
        connect_args={
            "ssl": ssl_ctx,
            "server_settings": {"client_encoding": "utf8"},
        },
    )

def init_db() -> None:
    global engine, async_session_maker
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL no configurada — DB deshabilitada")
        return

    engine = _build_engine(settings.DATABASE_URL)
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    logger.info("Engine de base de datos inicializado")

async def create_schema_and_tables() -> None:
    if not engine:
        return
    async with engine.begin() as conn:
        # Crear schema propio — idempotente, seguro correr múltiples veces
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS faucet"))
        # Crear tablas solo dentro del schema 'faucet'
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema 'faucet' y tablas listas")

async def drop_tables() -> None:
    if not engine:
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    logger.warning("Tablas del schema 'faucet' eliminadas")

@asynccontextmanager
async def get_db():
    if not async_session_maker:
        raise RuntimeError("Base de datos no inicializada — llamar init_db() primero")

    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def close_db() -> None:
    global engine
    if engine:
        await engine.dispose()
        logger.info("Conexión de base de datos cerrada")
        