import time
import redis
from typing import Tuple, Optional
import logging

from .config import settings

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self.use_redis = settings.ENABLE_REDIS and settings.REDIS_URL
        self.ip_requests = {}
        self.wallet_requests = {}
        self.total_requests = 0
        self.unique_ips = set()
        self.unique_wallets = set()
        
        if self.use_redis:
            try:
                self.redis_client = redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=settings.REDIS_DECODE_RESPONSES,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                )
                self.redis_client.ping()
                logger.info("Redis rate limiter initialized")
            except Exception as e:
                logger.warning(f"Redis connection failed, using in-memory: {e}")
                self.use_redis = False
                self.redis_client = None
    
    def check_ip(self, ip: str) -> Tuple[bool, int]:
        key = f"ratelimit:ip:{ip}"
        cooldown = settings.RATE_LIMIT_IP_SECONDS
        
        if self.use_redis and self.redis_client:
            try:
                last_request = self.redis_client.get(key)
                if last_request:
                    elapsed = time.time() - float(last_request)
                    if elapsed < cooldown:
                        wait_time = int(cooldown - elapsed)
                        logger.info(f"IP {ip} rate limited, wait {wait_time}s")
                        return False, wait_time
                return True, 0
            except Exception as e:
                logger.error(f"Redis check_ip error: {e}")
                return self._check_ip_memory(ip, cooldown)
        else:
            return self._check_ip_memory(ip, cooldown)
    
    def _check_ip_memory(self, ip: str, cooldown: int) -> Tuple[bool, int]:
        if ip not in self.ip_requests:
            return True, 0
        elapsed = time.time() - self.ip_requests[ip]
        if elapsed >= cooldown:
            return True, 0
        
        wait_time = int(cooldown - elapsed)
        return False, wait_time
    
    def check_wallet(self, wallet: str) -> Tuple[bool, int]:
        wallet_lower = wallet.lower()
        key = f"ratelimit:wallet:{wallet_lower}"
        cooldown = settings.RATE_LIMIT_WALLET_SECONDS
        
        if self.use_redis and self.redis_client:
            try:
                last_request = self.redis_client.get(key)
                if last_request:
                    elapsed = time.time() - float(last_request)
                    if elapsed < cooldown:
                        wait_time = int(cooldown - elapsed)
                        logger.info(f"Wallet {wallet} rate limited, wait {wait_time}s")
                        return False, wait_time
                return True, 0
            except Exception as e:
                logger.error(f"Redis check_wallet error: {e}")
                return self._check_wallet_memory(wallet_lower, cooldown)
        else:
            return self._check_wallet_memory(wallet_lower, cooldown)
    
    def _check_wallet_memory(self, wallet: str, cooldown: int) -> Tuple[bool, int]:
        if wallet not in self.wallet_requests:
            return True, 0
        
        elapsed = time.time() - self.wallet_requests[wallet]
        if elapsed >= cooldown:
            return True, 0
        
        wait_time = int(cooldown - elapsed)
        return False, wait_time
    
    def record_request(self, ip: str, wallet: str):
        now = time.time()
        wallet_lower = wallet.lower()

        if self.use_redis and self.redis_client:
            try:
                self.redis_client.setex(
                    f"ratelimit:ip:{ip}",
                    settings.RATE_LIMIT_IP_SECONDS,
                    str(now)
                )
                self.redis_client.setex(
                    f"ratelimit:wallet:{wallet_lower}",
                    settings.RATE_LIMIT_WALLET_SECONDS,
                    str(now)
                )
            except Exception as e:
                logger.error(f"Redis record_request error: {e}")
                self.ip_requests[ip] = now
                self.wallet_requests[wallet_lower] = now
        else:
            self.ip_requests[ip] = now
            self.wallet_requests[wallet_lower] = now

        self.total_requests += 1
        self.unique_ips.add(ip)
        self.unique_wallets.add(wallet_lower)
        
        logger.info(
            f"Recorded request - IP: {ip}, Wallet: {wallet}, "
            f"Total: {self.total_requests}"
        )
    
    def get_stats(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "unique_ips": len(self.unique_ips),
            "unique_wallets": len(self.unique_wallets),
            "using_redis": self.use_redis,
        }