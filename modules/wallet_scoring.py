"""
Module 6: Wallet Scoring Service
==================================
Analyzes and scores wallets from the discovery service.
Applies exclusion filters for bots, insiders, whales, etc.
Outputs scored WalletProfile objects.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .config_loader import Config
from .market_data import MarketDataService
from .wallet_discovery import WalletCandidate
from .monitoring import StructuredLogger


class ExclusionFlag(Enum):
    BOT = "bot"
    WHALE_DUMP = "whale_dump"
    INSIDER = "insider"
    RUG_PULLER = "rug_puller"
    LOW_VALUE = "low_value"
    WASH_TRADER = "wash_trader"
    FRONT_RUNNER = "front_runner"


@dataclass
class WalletProfile:
    """Scored and filtered wallet profile."""
    address: str
    score: float  # 0-100
    total_pnl_usd: float
    win_rate: float
    trade_count: int
    profit_factor: float
    tokens_traded: list
    exclusion_flags: list = field(default_factory=list)
    is_eligible: bool = True
    rejection_reasons: list = field(default_factory=list)
    scored_at: float = 0.0


class WalletScoringService:
    """
    Scores wallets on a 0-100 scale and applies exclusion filters.
    Only wallets with score >= min_score and no exclusion flags are eligible.
    """

    def __init__(self, config: Config, market_data: MarketDataService,
                 logger: StructuredLogger):
        self.config = config
        self.market_data = market_data
        self.logger = logger
        self._scored_wallets: dict[str, WalletProfile] = {}

    @property
    def eligible_wallets(self) -> dict[str, WalletProfile]:
        """Only wallets that passed all filters."""
        return {
            addr: wp for addr, wp in self._scored_wallets.items()
            if wp.is_eligible
        }

    @property
    def all_scored(self) -> dict[str, WalletProfile]:
        return self._scored_wallets.copy()

    async def score_wallets(self, candidates: list[WalletCandidate]) -> list[WalletProfile]:
        """
        Score and filter wallet candidates.
        Returns only eligible wallets sorted by score.
        """
        profiles = []
        for candidate in candidates:
            profile = await self._analyze_and_score(candidate)
            profiles.append(profile)
            self._scored_wallets[candidate.address] = profile

        # Filter eligible only
        eligible = [p for p in profiles if p.is_eligible]
        eligible.sort(key=lambda p: p.score, reverse=True)

        self.logger.log_wallet_analysis({
            "action": "SCORING_COMPLETE",
            "total_scored": len(profiles),
            "eligible": len(eligible),
            "excluded": len(profiles) - len(eligible),
        })

        return eligible

    async def _analyze_and_score(self, candidate: WalletCandidate) -> WalletProfile:
        """Analyze a single wallet and produce a scored profile."""
        flags = []
        rejection_reasons = []

        # ---- EXCLUSION FILTERS ----

        # Check for bot behavior
        if self._is_bot(candidate):
            flags.append(ExclusionFlag.BOT.value)
            rejection_reasons.append(
                f"Bot detected: {candidate.trade_count} trades, "
                f"avg interval too regular"
            )

        # Check for whale dump pattern
        if self._is_whale_dump(candidate):
            flags.append(ExclusionFlag.WHALE_DUMP.value)
            rejection_reasons.append(
                "Whale dump pattern: large position unloading"
            )

        # Check for insider trading
        if self._is_insider(candidate):
            flags.append(ExclusionFlag.INSIDER.value)
            rejection_reasons.append(
                "Insider: trades too early after token creation"
            )

        # Check for rug puller history
        if self._is_rug_puller(candidate):
            flags.append(ExclusionFlag.RUG_PULLER.value)
            rejection_reasons.append(
                "Rug puller: wallet associated with rug pulls"
            )

        # Check for low value
        if self._is_low_value(candidate):
            flags.append(ExclusionFlag.LOW_VALUE.value)
            rejection_reasons.append(
                f"Low value: PnL ${candidate.total_pnl_usd:.2f} "
                f"< $500 minimum"
            )

        # Check for wash trading
        if self._is_wash_trader(candidate):
            flags.append(ExclusionFlag.WASH_TRADER.value)
            rejection_reasons.append(
                "Wash trading: suspicious circular patterns"
            )

        # Check for front-running
        if self._is_front_runner(candidate):
            flags.append(ExclusionFlag.FRONT_RUNNER.value)
            rejection_reasons.append(
                "Front-runner: consistently high gas priority"
            )

        # ---- SCORING ----
        score = self._calculate_score(candidate, flags)

        is_eligible = (
            len(flags) == 0
            and score >= self.config.wallet_scoring.min_score
        )

        profile = WalletProfile(
            address=candidate.address,
            score=score,
            total_pnl_usd=candidate.total_pnl_usd,
            win_rate=candidate.win_rate,
            trade_count=candidate.trade_count,
            profit_factor=candidate.profit_factor,
            tokens_traded=candidate.tokens_traded,
            exclusion_flags=flags,
            is_eligible=is_eligible,
            rejection_reasons=rejection_reasons,
            scored_at=time.time(),
        )

        self.logger.log_wallet_analysis({
            "action": "WALLET_SCORED",
            "wallet": candidate.address[:12] + "...",
            "score": score,
            "eligible": is_eligible,
            "flags": flags,
            "pnl": candidate.total_pnl_usd,
            "win_rate": candidate.win_rate,
        })

        return profile

    # ---- EXCLUSION DETECTION METHODS ----

    def _is_bot(self, c: WalletCandidate) -> bool:
        """Detect bot wallets: >50 trades/hr or identical patterns."""
        bd = self.config.wallet_scoring.bot_detection
        if c.trade_count > bd["max_trades_per_hour"]:
            return True
        # Simplified: check if all trades are same size (pattern detection)
        if c.trade_count > 10:
            sizes = c.tokens_traded
            if len(set(str(s) for s in sizes)) < 3 and c.trade_count > 20:
                return True
        return False

    def _is_whale_dump(self, c: WalletCandidate) -> bool:
        """Detect whale dump pattern: large negative PnL in short time."""
        if c.total_pnl_usd < -5000:  # Lost > $5000 quickly
            time_span = c.last_seen - c.first_seen
            if time_span > 0 and time_span < 3600:  # Within 1 hour
                return True
        return False

    def _is_insider(self, c: WalletCandidate) -> bool:
        """Detect insider: trades immediately after token creation."""
        # This would need token creation timestamps
        # Simplified: if wallet trades tokens < 3 blocks old
        # Placeholder - would need deeper on-chain analysis
        return False

    def _is_rug_puller(self, c: WalletCandidate) -> bool:
        """Detect rug puller: wallet created tokens that were rugged."""
        # Would need historical data about tokens created by this wallet
        # Placeholder for future implementation
        return False

    def _is_low_value(self, c: WalletCandidate) -> bool:
        """Detect low-value wallets: PnL < $500 in 30 days."""
        return abs(c.total_pnl_usd) < 500

    def _is_wash_trader(self, c: WalletCandidate) -> bool:
        """Detect wash trading: suspicious circular patterns."""
        # Would need to analyze if same wallets trade back and forth
        # Simplified: if tokens_traded count is suspiciously low for trade_count
        if c.trade_count > 30 and len(c.tokens_traded) < 3:
            return True
        return False

    def _is_front_runner(self, c: WalletCandidate) -> bool:
        """Detect front-runner: consistently high gas priority."""
        # Would need gas price analysis per transaction
        # Placeholder for future implementation
        return False

    # ---- SCORING ----

    def _calculate_score(self, candidate: WalletCandidate,
                          flags: list[str]) -> float:
        """
        Calculate wallet score (0-100) based on weighted criteria.
        Flags reduce score significantly.
        """
        weights = self.config.wallet_scoring.weights

        # Base scores per criterion
        profit_consistency = min(100, max(0, candidate.total_pnl_usd / 50))
        diversification = min(100, len(candidate.tokens_traded) * 10)
        temporal_pattern = min(100, candidate.trade_count * 2)
        position_sizing = min(100, candidate.avg_trade_size_usd / 100)
        wallet_age = min(100, (candidate.last_seen - candidate.first_seen) / 86400 * 10)
        relative_volume = min(100, candidate.trade_count * 3)

        # Weighted score
        score = (
            profit_consistency * weights["profit_consistency"]
            + diversification * weights["diversification"]
            + temporal_pattern * weights["temporal_pattern"]
            + position_sizing * weights["position_sizing"]
            + wallet_age * weights["wallet_age"]
            + relative_volume * weights["relative_volume"]
        )

        # Penalty for each exclusion flag
        flag_penalty = len(flags) * 20
        score = max(0, score - flag_penalty)

        return round(min(100.0, score), 2)
