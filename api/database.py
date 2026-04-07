from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from contextlib import asynccontextmanager
import logging
import ssl

from .config import settings
from .models import Base

logger = logging.getLogger(__name__)
engine = None
async_session_maker = None

def init_db():
    global engine, async_session_maker
    
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL not set, database features disabled")
        return

    db_url = settings.DATABASE_URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # SSL context forzado para Supabase + compatibilidad con Render free tier (IPv4)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    engine = create_async_engine(
        db_url,
        echo=settings.DB_ECHO,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        poolclass=NullPool if settings.ENVIRONMENT == "test" else None,
        connect_args={
            "ssl": ssl_context,
            "server_settings": {"client_encoding": "utf8"},
        }
    )
    
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    logger.info("Database connection initialized")

async def create_tables():
    if not engine:
        return
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("Database tables created")

async def drop_tables():
    if not engine:
        return
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    logger.info("Database tables dropped")


@asynccontextmanager
async def get_db():
    if not async_session_maker:
        raise RuntimeError("Database not initialized")
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def close_db():
    global engine
    
    if engine:
        await engine.dispose()
        logger.info("Database connection closed")