"""
Module 3: Market Data Service
==============================
Provides current price, liquidity, volume for any Solana token.
Uses dex_api with fallback chain: DexScreener → Birdeye → Solana RPC.
Never invents data. Returns None if no source available.
"""

import time
from dataclasses import dataclass
from typing import Optional

from .config_loader import Config
from .dex_api import DexApiClient, TTLCache
from .monitoring import StructuredLogger


@dataclass
class TokenMarketData:
    """Market data for a single token."""
    token: str
    name: str
    symbol: str
    price_usd: float
    liquidity_usd: float
    volume_24h: float
    fdv: float
    price_impact_1pct: float  # Estimated price impact for 1% of liquidity
    timestamp: float
    source: str  # "dexscreener" | "birdeye" | "fallback"

    @property
    def is_valid(self) -> bool:
        """Check if data is usable (not stale, has price)."""
        age_seconds = time.time() - self.timestamp
        return (
            self.price_usd > 0
            and self.liquidity_usd > 0
            and age_seconds < 60  # Max 60s old
        )


class MarketDataService:
    """
    Centralized market data provider with caching and fallback.
    All data is fetched from public APIs (read-only).
    """

    def __init__(self, config: Config, dex_api: DexApiClient,
                 logger: StructuredLogger):
        self.config = config
        self.dex_api = dex_api
        self.logger = logger
        self._price_cache = TTLCache()
        self._data_cache = TTLCache()

    async def get_price(self, token: str) -> Optional[float]:
        """Get current price. Returns None if unavailable (never invents)."""
        cache_key = f"price:{token}"
        cached = self._price_cache.get(cache_key, 5)  # 5s TTL
        if cached is not None:
            return cached

        price = await self.dex_api.get_token_price(token)
        if price is not None:
            self._price_cache.set(cache_key, price)
        return price

    async def get_token_data(self, token: str) -> Optional[TokenMarketData]:
        """Get full market data for a token."""
        cache_key = f"data:{token}"
        cached = self._data_cache.get(cache_key, 5)
        if cached is not None:
            return cached

        info = await self.dex_api.get_token_info(token)
        if not info:
            return None

        # Calculate estimated price impact for 1% of liquidity
        price_impact = 0.0
        if info["liquidity_usd"] > 0:
            # Simplified: price impact ≈ trade_size / liquidity * 100
            trade_size_1pct = info["liquidity_usd"] * 0.01
            price_impact = (trade_size_1pct / info["liquidity_usd"]) * 100

        data = TokenMarketData(
            token=token,
            name=info.get("name", "Unknown"),
            symbol=info.get("symbol", "???"),
            price_usd=info.get("price_usd", 0),
            liquidity_usd=info.get("liquidity_usd", 0),
            volume_24h=info.get("volume_24h", 0),
            fdv=info.get("fdv", 0),
            price_impact_1pct=price_impact,
            timestamp=time.time(),
            source="dexscreener",
        )

        if data.is_valid:
            self._data_cache.set(cache_key, data)
            return data

        self.logger.log_error({
            "action": "MARKET_DATA_INVALID",
            "token": token,
            "price": data.price_usd,
            "liquidity": data.liquidity_usd,
            "message": "Token data is invalid or stale. Refusing to use.",
        })
        return None

    async def get_multiple_prices(self, tokens: list[str]) -> dict[str, float]:
        """Get prices for multiple tokens. Only returns valid prices."""
        prices = {}
        for token in tokens:
            price = await self.get_price(token)
            if price is not None:
                prices[token] = price
        return prices

    async def get_top_tokens(self, count: int = 20) -> list[dict]:
        """Get top memecoins by volume."""
        return await self.dex_api.get_top_tokens(count)

    async def get_token_trades(self, token: str,
                                limit: int = 50) -> list[dict]:
        """Get recent trades for a token."""
        return await self.dex_api.get_token_trades(token, limit)

    async def get_wallet_trades(self, wallet: str,
                                 limit: int = 20) -> list[dict]:
        """Get recent wallet transactions."""
        return await self.dex_api.get_wallet_transactions(wallet, limit)

    def invalidate_cache(self, token: Optional[str] = None):
        """Invalidate cache for a specific token or all."""
        if token:
            self._price_cache._store.pop(f"price:{token}", None)
            self._data_cache._store.pop(f"data:{token}", None)
        else:
            self._price_cache.clear()
            self._data_cache.clear()
