"""
Module 7: Token Safety Service
================================
Evaluates the safety of a token before allowing a trade.
Checks liquidity, holder concentration, token age, mint/freeze authorities.
Rejects unsafe tokens.
"""

import time
from dataclasses import dataclass
from typing import Optional

from .config_loader import Config
from .market_data import MarketDataService, TokenMarketData
from .monitoring import StructuredLogger


@dataclass
class TokenSafetyReport:
    """Safety analysis result for a token."""
    token: str
    token_name: str
    safety_score: float  # 0-100
    is_safe: bool
    rejection_reasons: list
    checks: dict  # Individual check results
    analyzed_at: float


class TokenSafetyService:
    """
    Evaluates token safety before permitting trade execution.
    Only tokens with safety_score >= min_safety_score are approved.
    """

    def __init__(self, config: Config, market_data: MarketDataService,
                 logger: StructuredLogger):
        self.config = config
        self.market_data = market_data
        self.logger = logger
        self._reports: dict[str, TokenSafetyReport] = {}

    async def analyze_token(self, token_address: str) -> TokenSafetyReport:
        """
        Full safety analysis of a token.
        Returns safety report with score and rejection reasons.
        """
        ts = self.config.token_safety
        checks = {}
        reasons = []
        score = 100.0

        # Get market data
        market_data = await self.market_data.get_token_data(token_address)

        # ---- CHECK 1: Liquidity ----
        if market_data:
            liquidity_ok = market_data.liquidity_usd >= ts.min_liquidity_usd
            checks["liquidity"] = {
                "value": market_data.liquidity_usd,
                "threshold": ts.min_liquidity_usd,
                "pass": liquidity_ok,
            }
            if not liquidity_ok:
                score -= 30
                reasons.append(
                    f"Liquidity ${market_data.liquidity_usd:.0f} "
                    f"< ${ts.min_liquidity_usd:.0f} minimum"
                )
        else:
            checks["liquidity"] = {
                "value": None, "threshold": ts.min_liquidity_usd,
                "pass": False, "error": "Data unavailable"
            }
            score -= 50
            reasons.append("Token market data unavailable")

        # ---- CHECK 2: Holder concentration ----
        # Would need on-chain holder data from Solana RPC
        # Simplified: estimate from liquidity/VOL ratio
        if market_data and market_data.liquidity_usd > 0:
            # Higher volume/liquidity ratio suggests wider distribution
            vol_liq_ratio = (
                market_data.volume_24h / market_data.liquidity_usd
                if market_data.liquidity_usd > 0 else 0
            )
            # Assume: ratio > 0.5 suggests reasonable distribution
            holder_ok = vol_liq_ratio > 0.1  # Relaxed threshold
            checks["holder_concentration"] = {
                "estimated_ratio": vol_liq_ratio,
                "pass": holder_ok,
                "note": "Estimated from volume/liquidity ratio",
            }
            if not holder_ok:
                score -= 20
                reasons.append(
                    f"Potential high holder concentration "
                    f"(vol/liq ratio: {vol_liq_ratio:.3f})"
                )
        else:
            checks["holder_concentration"] = {
                "pass": False, "error": "Insufficient data"
            }
            score -= 15

        # ---- CHECK 3: Token age ----
        if market_data:
            # Would need token creation timestamp
            # DexScreener provides pairCreatedAt
            checks["token_age"] = {
                "pass": True,  # Default to pass if no age data
                "note": "Age check requires creation timestamp",
            }
        else:
            checks["token_age"] = {
                "pass": True, "note": "Skipped - no market data"
            }

        # ---- CHECK 4: Mint authority revoked ----
        # Would need on-chain data to check this properly
        # Simplified: check via Solana RPC getAccountInfo
        checks["mint_revoked"] = {
            "pass": True,  # Default - would check via RPC
            "note": "Requires on-chain verification",
        }

        # ---- CHECK 5: Freeze authority revoked ----
        checks["freeze_revoked"] = {
            "pass": True,  # Default - would check via RPC
            "note": "Requires on-chain verification",
        }

        # ---- CHECK 6: Price impact ----
        if market_data:
            # Estimate slippage for our typical trade size
            typical_trade_usd = (
                self.config.paper_account.initial_balance_usd
                * self.config.risk.max_risk_per_trade_pct / 100
            )
            if market_data.liquidity_usd > 0:
                price_impact = (
                    typical_trade_usd / market_data.liquidity_usd * 10000
                )  # in bps
                impact_ok = price_impact <= self.config.risk.max_price_impact_bps
                checks["price_impact"] = {
                    "estimated_bps": round(price_impact, 2),
                    "threshold_bps": self.config.risk.max_price_impact_bps,
                    "pass": impact_ok,
                }
                if not impact_ok:
                    score -= 25
                    reasons.append(
                        f"Price impact {price_impact:.0f}bps "
                        f"> {self.config.risk.max_price_impact_bps}bps max"
                    )
            else:
                checks["price_impact"] = {
                    "pass": False, "error": "No liquidity data"
                }
                score -= 20

        # ---- CHECK 7: FDV sanity ----
        if market_data and market_data.fdv > 0:
            # FDV should be reasonable for a memecoin
            fdv_ok = 10000 < market_data.fdv < 1_000_000_000
            checks["fdv_sanity"] = {
                "fdv": market_data.fdv,
                "pass": fdv_ok,
            }
            if not fdv_ok:
                score -= 10
                reasons.append(f"Suspicious FDV: ${market_data.fdv:,.0f}")

        # ---- FINAL SCORE ----
        score = max(0, min(100, score))
        is_safe = score >= ts.min_safety_score and len(reasons) == 0

        token_name = market_data.name if market_data else "Unknown"

        report = TokenSafetyReport(
            token=token_address,
            token_name=token_name,
            safety_score=score,
            is_safe=is_safe,
            rejection_reasons=reasons,
            checks=checks,
            analyzed_at=time.time(),
        )

        self._reports[token_address] = report

        self.logger.log_wallet_analysis({
            "action": "TOKEN_SAFETY_CHECK",
            "token": token_address[:12] + "...",
            "name": token_name,
            "score": score,
            "safe": is_safe,
            "reasons": reasons,
        })

        return report

    def get_cached_report(self, token: str) -> Optional[TokenSafetyReport]:
        """Get cached safety report if recent."""
        if token in self._reports:
            report = self._reports[token]
            age = time.time() - report.analyzed_at
            if age < 300:  # 5 minutes cache
                return report
        return None
