"""
rate_limiter.py — Faucet API

Implementa rate limiting por wallet y por IP usando SET NX atómico en Redis.

Con SET NX:
  - Si la clave NO existe → se crea con TTL = cooldown → request permitido
  - Si la clave SÍ existe → no se modifica nada    → request bloqueado
  - El TTL devuelto por PTTL indica cuánto tiempo falta

Fallback a memoria:
  Si Redis no está disponible, se usa un dict in-process con threading.Lock.
  LIMITACIÓN CONOCIDA: el fallback no es seguro entre múltiples workers/procesos
  (cada worker tiene su propio estado). Esto es aceptable solo en desarrollo.
  En producción, Redis debe estar siempre disponible (ENABLE_REDIS=True).
"""
from __future__ import annotations

import threading
import time
import logging
from typing import Optional, Tuple

import redis

from .config import settings

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self) -> None:
        self.redis_client: Optional[redis.Redis] = None
        self.use_redis = bool(settings.ENABLE_REDIS and settings.REDIS_URL)

        self._lock           = threading.Lock()
        self._ip_expiry:     dict[str, float] = {}   # ip     → timestamp de expiración
        self._wallet_expiry: dict[str, float] = {}   # wallet → timestamp de expiración

        self._total_requests = 0
        self._unique_ips:     set[str] = set()
        self._unique_wallets: set[str] = set()

        if self.use_redis:
            try:
                self.redis_client = redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=settings.REDIS_DECODE_RESPONSES,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                )
                self.redis_client.ping()
                logger.info("Redis rate limiter inicializado (modo atómico SET NX)")
            except Exception as exc:
                logger.warning(
                    f"Redis no disponible, usando fallback en memoria: {exc}. "
                    "ADVERTENCIA: el fallback no es seguro entre múltiples workers."
                )
                self.use_redis = False
                self.redis_client = None

    def check_and_reserve_wallet(self, wallet: str) -> Tuple[bool, int]:
        """
        Verifica si la wallet puede hacer un request Y lo reserva atómicamente.

        Retorna (allowed, wait_seconds):
          - (True,  0)         → request permitido, cupo reservado
          - (False, N)         → bloqueado, N segundos para que expire el cooldown

        Usar este método en lugar de check_wallet() + record_request() por separado.
        """
        wallet_lower = wallet.lower()
        cooldown     = settings.RATE_LIMIT_WALLET_SECONDS
        key          = f"rl:wallet:{wallet_lower}"
        return self._set_nx(key, cooldown)

    def check_and_reserve_ip(self, ip: str) -> Tuple[bool, int]:
        """
        Verifica si la IP puede hacer un request Y lo reserva atómicamente.
        Mismo contrato que check_and_reserve_wallet().
        """
        cooldown = settings.RATE_LIMIT_IP_SECONDS
        key      = f"rl:ip:{ip}"
        return self._set_nx(key, cooldown)

    def record_stats(self, ip: str, wallet: str) -> None:
        """
        Actualiza contadores en memoria (total, unique IPs y wallets).
        Llamar después de confirmar que el request fue permitido.
        """
        wallet_lower = wallet.lower()
        with self._lock:
            self._total_requests += 1
            self._unique_ips.add(ip)
            self._unique_wallets.add(wallet_lower)
        logger.info(
            "Request registrado | wallet=%.10s ip=%s total=%d",
            wallet_lower, ip, self._total_requests,
        )

    def release_ip(self, key: str) -> None:
        """
        Libera la reserva de IP cuando la wallet falla el check posterior.
        Esto evita que un usuario consuma su cupo de IP sin haber recibido tokens.

        Solo aplica cuando Redis está disponible — en memoria el TTL es tan
        corto que no vale la complejidad de revertirlo.
        """
        if self.use_redis and self.redis_client:
            try:
                self.redis_client.delete(f"rl:ip:{key}")
                logger.debug("IP reserva liberada | key=%s", key)
            except Exception as exc:
                logger.warning("No se pudo liberar IP key=%s: %s", key, exc)

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_requests":  self._total_requests,
                "unique_ips":      len(self._unique_ips),
                "unique_wallets":  len(self._unique_wallets),
                "using_redis":     self.use_redis,
            }

    def _set_nx(self, key: str, cooldown: int) -> Tuple[bool, int]:
        """
        Intenta SET NX EX <cooldown> en Redis.
          - Si la clave no existía → la crea → retorna (True, 0)
          - Si ya existía          → no toca nada → retorna (False, ttl_restante)

        En caso de error de Redis, hace fallback al dict in-memory.
        """
        if self.use_redis and self.redis_client:
            try:
                return self._redis_set_nx(key, cooldown)
            except Exception as exc:
                logger.error("Redis SET NX falló, usando fallback: %s", exc)
                return self._memory_set_nx(key, cooldown)
        return self._memory_set_nx(key, cooldown)

    def _redis_set_nx(self, key: str, cooldown: int) -> Tuple[bool, int]:
        """
        SET key "1" EX cooldown NX
          → True  si se seteó (clave nueva)   → permitido
          → None  si ya existía               → bloqueado; consultar TTL

        PTTL devuelve milisegundos restantes:
          ≥ 0  → clave existe con TTL
          -1   → sin TTL (no debería ocurrir, pero se trata como bloqueado)
          -2   → clave expiró entre SET y PTTL (tratamos como permitido)
        """
        acquired = self.redis_client.set(key, "1", ex=cooldown, nx=True)
        if acquired:
            return True, 0

        pttl = self.redis_client.pttl(key)
        if pttl == -2:
            # Expiró en el microsegundo entre SET NX y PTTL — reintentamos una vez
            acquired = self.redis_client.set(key, "1", ex=cooldown, nx=True)
            if acquired:
                return True, 0
            pttl = self.redis_client.pttl(key)

        wait = max(1, int(pttl / 1000)) if pttl > 0 else cooldown
        logger.info("Rate limited (Redis) | key=%s wait=%ds", key, wait)
        return False, wait

    def _memory_set_nx(self, key: str, cooldown: int) -> Tuple[bool, int]:
        """
        Equivalente in-memory de SET NX EX.
        Usa threading.Lock para ser seguro dentro del mismo proceso,
        pero NO es seguro entre procesos (múltiples workers).
        """
        now = time.monotonic()
        with self._lock:
            expiry = self._ip_expiry.get(key) or self._wallet_expiry.get(key)

            if expiry is None or now >= expiry:
                # Clave no existe o ya expiró → permitir y registrar
                new_expiry = now + cooldown
                if key.startswith("rl:ip:"):
                    self._ip_expiry[key] = new_expiry
                else:
                    self._wallet_expiry[key] = new_expiry
                return True, 0

            wait = max(1, int(expiry - now))
            logger.info("Rate limited (memory) | key=%s wait=%ds", key, wait)
            return False, wait