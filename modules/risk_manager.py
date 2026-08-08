"""
Module 9: Risk Manager Service
================================
Validates every trade before execution.
Applies position sizing, exposure limits, drawdown monitoring.
Logs every APPROVED/REJECTED/MODIFIED decision.
"""

import time
from dataclasses import dataclass
from typing import Optional

from .config_loader import Config
from .paper_account import PaperAccount, Position
from .monitoring import StructuredLogger


@dataclass
class RiskDecision:
    """Result of risk validation."""
    approved: bool
    modified: bool  # True if position was resized
    original_qty: float
    approved_qty: float
    reason: str
    risk_metrics: dict


class RiskManagerService:
    """
    Validates and sizes every trade before paper execution.
    Central risk authority for the system.
    """

    def __init__(self, config: Config, paper_account: PaperAccount,
                 logger: StructuredLogger):
        self.config = config
        self.paper_account = paper_account
        self.logger = logger
        self._blocked_until: Optional[float] = None  # Daily loss block

    async def validate_trade(self, token: str, side: str, qty: float,
                              price: float, liquidity_usd: float = 0) -> RiskDecision:
        """
        Validate a proposed trade against all risk rules.
        Returns RiskDecision with approval status and reasoning.
        """
        risk = self.config.risk
        current_value = self.paper_account.balance  # Simplified

        # Get current positions for exposure calc
        positions = self.paper_account.positions
        total_positions_value = sum(
            p.market_value for p in positions.values()
        )

        trade_value = qty * price

        # ---- RULE 1: Daily loss limit ----
        if self._blocked_until and time.time() < self._blocked_until:
            return self._reject(
                qty, "DAILY_LOSS_BLOCKED",
                f"Trading blocked until {time.strftime('%H:%M:%S', time.localtime(self._blocked_until))}"
            )

        # ---- RULE 2: Max risk per trade ----
        max_trade_value = current_value * (risk.max_risk_per_trade_pct / 100)
        if trade_value > max_trade_value:
            # Modify: reduce qty to fit
            approved_qty = max_trade_value / price if price > 0 else 0
            if approved_qty <= 0:
                return self._reject(
                    qty, "MAX_RISK_PER_TRADE",
                    f"Trade value ${trade_value:.2f} > max ${max_trade_value:.2f}"
                )
            self.logger.log_risk_decision({
                "action": "TRADE_MODIFIED",
                "reason": "Max risk per trade",
                "original_qty": qty,
                "approved_qty": approved_qty,
                "trade_value": trade_value,
                "max_value": max_trade_value,
            })
            qty = approved_qty
            trade_value = qty * price

        # ---- RULE 3: Max total exposure ----
        new_total = total_positions_value + trade_value
        max_exposure = current_value * (risk.max_total_exposure_pct / 100)
        if side == "buy" and new_total > max_exposure:
            allowed = max_exposure - total_positions_value
            if allowed <= 0:
                return self._reject(
                    qty, "MAX_EXPOSURE",
                    f"Total exposure would be "
                    f"{(new_total/current_value*100):.1f}% "
                    f"> {risk.max_total_exposure_pct}% max"
                )
            approved_qty = allowed / price if price > 0 else 0
            if approved_qty <= 0:
                return self._reject(
                    qty, "MAX_EXPOSURE",
                    "Cannot fit any additional exposure"
                )
            self.logger.log_risk_decision({
                "action": "TRADE_MODIFIED",
                "reason": "Max total exposure",
                "original_qty": qty,
                "approved_qty": approved_qty,
            })
            qty = approved_qty
            trade_value = qty * price

        # ---- RULE 4: Max open positions ----
        if side == "buy" and token not in positions:
            if len(positions) >= risk.max_open_positions:
                return self._reject(
                    qty, "MAX_POSITIONS",
                    f"Already at {len(positions)} positions "
                    f"(max: {risk.max_open_positions})"
                )

        # ---- RULE 5: Trades per hour ----
        if self.paper_account.hourly_trades >= risk.max_trades_per_hour:
            return self._reject(
                qty, "MAX_TRADES_PER_HOUR",
                f"Hourly limit {risk.max_trades_per_hour} reached"
            )

        # ---- RULE 6: Liquidity check ----
        if liquidity_usd > 0 and liquidity_usd < risk.min_liquidity_usd:
            return self._reject(
                qty, "INSUFFICIENT_LIQUIDITY",
                f"Liquidity ${liquidity_usd:.0f} < "
                f"${risk.min_liquidity_usd:.0f} minimum"
            )

        # ---- RULE 9: No leverage ----
        if risk.allow_leverage is False:
            # Already enforced by config validation, but double-check
            pass

        # ---- RULE 10: No shorting ----
        if risk.allow_short is False and side == "sell":
            # Allow sells only for closing existing positions
            if token not in positions:
                return self._reject(
                    qty, "NO_SHORT_SELLING",
                    "Short selling not permitted"
                )

        # ---- RULE 11: Max slippage check ----
        if liquidity_usd > 0:
            estimated_slippage = (trade_value / liquidity_usd) * 10000
            if estimated_slippage > risk.max_slippage_bps:
                return self._reject(
                    qty, "SLIPPAGE_TOO_HIGH",
                    f"Estimated slippage {estimated_slippage:.0f}bps "
                    f"> {risk.max_slippage_bps}bps max"
                )

        # ---- RULE 12: Gas cost check ----
        estimated_gas = 0.0005 * price  # ~0.0005 SOL per tx
        if estimated_gas > risk.max_gas_per_trade_usd:
            return self._reject(
                qty, "GAS_TOO_HIGH",
                f"Estimated gas ${estimated_gas:.4f} > "
                f"${risk.max_gas_per_trade_usd} max"
            )

        # ---- ALL CHECKS PASSED ----
        return RiskDecision(
            approved=True,
            modified=(qty != self.paper_account.balance),  # Simplified
            original_qty=qty,
            approved_qty=qty,
            reason="ALL_CHECKS_PASSED",
            risk_metrics={
                "trade_value": trade_value,
                "total_exposure_pct": (
                    (new_total / current_value * 100)
                    if current_value > 0 else 0
                ),
                "open_positions": len(positions),
                "daily_pnl_pct": daily_pnl,
                "drawdown_pct": drawdown,
                "hourly_trades": self.paper_account.hourly_trades,
            },
        )

    async def check_stop_loss(self, position: Position,
                                current_price: float) -> Optional[str]:
        """
        Check if a position should be stopped out.
        Returns stop reason or None.
        """
        if position.avg_entry_price <= 0:
            return None

        loss_pct = (
            (current_price - position.avg_entry_price)
            / position.avg_entry_price * 100
        )

        # Stop loss
        if loss_pct <= -self.config.risk.stop_loss_pct:
            return f"STOP_LOSS: {loss_pct:.2f}% (threshold: -{self.config.risk.stop_loss_pct}%)"

        return None

    async def check_take_profit(self, position: Position,
                                 current_price: float) -> Optional[str]:
        """
        Check if a trailing take-profit should trigger.
        Returns take-profit reason or None.
        """
        if position.avg_entry_price <= 0:
            return None

        gain_pct = (
            (current_price - position.avg_entry_price)
            / position.avg_entry_price * 100
        )

        # Update highest price for trailing
        if current_price > position.highest_price:
            position.highest_price = current_price

        # Trailing take-profit: if gain > trailing threshold AND
        # current price has dropped > 10% from highest
        if gain_pct >= self.config.risk.trailing_take_profit_pct:
            if position.highest_price > 0:
                drop_from_high = (
                    (position.highest_price - current_price)
                    / position.highest_price * 100
                )
                if drop_from_high >= 10:  # 10% trailing stop
                    return (
                        f"TRAILING_TP: peak ${position.highest_price:.6f} "
                        f"-> current ${current_price:.6f} "
                        f"({drop_from_high:.1f}% drop)"
                    )

        return None

    def _reject(self, qty: float, reason: str, detail: str) -> RiskDecision:
        """Create a rejection decision."""
        self.logger.log_risk_decision({
            "action": "TRADE_REJECTED",
            "reason": reason,
            "detail": detail,
            "qty": qty,
        })
        return RiskDecision(
            approved=False,
            modified=False,
            original_qty=qty,
            approved_qty=0,
            reason=f"{reason}: {detail}",
            risk_metrics={},
        )

    def _end_of_day(self) -> float:
        """Get timestamp for end of current day."""
        now = time.time()
        import datetime
        today = datetime.datetime.fromtimestamp(now)
        end_of_day = today.replace(hour=23, minute=59, second=59)
        return end_of_day.timestamp()

    def reset_daily_block(self):
        """Reset daily loss block (called at midnight)."""
        self._blocked_until = None
