"""
Memecoin CopyTrader - Main Entry Point & Orchestrator
======================================================
Paper Trading Bot - NO REAL TRANSACTIONS

This system operates EXCLUSIVELY in paper trading mode.
No real funds are used. No real transactions are sent.
No private keys or seed phrases are stored or required.
"""

import asyncio
import signal as sig_mod
import sys
import time
import os
from datetime import datetime, timezone
from pathlib import Path

# Ensure we're running in paper trading mode
os.environ["PAPER_TRADING_MODE"] = "1"

from modules.config_loader import load_config, Config
from modules.paper_account import PaperAccount
from modules.market_data import MarketDataService
from modules.dex_api import DexApiClient
from modules.wallet_discovery import WalletDiscoveryService
from modules.wallet_scoring import WalletScoringService
from modules.token_safety import TokenSafetyService
from modules.risk_manager import RiskManagerService
from modules.copy_engine import CopyEngineService, TradeSignal
from modules.monitoring import (
    StructuredLogger, KillSwitch, HealthChecker,
    ReportGenerator, RealTransactionBlocker
)


class MemecoinCopyTrader:
    """
    Main orchestrator for the paper trading copy bot.

    Lifecycle:
    1. Load & validate config
    2. Initialize all services
    3. Run main loop:
       a. Discover wallets (every 4h)
       b. Monitor for signals (every 2s)
       c. Process signals through copy engine
       d. Check stop-losses (every 30s)
       e. Health checks (every 30s)
       f. Reports (every 1h)
       g. Kill switch check (every 1s)
    4. Graceful shutdown
    """

    def __init__(self):
        self.config: Config = None
        self.logger: StructuredLogger = None
        self.blocker: RealTransactionBlocker = None
        self.kill_switch: KillSwitch = None
        self.paper_account: PaperAccount = None
        self.dex_api: DexApiClient = None
        self.market_data: MarketDataService = None
        self.wallet_discovery: WalletDiscoveryService = None
        self.wallet_scoring: WalletScoringService = None
        self.token_safety: TokenSafetyService = None
        self.risk_manager: RiskManagerService = None
        self.copy_engine: CopyEngineService = None
        self.health_checker: HealthChecker = None
        self.reporter: ReportGenerator = None
        self._is_shutting_down = False
        self._start_time = 0.0

    async def initialize(self):
        """Initialize all services."""
        print("=" * 60)
        print("  MEMECOIN COPYTRADER - PAPER TRADING MODE")
        print("  NO REAL TRANSACTIONS WILL BE SENT")
        print("=" * 60)

        # Load config
        try:
            self.config = load_config("config.yaml")
        except Exception as e:
            print(f"FATAL: Config validation failed: {e}", file=sys.stderr)
            sys.exit(1)

        # Verify paper trading mode (redundant but safe)
        if self.config.paper_account.mode != "paper_trading":
            print("FATAL: System only operates in paper_trading mode", file=sys.stderr)
            sys.exit(1)
        if self.config.paper_account.use_real_funds is True:
            print("FATAL: use_real_funds must be false", file=sys.stderr)
            sys.exit(1)
        if self.config.paper_account.send_real_transactions is True:
            print("FATAL: send_real_transactions must be false", file=sys.stderr)
            sys.exit(1)

        # Initialize logging
        self.logger = StructuredLogger(self.config)

        # Initialize blocker (Layer 4)
        self.blocker = RealTransactionBlocker(self.logger)

        # Initialize kill switch
        self.kill_switch = KillSwitch(self.config, self.logger)

        # Initialize paper account
        self.paper_account = PaperAccount(
            self.config, self.logger, self.blocker
        )
        await self.paper_account.initialize()

        # Initialize API client
        self.dex_api = DexApiClient(
            self.config, self.logger, self.blocker
        )
        await self.dex_api.initialize()

        # Initialize market data
        self.market_data = MarketDataService(
            self.config, self.dex_api, self.logger
        )

        # Initialize wallet services
        self.wallet_discovery = WalletDiscoveryService(
            self.config, self.market_data, self.logger
        )
        self.wallet_scoring = WalletScoringService(
            self.config, self.market_data, self.logger
        )

        # Initialize safety and risk
        self.token_safety = TokenSafetyService(
            self.config, self.market_data, self.logger
        )
        self.risk_manager = RiskManagerService(
            self.config, self.paper_account, self.logger
        )

        # Initialize copy engine
        self.copy_engine = CopyEngineService(
            self.config, self.paper_account, self.market_data,
            self.token_safety, self.risk_manager, self.wallet_scoring,
            self.logger, self.kill_switch
        )

        # Initialize reporting
        self.health_checker = HealthChecker(self.config, self.logger)
        self.reporter = ReportGenerator(self.config, self.logger)

        # Register kill switch callbacks
        self.kill_switch.on_kill(self._graceful_shutdown_callback)

        self._start_time = time.time()

        self.logger.log_state_change({
            "action": "SYSTEM_INITIALIZED",
            "paper_account": self.config.paper_account.name,
            "initial_balance": self.config.paper_account.initial_balance_usd,
            "mode": "paper_trading",
            "real_funds": False,
            "real_transactions": False,
        })

        print(f"\nAccount: {self.config.paper_account.name}")
        print(f"Balance: ${self.config.paper_account.initial_balance_usd:,.2f}")
        print(f"Mode: PAPER TRADING")
        print(f"Max Drawdown (Kill Switch): {self.config.risk.max_drawdown_pct}%")
        print(f"Test Duration: {self.config.monitoring.test_duration_hours}h")
        print(f"\nStarting main loop...\n")

    async def run(self):
        """Main event loop."""
        await self.initialize()

        # Setup signal handlers (Windows-compatible)
        try:
            loop = asyncio.get_event_loop()
            for s in (sig_mod.SIGINT, sig_mod.SIGTERM):
                loop.add_signal_handler(
                    s,
                    lambda s=s: asyncio.create_task(self._signal_shutdown(s))
                )
        except (NotImplementedError, OSError):
            # Windows: add_signal_handler not supported, use signal.signal instead
            for s in (sig_mod.SIGINT, sig_mod.SIGTERM):
                sig_mod.signal(s, lambda s, f: asyncio.get_event_loop().create_task(
                    self._signal_shutdown(s)
                ))

        # Main loop tasks
        last_discovery = 0
        last_health_check = 0
        last_report = 0
        last_stop_loss_check = 0
        last_kill_check = 0
        last_daily_reset = 0

        test_end_time = (
            self._start_time
            + self.config.monitoring.test_duration_hours * 3600
        )

        try:
            while not self._is_shutting_down:
                now = time.time()

                # Check test duration
                if now >= test_end_time:
                    self.logger.log_state_change({
                        "action": "TEST_DURATION_REACHED",
                        "hours": self.config.monitoring.test_duration_hours,
                    })
                    break

                # ---- Kill switch check (every 1s) ----
                if now - last_kill_check >= self.config.monitoring.kill_switch_check_seconds:
                    await self.kill_switch.check_kill_file()
                    if self.kill_switch.is_triggered:
                        break

                    # Check drawdown
                    prices = await self.market_data.get_multiple_prices(
                        list(self.paper_account.positions.keys())
                    )
                    pv = self.paper_account.portfolio_value(prices)
                    dd = self.paper_account.drawdown_pct(pv)
                    await self.kill_switch.check_drawdown(dd)

                    # Warning
                    if dd >= self.config.risk.warning_drawdown_pct:
                        self.logger.log_state_change({
                            "action": "DRAWDOWN_WARNING",
                            "drawdown_pct": dd,
                            "threshold": self.config.risk.warning_drawdown_pct,
                        })

                    last_kill_check = now

                # ---- Wallet discovery (every 4h) ----
                if now - last_discovery >= self.config.wallet_discovery.rebalance_interval_hours * 3600:
                    try:
                        candidates = await self.wallet_discovery.discover_wallets()
                        if candidates:
                            eligible = await self.wallet_scoring.score_wallets(candidates)
                            self.health_checker.update_counters(
                                wallets=len(eligible)
                            )
                    except Exception as e:
                        self.logger.log_error({
                            "action": "DISCOVERY_ERROR",
                            "error": str(e),
                        })
                    last_discovery = now

                # ---- Signal monitoring & processing (every 2s) ----
                if self.wallet_scoring.eligible_wallets:
                    for addr, wallet in list(self.wallet_scoring.eligible_wallets.items())[:5]:
                        try:
                            trades = await self.market_data.get_wallet_trades(
                                addr, limit=5
                            )
                            for tx in trades:
                                # Create signal from wallet trade
                                signal = TradeSignal(
                                    id=str(hash(tx.get("tx_hash", "")))[:8],
                                    token=tx.get("to", "") or tx.get("from", ""),
                                    token_name="Unknown",
                                    side="buy" if tx.get("side", "").lower() == "buy" else "sell",
                                    qty=tx.get("amount", 0),
                                    price=tx.get("usd_value", 0) / max(tx.get("amount", 1), 1),
                                    source_wallet=addr,
                                    detection_time=time.time(),
                                    signal_age_ms=(time.time() - tx.get("timestamp", time.time())) * 1000,
                                    liquidity_usd=0,
                                )

                                if signal.token and signal.qty > 0:
                                    result = await self.copy_engine.process_signal(signal)
                                    self.health_checker.update_counters(
                                        processed=1,
                                        rejected=0 if result.executed else 1,
                                    )
                        except Exception as e:
                            self.logger.log_error({
                                "action": "SIGNAL_PROCESSING_ERROR",
                                "wallet": addr[:12],
                                "error": str(e),
                            })

                # ---- Stop-loss check (every 30s) ----
                if now - last_stop_loss_check >= 30:
                    try:
                        await self.copy_engine.check_stop_losses()
                    except Exception as e:
                        self.logger.log_error({
                            "action": "STOP_LOSS_CHECK_ERROR",
                            "error": str(e),
                        })
                    last_stop_loss_check = now

                # ---- Health check (every 30s) ----
                if now - last_health_check >= self.config.monitoring.health_check_interval_seconds:
                    try:
                        prices = await self.market_data.get_multiple_prices(
                            list(self.paper_account.positions.keys())
                        )
                        pv = self.paper_account.portfolio_value(prices)
                        health = self.health_checker.get_health(
                            portfolio_value=pv,
                            balance=self.paper_account.balance,
                            open_positions=self.paper_account.open_positions_count,
                            exposure_pct=self.paper_account.total_exposure_pct(prices),
                            daily_pnl_pct=self.paper_account.daily_pnl_pct(pv),
                            drawdown_pct=self.paper_account.drawdown_pct(pv),
                            trades_today=self.paper_account.daily_trades,
                            trades_this_hour=self.paper_account.hourly_trades,
                            kill_switch_active=self.kill_switch.is_triggered,
                            kill_switch_reason=self.kill_switch.trigger_reason,
                        )
                        self.health_checker.log_health(health)
                    except Exception as e:
                        self.logger.log_error({
                            "action": "HEALTH_CHECK_ERROR",
                            "error": str(e),
                        })
                    last_health_check = now

                # ---- Report (every hour) ----
                if now - last_report >= self.config.monitoring.report_interval_hours * 3600:
                    try:
                        prices = await self.market_data.get_multiple_prices(
                            list(self.paper_account.positions.keys())
                        )
                        pv = self.paper_account.portfolio_value(prices)
                        trade_data = [
                            {
                                "timestamp": t.timestamp,
                                "token": t.token,
                                "side": t.side,
                                "qty": t.qty,
                                "price": t.price,
                                "pnl_usd": t.pnl_usd,
                                "source_wallet": t.source_wallet,
                                "slippage_bps": t.slippage_bps,
                                "actual_latency_ms": t.actual_latency_ms,
                            }
                            for t in self.paper_account.trade_history
                        ]
                        report_path = self.reporter.generate_hourly_report(
                            trade_data, pv, self.paper_account.balance,
                            self.paper_account._peak_value,
                            self.paper_account.daily_pnl_pct(pv),
                            self.paper_account.drawdown_pct(pv),
                        )
                        self.logger.log_state_change({
                            "action": "REPORT_GENERATED",
                            "path": report_path,
                        })
                    except Exception as e:
                        self.logger.log_error({
                            "action": "REPORT_ERROR",
                            "error": str(e),
                        })
                    last_report = now

                # ---- Daily reset ----
                now_dt = datetime.now()
                if (now_dt.hour == 0 and now_dt.minute == 0
                        and now - last_daily_reset > 3600):
                    self.paper_account.reset_daily()
                    self.risk_manager.reset_daily_block()
                    last_daily_reset = now

                # Small sleep to prevent CPU spinning
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            pass
        finally:
            await self._final_shutdown()

    async def _signal_shutdown(self, sig):
        """Handle SIGINT/SIGTERM."""
        self.logger.log_state_change({
            "action": "SIGNAL_RECEIVED",
            "signal": str(sig),
        })
        self._is_shutting_down = True

    async def _graceful_shutdown_callback(self):
        """Called by kill switch when triggered."""
        self._is_shutting_down = True

    async def _final_shutdown(self):
        """Final cleanup and report."""
        self.logger.log_state_change({
            "action": "SHUTDOWN_STARTED",
        })

        # Close all positions (simulated)
        for token, pos in list(self.paper_account.positions.items()):
            try:
                price = await self.market_data.get_price(token)
                if price:
                    await self.paper_account.execute_sell(
                        token=token, qty=pos.qty, price=price,
                        slippage_bps=0, gas_usd=0.0005 * price,
                    )
            except Exception:
                pass

        # Generate final report
        try:
            prices = await self.market_data.get_multiple_prices(
                list(self.paper_account.positions.keys())
            )
            pv = self.paper_account.portfolio_value(prices)
            trade_data = [
                {
                    "timestamp": t.timestamp,
                    "token": t.token,
                    "side": t.side,
                    "qty": t.qty,
                    "price": t.price,
                    "pnl_usd": t.pnl_usd,
                    "source_wallet": t.source_wallet,
                }
                for t in self.paper_account.trade_history
            ]
            self.reporter.generate_shutdown_report(
                trade_data, pv, self.paper_account.balance,
                self.paper_account._peak_value,
                self.kill_switch.trigger_reason,
            )
        except Exception:
            pass

        # Save state
        await self.paper_account.save_state()
        await self.paper_account.close()
        await self.dex_api.close()

        uptime = time.time() - self._start_time
        self.logger.log_state_change({
            "action": "SHUTDOWN_COMPLETE",
            "uptime_seconds": uptime,
            "total_trades": len(self.paper_account.trade_history),
            "final_balance": self.paper_account.balance,
            "kill_switch_reason": self.kill_switch.trigger_reason,
        })

        print(f"\n{'='*60}")
        print(f"  SHUTDOWN COMPLETE")
        print(f"  Uptime: {uptime/3600:.1f}h")
        print(f"  Final Balance: ${self.paper_account.balance:,.2f}")
        print(f"  Total Trades: {len(self.paper_account.trade_history)}")
        print(f"{'='*60}\n")


async def main():
    """Entry point."""
    trader = MemecoinCopyTrader()
    await trader.run()


if __name__ == "__main__":
    asyncio.run(main())
