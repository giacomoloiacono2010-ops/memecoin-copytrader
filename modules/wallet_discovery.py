"""
Module 5: Wallet Discovery Service
====================================
Automatically finds profitable wallets to copy in the memecoin space.
Scans top tokens, extracts traders, aggregates performance.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from .config_loader import Config
from .market_data import MarketDataService
from .monitoring import StructuredLogger


@dataclass
class WalletCandidate:
    """A wallet discovered as a potential copy target."""
    address: str
    total_pnl_usd: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    profit_factor: float = 0.0
    tokens_traded: list = field(default_factory=list)
    avg_trade_size_usd: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    discovery_score: float = 0.0


class WalletDiscoveryService:
    """
    Scans the Solana memecoin ecosystem to find profitable wallets.
    Runs periodically (every 4 hours by default).
    """

    def __init__(self, config: Config, market_data: MarketDataService,
                 logger: StructuredLogger):
        self.config = config
        self.market_data = market_data
        self.logger = logger
        self._discovered_wallets: dict[str, WalletCandidate] = {}
        self._last_scan_time = 0.0

    @property
    def wallets(self) -> dict[str, WalletCandidate]:
        return self._discovered_wallets.copy()

    @property
    def should_rescan(self) -> bool:
        """Check if it's time to rescan."""
        hours_since = (time.time() - self._last_scan_time) / 3600
        return hours_since >= self.config.wallet_discovery.rebalance_interval_hours

    async def discover_wallets(self) -> list[WalletCandidate]:
        """
        Main discovery pipeline:
        1. Get top memecoins by volume
        2. For each token, get recent traders
        3. Aggregate wallet performance
        4. Filter and rank
        """
        self.logger.log_state_change({
            "action": "DISCOVERY_STARTED",
            "timestamp": time.time(),
        })

        wd = self.config.wallet_discovery

        # Step 1: Get top tokens
        top_tokens = await self.market_data.get_top_tokens(
            wd.top_tokens_scan_count
        )
        if not top_tokens:
            self.logger.log_error({
                "action": "DISCOVERY_FAILED",
                "reason": "No tokens found",
            })
            return []

        self.logger.log_wallet_analysis({
            "action": "DISCOVERY_STEP1",
            "tokens_found": len(top_tokens),
        })

        # Step 2: For each token, get traders
        wallet_stats: dict[str, dict] = {}

        for token_info in top_tokens[:wd.top_tokens_scan_count]:
            token_addr = token_info.get("address", "")
            if not token_addr:
                continue

            trades = await self.market_data.get_token_trades(
                token_addr, limit=wd.top_traders_per_token
            )

            for trade in trades:
                wallet = trade.get("from", "") or trade.get("to", "")
                if not wallet:
                    continue

                if wallet not in wallet_stats:
                    wallet_stats[wallet] = {
                        "pnl": 0.0,
                        "wins": 0,
                        "losses": 0,
                        "trade_count": 0,
                        "tokens": set(),
                        "trade_sizes": [],
                        "timestamps": [],
                    }

                ws = wallet_stats[wallet]
                ws["trade_count"] += 1
                ws["tokens"].add(token_addr)
                ws["trade_sizes"].append(trade.get("usd_value", 0))
                ws["timestamps"].append(trade.get("timestamp", 0))

                # Approximate PnL from trade direction
                usd_val = trade.get("usd_value", 0)
                if trade.get("side", "").lower() == "sell":
                    ws["pnl"] += usd_val  # Simplified: sell = profit
                    ws["wins"] += 1
                else:
                    ws["pnl"] -= usd_val  # Simplified: buy = cost
                    ws["losses"] += 1

        self.logger.log_wallet_analysis({
            "action": "DISCOVERY_STEP2",
            "wallets_found": len(wallet_stats),
        })

        # Step 3: Create WalletCandidate objects
        candidates = []
        for addr, stats in wallet_stats.items():
            if stats["trade_count"] < wd.min_trades_per_wallet:
                continue

            total = stats["wins"] + stats["losses"]
            win_rate = (stats["wins"] / total * 100) if total > 0 else 0
            profit_factor = (
                (stats["wins"] / stats["losses"])
                if stats["losses"] > 0
                else float('inf')
            )

            avg_trade_size = (
                sum(stats["trade_sizes"]) / len(stats["trade_sizes"])
                if stats["trade_sizes"] else 0
            )

            candidate = WalletCandidate(
                address=addr,
                total_pnl_usd=stats["pnl"],
                win_rate=win_rate,
                trade_count=stats["trade_count"],
                profit_factor=profit_factor,
                tokens_traded=list(stats["tokens"]),
                avg_trade_size_usd=avg_trade_size,
                first_seen=min(stats["timestamps"]) if stats["timestamps"] else 0,
                last_seen=max(stats["timestamps"]) if stats["timestamps"] else 0,
            )

            # Step 4: Initial filtering
            if (win_rate >= wd.min_win_rate_pct
                    and profit_factor >= wd.min_profit_factor):
                # Calculate discovery score
                candidate.discovery_score = self._calculate_score(candidate)
                candidates.append(candidate)

        # Sort by score descending
        candidates.sort(key=lambda c: c.discovery_score, reverse=True)

        # Take top N
        top_candidates = candidates[:wd.max_wallets_to_copy]

        self._discovered_wallets = {c.address: c for c in top_candidates}
        self._last_scan_time = time.time()

        self.logger.log_wallet_analysis({
            "action": "DISCOVERY_COMPLETE",
            "total_candidates": len(candidates),
            "selected": len(top_candidates),
            "top_score": top_candidates[0].discovery_score if top_candidates else 0,
        })

        return top_candidates

    def _calculate_score(self, candidate: WalletCandidate) -> float:
        """Calculate initial discovery score (0-100)."""
        score = 0.0

        # PnL score (0-30)
        if candidate.total_pnl_usd > 0:
            score += min(30, candidate.total_pnl_usd / 100)

        # Win rate score (0-25)
        score += min(25, candidate.win_rate / 4)

        # Profit factor score (0-20)
        if candidate.profit_factor > 0:
            score += min(20, candidate.profit_factor * 10)

        # Trade frequency score (0-15)
        score += min(15, candidate.trade_count / 10)

        # Diversification score (0-10)
        score += min(10, len(candidate.tokens_traded))

        return min(100.0, score)
