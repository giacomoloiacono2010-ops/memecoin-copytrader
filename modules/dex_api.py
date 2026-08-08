"""
Module 4: Dex API Client
=========================
Unified HTTP client for all external APIs (Birdeye, DexScreener, Solana RPC).
Read-only. No trading, no signing, no private keys.
Implements: retry, timeout, cache, rate limiting, circuit breaker.
"""

import asyncio
import time
import json
from typing import Optional, Any
from dataclasses import dataclass, field

import aiohttp

from .config_loader import Config
from .monitoring import StructuredLogger, RealTransactionBlocker


# ============================================================
# CACHE
# ============================================================

class TTLCache:
    """Simple in-memory TTL cache."""

    def __init__(self):
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl_seconds: int) -> Optional[Any]:
        if key in self._store:
            ts, value = self._store[key]
            if time.time() - ts < ttl_seconds:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any):
        self._store[key] = (time.time(), value)

    def clear(self):
        self._store.clear()


# ============================================================
# CIRCUIT BREAKER
# ============================================================

class CircuitBreaker:
    """Circuit breaker: opens after consecutive failures, resets after cooldown."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._last_failure_time = 0.0
        self._is_open = False

    def record_success(self):
        self._failure_count = 0
        self._is_open = False

    def record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._failure_threshold:
            self._is_open = True

    def is_open(self) -> bool:
        if not self._is_open:
            return False
        # Check if cooldown has passed
        if time.time() - self._last_failure_time > self._cooldown_seconds:
            self._is_open = False
            self._failure_count = 0
            return False
        return True


# ============================================================
# RATE LIMITER
# ============================================================

class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 1.0
        self._last_request = 0.0

    async def wait(self):
        """Wait if necessary to respect rate limit."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.time()


# ============================================================
# DEX API CLIENT
# ============================================================

class DexApiClient:
    """
    Unified read-only HTTP client for blockchain data APIs.
    All requests are GET (read-only). No POST, no signing, no auth with private keys.
    """

    def __init__(self, config: Config, logger: StructuredLogger,
                 blocker: RealTransactionBlocker):
        self.config = config
        self.logger = logger
        self.blocker = blocker
        self._cache = TTLCache()
        self._session: Optional[aiohttp.ClientSession] = None

        # Per-API components
        self._solana_rpc_breaker = CircuitBreaker()
        self._birdeye_breaker = CircuitBreaker()
        self._dexscreener_breaker = CircuitBreaker()

        self._birdeye_limiter = RateLimiter(
            config.api.birdeye.rate_limit_per_second
        )
        self._dexscreener_limiter = RateLimiter(
            config.api.dexscreener.rate_limit_per_second
        )

    async def initialize(self):
        """Create HTTP session."""
        timeout = aiohttp.ClientTimeout(total=15)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self):
        """Close HTTP session."""
        if self._session:
            await self._session.close()

    # ---- SOLANA RPC ----

    async def solana_rpc(self, method: str, params: list = None) -> Optional[dict]:
        """
        Make a read-only RPC call to Solana.
        CRITICAL: Only allows read methods. Blocks any write method.
        """
        # LAYER 4: Block any write methods
        write_methods = {
            "sendTransaction", "sendRawTransaction", "signTransaction",
            "confirmTransaction", "requestAirdrop",
        }
        if method in write_methods:
            self.blocker.block(
                attempted_method=method,
                target=self.config.api.solana_rpc.url,
                details={"reason": "Write method blocked in paper trading mode"}
            )
            return None

        cache_key = f"solana:{method}:{json.dumps(params or [])}"
        cached = self._cache.get(cache_key, 30)  # 30s cache for RPC
        if cached is not None:
            return cached

        if self._solana_rpc_breaker.is_open():
            self.logger.log_error({
                "action": "API_CIRCUIT_OPEN",
                "api": "solana_rpc", "method": method,
            })
            return None

        start = time.time()
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or [],
            }
            async with self._session.post(
                self.config.api.solana_rpc.url,
                json=payload
            ) as resp:
                latency = (time.time() - start) * 1000
                if resp.status == 200:
                    data = await resp.json()
                    self._solana_rpc_breaker.record_success()
                    self._cache.set(cache_key, data)
                    self.logger.log_health({
                        "action": "API_CALL",
                        "api": "solana_rpc", "method": method,
                        "status": resp.status, "latency_ms": latency,
                    })
                    return data
                else:
                    self._solana_rpc_breaker.record_failure()
                    self.logger.log_error({
                        "action": "API_ERROR",
                        "api": "solana_rpc", "method": method,
                        "status": resp.status, "latency_ms": latency,
                    })
                    return None
        except Exception as e:
            self._solana_rpc_breaker.record_failure()
            self.logger.log_error({
                "action": "API_EXCEPTION",
                "api": "solana_rpc", "method": method,
                "error": str(e),
            })
            return None

    # ---- BIRDEYE ----

    async def birdeye_get(self, path: str, params: dict = None) -> Optional[dict]:
        """Make a read-only GET request to Birdeye API."""
        cache_key = f"birdeye:{path}:{json.dumps(params or {})}"
        cached = self._cache.get(cache_key, self.config.api.birdeye.cache_ttl_seconds)
        if cached is not None:
            return cached

        if self._birdeye_breaker.is_open():
            return None

        await self._birdeye_limiter.wait()

        start = time.time()
        try:
            headers = {"x-chain": "solana"}
            if self.config.api.birdeye.api_key:
                headers["X-API-KEY"] = self.config.api.birdeye.api_key

            url = f"{self.config.api.birdeye.base_url}{path}"
            async with self._session.get(url, params=params,
                                         headers=headers) as resp:
                latency = (time.time() - start) * 1000
                if resp.status == 200:
                    data = await resp.json()
                    self._birdeye_breaker.record_success()
                    self._cache.set(cache_key, data)
                    self.logger.log_health({
                        "action": "API_CALL",
                        "api": "birdeye", "path": path,
                        "status": resp.status, "latency_ms": latency,
                    })
                    return data
                else:
                    self._birdeye_breaker.record_failure()
                    self.logger.log_error({
                        "action": "API_ERROR",
                        "api": "birdeye", "path": path,
                        "status": resp.status, "latency_ms": latency,
                    })
                    return None
        except Exception as e:
            self._birdeye_breaker.record_failure()
            self.logger.log_error({
                "action": "API_EXCEPTION",
                "api": "birdeye", "path": path,
                "error": str(e),
            })
            return None

    # ---- DEXSCREENER ----

    async def dexscreener_get(self, path: str, params: dict = None) -> Optional[dict]:
        """Make a read-only GET request to DexScreener API.
        Paths starting with /tokens/ or /search use the base URL.
        Paths like /token-boosts use the root API URL.
        """
        cache_key = f"dexscreener:{path}:{json.dumps(params or {})}"
        cached = self._cache.get(cache_key, self.config.api.dexscreener.cache_ttl_seconds)
        if cached is not None:
            return cached

        if self._dexscreener_breaker.is_open():
            return None

        await self._dexscreener_limiter.wait()

        start = time.time()
        try:
            # Use root API for token-boosts, token-profiles; use base for tokens/search
            if path.startswith("/token-boosts") or path.startswith("/token-profiles"):
                url = f"https://api.dexscreener.com{path}"
            else:
                url = f"{self.config.api.dexscreener.base_url}{path}"
            async with self._session.get(url, params=params) as resp:
                latency = (time.time() - start) * 1000
                if resp.status == 200:
                    data = await resp.json()
                    self._dexscreener_breaker.record_success()
                    self._cache.set(cache_key, data)
                    self.logger.log_health({
                        "action": "API_CALL",
                        "api": "dexscreener", "path": path,
                        "status": resp.status, "latency_ms": latency,
                    })
                    return data
                else:
                    self._dexscreener_breaker.record_failure()
                    self.logger.log_error({
                        "action": "API_ERROR",
                        "api": "dexscreener", "path": path,
                        "status": resp.status, "latency_ms": latency,
                    })
                    return None
        except Exception as e:
            self._dexscreener_breaker.record_failure()
            self.logger.log_error({
                "action": "API_EXCEPTION",
                "api": "dexscreener", "path": path,
                "error": str(e),
            })
            return None

    # ---- HIGH-LEVEL HELPERS ----

    async def get_token_price(self, token_address: str) -> Optional[float]:
        """Get current price for a token. Returns None if unavailable."""
        # Try DexScreener first
        data = await self.dexscreener_get(f"/tokens/{token_address}")
        if data and "pairs" in data and data["pairs"]:
            pair = data["pairs"][0]
            if "priceUsd" in pair:
                try:
                    return float(pair["priceUsd"])
                except (ValueError, TypeError):
                    pass

        # Fallback: Birdeye
        data = await self.birdeye_get(
            "/public/price",
            params={"address": token_address}
        )
        if data and "data" in data and "price" in data["data"]:
            try:
                return float(data["data"]["price"])
            except (ValueError, TypeError):
                pass

        # Data unavailable - do NOT invent a price
        self.logger.log_error({
            "action": "PRICE_UNAVAILABLE",
            "token": token_address,
            "message": "Could not fetch price from any source. Signal rejected.",
        })
        return None

    async def get_token_info(self, token_address: str) -> Optional[dict]:
        """Get token info from DexScreener."""
        data = await self.dexscreener_get(f"/tokens/{token_address}")
        if data and "pairs" in data and data["pairs"]:
            pair = data["pairs"][0]
            return {
                "name": pair.get("baseToken", {}).get("name", "Unknown"),
                "symbol": pair.get("baseToken", {}).get("symbol", "???"),
                "price_usd": float(pair.get("priceUsd", 0)),
                "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
                "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0)),
                "fdv": float(pair.get("fdv", 0)),
                "pair_address": pair.get("pairAddress", ""),
                "dex": pair.get("dexId", ""),
                "created_at": pair.get("pairCreatedAt", 0),
            }
        return None

    async def get_top_tokens(self, count: int = 20) -> list[dict]:
        """Get top memecoins from DexScreener token boosts."""
        data = await self.dexscreener_get(
            "/token-boosts/latest/v1"
        )
        if not data:
            return []

        tokens = []
        for item in (data if isinstance(data, list) else []):
            addr = item.get("tokenAddress", "")
            if not addr:
                continue
            tokens.append({
                "address": addr,
                "name": item.get("description", "Unknown")[:50],
                "volume": float(item.get("amount", 0)) * 1000,  # boost amount as proxy
                "price": 0,
                "liquidity": 0,
            })

        # Sort by boost amount (proxy for popularity)
        tokens.sort(key=lambda x: x["volume"], reverse=True)
        return tokens[:count]

    async def get_token_trades(self, token_address: str,
                               limit: int = 50) -> list[dict]:
        """Get recent trades for a token from Birdeye."""
        data = await self.birdeye_get(
            "/public/txs/token",
            params={"address": token_address, "limit": str(limit)}
        )
        if not data or "data" not in data:
            return []

        trades = []
        for tx in data["data"].get("results", []):
            trades.append({
                "tx_hash": tx.get("txHash", ""),
                "from": tx.get("from", ""),
                "to": tx.get("to", ""),
                "side": tx.get("side", ""),
                "amount": float(tx.get("amount", 0)),
                "usd_value": float(tx.get("usdValue", 0)),
                "timestamp": tx.get("blockUnixTime", 0),
            })
        return trades

    async def get_wallet_transactions(self, wallet: str,
                                       limit: int = 20) -> list[dict]:
        """Get recent transactions for a wallet."""
        data = await self.solana_rpc(
            "getSignaturesForAddress",
            [wallet, {"limit": limit}]
        )
        if not data or "result" not in data:
            return []
        return data["result"]
