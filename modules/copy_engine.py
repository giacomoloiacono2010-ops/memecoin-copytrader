"""
Module 8: Copy Engine Service
================================
Core orchestrator: receives signals from monitoring, validates,
applies 200ms delay, and executes paper trades.
This is the heart of the copy-trading system.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from .config_loader import Config
from .paper_account import PaperAccount
from .market_data import MarketDataService
from .token_safety import TokenSafetyService
from .risk_manager import RiskManagerService
from .wallet_scoring import WalletScoringService
from .monitoring import StructuredLogger, KillSwitch


@dataclass
class TradeSignal:
    """A detected signal from a copied wallet."""
    id: str
    token: str
    token_name: str
    side: str  # "buy" | "sell"
    qty: float
    price: float
    source_wallet: str
    detection_time: float
    signal_age_ms: float
    liquidity_usd: float = 0.0


@dataclass
class CopyTradeResult:
    """Result of a copy trade execution."""
    signal_id: str
    executed: bool
    trade_id: Optional[str]
    delay_ms: float
    actual_latency_ms: float
    slippage_bps: float
    rejection_reason: Optional[str]
    risk_decision: str
    token_safety_score: float


class CopyEngineService:
    """
    Core copy-trading engine.
    Receives signals, validates them, applies delay, executes paper trades.

    Flow:
    1. Signal received
    2. Check signal age (max 800ms)
    3. Token safety check
    4. Risk manager validation
    5. 200ms delay (asyncio.sleep)
    6. Paper trade execution
    7. Log everything
    """

    def __init__(self, config: Config, paper_account: PaperAccount,
                 market_data: MarketDataService,
                 token_safety: TokenSafetyService,
                 risk_manager: RiskManagerService,
                 wallet_scoring: WalletScoringService,
                 logger: StructuredLogger,
                 kill_switch: KillSwitch):
        self.config = config
        self.paper_account = paper_account
        self.market_data = market_data
        self.token_safety = token_safety
        self.risk_manager = risk_manager
        self.wallet_scoring = wallet_scoring
        self.logger = logger
        self.kill_switch = kill_switch
        self._signals_processed = 0
        self._signals_rejected = 0
        self._pending_trades: list[asyncio.Task] = []

    async def process_signal(self, signal: TradeSignal) -> CopyTradeResult:
        """
        Process a single trade signal through the full pipeline.
        Returns CopyTradeResult with execution details.
        """
        self._signals_processed += 1
        start_time = time.time()

        self.logger.log_signal({
            "action": "SIGNAL_RECEIVED",
            "signal_id": signal.id,
            "token": signal.token[:12] + "...",
            "side": signal.side,
            "source_wallet": signal.source_wallet[:12] + "...",
            "signal_age_ms": signal.signal_age_ms,
        })

        # ---- STEP 1: Check kill switch ----
        if self.kill_switch.is_triggered:
            self._signals_rejected += 1
            return CopyTradeResult(
                signal_id=signal.id, executed=False, trade_id=None,
                delay_ms=0, actual_latency_ms=0, slippage_bps=0,
                rejection_reason="KILL_SWITCH_ACTIVE",
                risk_decision="BLOCKED",
                token_safety_score=0,
            )

        # ---- STEP 2: Check signal age ----
        if signal.signal_age_ms > self.config.execution.max_signal_age_ms:
            self._signals_rejected += 1
            self.logger.log_signal({
                "action": "SIGNAL_REJECTED",
                "reason": "SIGNAL_TOO_OLD",
                "signal_age_ms": signal.signal_age_ms,
                "max_age_ms": self.config.execution.max_signal_age_ms,
            })
            return CopyTradeResult(
                signal_id=signal.id, executed=False, trade_id=None,
                delay_ms=0, actual_latency_ms=0, slippage_bps=0,
                rejection_reason=(
                    f"Signal too old: {signal.signal_age_ms:.0f}ms "
                    f"> {self.config.execution.max_signal_age_ms}ms max"
                ),
                risk_decision="SKIPPED",
                token_safety_score=0,
            )

        # ---- STEP 3: Token safety check ----
        safety_report = await self.token_safety.analyze_token(signal.token)
        if not safety_report.is_safe:
            self._signals_rejected += 1
            self.logger.log_signal({
                "action": "SIGNAL_REJECTED",
                "reason": "TOKEN_UNSAFE",
                "token": signal.token[:12] + "...",
                "safety_score": safety_report.safety_score,
                "reasons": safety_report.rejection_reasons,
            })
            return CopyTradeResult(
                signal_id=signal.id, executed=False, trade_id=None,
                delay_ms=0, actual_latency_ms=0, slippage_bps=0,
                rejection_reason=(
                    f"Token unsafe: score {safety_report.safety_score} "
                    f"< {self.config.token_safety.min_safety_score} min"
                ),
                risk_decision="SKIPPED",
                token_safety_score=safety_report.safety_score,
            )

        # ---- STEP 4: Risk manager validation ----
        risk_decision = await self.risk_manager.validate_trade(
            token=signal.token,
            side=signal.side,
            qty=signal.qty,
            price=signal.price,
            liquidity_usd=signal.liquidity_usd,
        )

        if not risk_decision.approved:
            self._signals_rejected += 1
            return CopyTradeResult(
                signal_id=signal.id, executed=False, trade_id=None,
                delay_ms=0, actual_latency_ms=0, slippage_bps=0,
                rejection_reason=risk_decision.reason,
                risk_decision="REJECTED",
                token_safety_score=safety_report.safety_score,
            )

        # Use approved quantity (may have been modified by risk manager)
        exec_qty = risk_decision.approved_qty

        # ---- STEP 5: Calculate slippage ----
        slippage_bps = self._simulate_slippage(
            exec_qty, signal.price, signal.liquidity_usd
        )

        # Check if slippage is acceptable
        if slippage_bps > self.config.risk.max_slippage_bps:
            self._signals_rejected += 1
            return CopyTradeResult(
                signal_id=signal.id, executed=False, trade_id=None,
                delay_ms=0, actual_latency_ms=0, slippage_bps=slippage_bps,
                rejection_reason=(
                    f"Slippage {slippage_bps:.0f}bps "
                    f"> {self.config.risk.max_slippage_bps}bps max"
                ),
                risk_decision="APPROVED",
                token_safety_score=safety_report.safety_score,
            )

        # ---- STEP 6: 200ms DELAY ----
        delay_ms = self.config.execution.delay_ms
        delay_seconds = delay_ms / 1000.0

        self.logger.log_signal({
            "action": "EXECUTION_DELAY",
            "signal_id": signal.id,
            "delay_ms": delay_ms,
            "token": signal.token[:12] + "...",
        })

        await asyncio.sleep(delay_seconds)

        # ---- STEP 7: Execute paper trade ----
        exec_price = signal.price * (1 + slippage_bps / 10000)

        if signal.side == "buy":
            trade = await self.paper_account.execute_buy(
                token=signal.token,
                token_name=signal.token_name,
                qty=exec_qty,
                price=exec_price,
                slippage_bps=slippage_bps,
                gas_usd=0.0005 * exec_price,
                source_wallet=signal.source_wallet,
                signal_detection_time=signal.detection_time,
            )
        else:
            trade = await self.paper_account.execute_sell(
                token=signal.token,
                qty=exec_qty,
                price=exec_price,
                slippage_bps=slippage_bps,
                gas_usd=0.0005 * exec_price,
                source_wallet=signal.source_wallet,
                signal_detection_time=signal.detection_time,
            )

        actual_latency_ms = (time.time() - start_time) * 1000

        result = CopyTradeResult(
            signal_id=signal.id,
            executed=True,
            trade_id=trade.id,
            delay_ms=delay_ms,
            actual_latency_ms=actual_latency_ms,
            slippage_bps=slippage_bps,
            rejection_reason=None,
            risk_decision="APPROVED",
            token_safety_score=safety_report.safety_score,
        )

        self.logger.log_trade({
            "action": "COPY_TRADE_EXECUTED",
            "signal_id": signal.id,
            "trade_id": trade.id,
            "token": signal.token[:12] + "...",
            "side": signal.side,
            "qty": exec_qty,
            "price": exec_price,
            "slippage_bps": slippage_bps,
            "target_delay_ms": delay_ms,
            "actual_latency_ms": actual_latency_ms,
            "source_wallet": signal.source_wallet[:12] + "...",
        })

        return result

    def _simulate_slippage(self, qty: float, price: float,
                            liquidity: float) -> float:
        """
        Simulate slippage in basis points.
        Based on trade size relative to liquidity pool.
        """
        if liquidity <= 0 or price <= 0:
            return self.config.execution.default_slippage_bps

        trade_value = qty * price
        # Simplified AMM slippage: impact ≈ trade_size / liquidity * 10000
        slippage = (trade_value / liquidity) * 10000

        # Add random noise (±10 bps)
        import random
        noise = random.uniform(-10, 10)
        slippage += noise

        # Clamp to reasonable range
        return max(1, min(slippage, self.config.risk.max_slippage_bps))

    async def check_stop_losses(self):
        """Check all open positions for stop-loss triggers."""
        positions = self.paper_account.positions
        for token, pos in positions.items():
            current_price = await self.market_data.get_price(token)
            if current_price is None:
                continue

            stop_reason = await self.risk_manager.check_stop_loss(pos, current_price)
            if stop_reason:
                self.logger.log_trade({
                    "action": "STOP_LOSS_TRIGGERED",
                    "token": token[:12] + "...",
                    "reason": stop_reason,
                    "entry_price": pos.avg_entry_price,
                    "current_price": current_price,
                })
                # Execute sell
                await self.paper_account.execute_sell(
                    token=token,
                    qty=pos.qty,
                    price=current_price,
                    slippage_bps=0,
                    gas_usd=0.0005 * current_price,
                    source_wallet=pos.source_wallet,
                )

            tp_reason = await self.risk_manager.check_take_profit(pos, current_price)
            if tp_reason:
                self.logger.log_trade({
                    "action": "TAKE_PROFIT_TRIGGERED",
                    "token": token[:12] + "...",
                    "reason": tp_reason,
                    "entry_price": pos.avg_entry_price,
                    "current_price": current_price,
                })
                await self.paper_account.execute_sell(
                    token=token,
                    qty=pos.qty,
                    price=current_price,
                    slippage_bps=0,
                    gas_usd=0.0005 * current_price,
                    source_wallet=pos.source_wallet,
                )

    @property
    def signals_processed(self) -> int:
        return self._signals_processed

    @property
    def signals_rejected(self) -> int:
        return self._signals_rejected
