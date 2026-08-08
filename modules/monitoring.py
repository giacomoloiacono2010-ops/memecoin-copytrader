"""
Module 10: Monitoring, Logging & Kill Switch
=============================================
Provides structured logging, kill switch mechanisms, health checks,
and report generation. Every event in the system flows through here.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

from .config_loader import Config


# ============================================================
# STRUCTURED LOGGER
# ============================================================

class StructuredLogger:
    """
    JSON-lines structured logger. Writes to separate files per event type.
    Every module imports this and logs through it.
    """

    def __init__(self, config: Config):
        self.config = config
        self.logs_dir = Path(config.monitoring.log_level and "logs")
        self.logs_dir.mkdir(exist_ok=True)

        self._loggers: dict[str, logging.Logger] = {}
        self._init_loggers()

    def _init_loggers(self):
        """Initialize separate loggers for each event type."""
        event_types = ["trades", "signals", "errors", "state_changes",
                       "risk_decisions", "wallet_analysis", "health"]

        for event_type in event_types:
            logger = logging.getLogger(f"copytrader.{event_type}")
            logger.setLevel(logging.DEBUG)

            # File handler - JSON lines
            fh = logging.FileHandler(
                self.logs_dir / f"{event_type}.jsonl",
                encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)

            # Console handler - only for errors and state changes
            if event_type in ("errors", "state_changes"):
                ch = logging.StreamHandler(sys.stdout)
                ch.setLevel(getattr(logging, self.config.monitoring.log_level))
                ch.setFormatter(logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(message)s"
                ))
                logger.addHandler(ch)

            fh.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(fh)
            self._loggers[event_type] = logger

    def _log_event(self, event_type: str, event_data: dict):
        """Write a structured JSON event."""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **event_data
        }
        log_line = json.dumps(event, default=str)
        self._loggers[event_type].info(log_line)

    def log_trade(self, trade_data: dict):
        """Log a trade execution (simulated)."""
        self._log_event("trades", trade_data)

    def log_signal(self, signal_data: dict):
        """Log a detected signal."""
        self._log_event("signals", signal_data)

    def log_error(self, error_data: dict):
        """Log an error."""
        self._log_event("errors", error_data)

    def log_critical_error(self, error_data: dict):
        """Log a CRITICAL error (real transaction attempt, etc.)."""
        error_data["severity"] = "CRITICAL"
        self._log_event("errors", error_data)
        # Also print to stderr
        print(f"CRITICAL_ERROR: {json.dumps(error_data)}", file=sys.stderr)

    def log_state_change(self, state_data: dict):
        """Log a state change."""
        self._log_event("state_changes", state_data)

    def log_risk_decision(self, risk_data: dict):
        """Log a risk management decision."""
        self._log_event("risk_decisions", risk_data)

    def log_wallet_analysis(self, wallet_data: dict):
        """Log wallet analysis results."""
        self._log_event("wallet_analysis", wallet_data)

    def log_health(self, health_data: dict):
        """Log health check."""
        self._log_event("health", health_data)


# ============================================================
# KILL SWITCH
# ============================================================

class KillSwitch:
    """
    Kill switch with multiple activation methods:
    1. Manual: create KILL file in project root
    2. Automatic: drawdown > max_drawdown_pct
    3. Remote: python kill.py
    4. Signal: SIGINT/SIGTERM
    """

    def __init__(self, config: Config, logger: StructuredLogger):
        self.config = config
        self.logger = logger
        self._is_triggered = False
        self._trigger_reason: Optional[str] = None
        self._trigger_time: Optional[float] = None
        self._callbacks: list = []

    @property
    def is_triggered(self) -> bool:
        return self._is_triggered

    @property
    def trigger_reason(self) -> Optional[str]:
        return self._trigger_reason

    def on_kill(self, callback):
        """Register a callback to run when kill switch triggers."""
        self._callbacks.append(callback)

    async def check_kill_file(self) -> bool:
        """Check if KILL file exists. Called every cycle."""
        kill_file = Path("KILL")
        if kill_file.exists():
            await self.trigger("MANUAL_KILL_FILE")
            return True
        return False

    async def trigger(self, reason: str, extra: Optional[dict] = None):
        """Trigger the kill switch."""
        if self._is_triggered:
            return  # Already triggered

        self._is_triggered = True
        self._trigger_reason = reason
        self._trigger_time = time.time()

        event = {
            "action": "KILL_SWITCH_TRIGGERED",
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            event["details"] = extra

        self.logger.log_state_change(event)
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  KILL SWITCH TRIGGERED: {reason}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        # Run all registered callbacks
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                self.logger.log_error({
                    "action": "KILL_SWITCH_CALLBACK_ERROR",
                    "error": str(e),
                    "callback": cb.__name__,
                })

    def reset(self):
        """Reset kill switch (for testing only)."""
        self._is_triggered = False
        self._trigger_reason = None
        self._trigger_time = None


# ============================================================
# HEALTH CHECKER
# ============================================================

@dataclass
class HealthState:
    """Current system health snapshot."""
    timestamp: str
    uptime_seconds: float
    portfolio_value_usd: float
    balance_usd: float
    open_positions: int
    total_exposure_pct: float
    daily_pnl_pct: float
    drawdown_pct: float
    trades_today: int
    trades_this_hour: int
    kill_switch_active: bool
    kill_switch_reason: Optional[str]
    wallets_tracked: int
    signals_processed: int
    signals_rejected: int


class HealthChecker:
    """Periodic health check and reporting."""

    def __init__(self, config: Config, logger: StructuredLogger):
        self.config = config
        self.logger = logger
        self._start_time = time.time()
        self._signals_processed = 0
        self._signals_rejected = 0
        self._wallets_tracked = 0

    def update_counters(self, processed: int = 0, rejected: int = 0,
                        wallets: Optional[int] = None):
        self._signals_processed += processed
        self._signals_rejected += rejected
        if wallets is not None:
            self._wallets_tracked = wallets

    def get_health(self, portfolio_value: float, balance: float,
                   open_positions: int, exposure_pct: float,
                   daily_pnl_pct: float, drawdown_pct: float,
                   trades_today: int, trades_this_hour: int,
                   kill_switch_active: bool,
                   kill_switch_reason: Optional[str]) -> HealthState:
        return HealthState(
            timestamp=datetime.now(timezone.utc).isoformat(),
            uptime_seconds=time.time() - self._start_time,
            portfolio_value_usd=portfolio_value,
            balance_usd=balance,
            open_positions=open_positions,
            total_exposure_pct=exposure_pct,
            daily_pnl_pct=daily_pnl_pct,
            drawdown_pct=drawdown_pct,
            trades_today=trades_today,
            trades_this_hour=trades_this_hour,
            kill_switch_active=kill_switch_active,
            kill_switch_reason=kill_switch_reason,
            wallets_tracked=self._wallets_tracked,
            signals_processed=self._signals_processed,
            signals_rejected=self._signals_rejected,
        )

    def log_health(self, health: HealthState):
        """Log health state."""
        self.logger.log_health(asdict(health))


# ============================================================
# REPORT GENERATOR
# ============================================================

class ReportGenerator:
    """Generates performance reports in HTML and CSV."""

    def __init__(self, config: Config, logger: StructuredLogger):
        self.config = config
        self.logger = logger
        self.reports_dir = Path(config.output.reports_dir if hasattr(config, 'output') else "reports")
        self.reports_dir.mkdir(exist_ok=True)

    def generate_hourly_report(self, trade_history: list[dict],
                               portfolio_value: float, balance: float,
                               peak_value: float, daily_pnl_pct: float,
                               drawdown_pct: float) -> str:
        """Generate hourly performance report. Returns file path."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self.reports_dir / f"report_{timestamp}.html"

        # Calculate metrics
        total_trades = len(trade_history)
        winning = sum(1 for t in trade_history if t.get("pnl_usd", 0) > 0)
        losing = sum(1 for t in trade_history if t.get("pnl_usd", 0) < 0)
        win_rate = (winning / total_trades * 100) if total_trades > 0 else 0

        total_pnl = sum(t.get("pnl_usd", 0) for t in trade_history)
        avg_latency = (sum(t.get("actual_latency_ms", 0) for t in trade_history)
                       / total_trades) if total_trades > 0 else 0
        avg_slippage = (sum(t.get("slippage_bps", 0) for t in trade_history)
                        / total_trades) if total_trades > 0 else 0

        roi = ((portfolio_value - self.config.paper_account.initial_balance_usd)
               / self.config.paper_account.initial_balance_usd * 100)

        html = f"""<!DOCTYPE html>
<html>
<head><title>CopyTrader Report - {timestamp}</title>
<style>
body {{ font-family: monospace; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #00ff88; }}
.metric {{ background: #16213e; padding: 10px; margin: 5px 0; border-left: 3px solid #00ff88; }}
.metric.warn {{ border-left-color: #ffaa00; }}
.metric.danger {{ border-left-color: #ff4444; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #333; }}
th {{ color: #00ff88; }}
</style>
</head>
<body>
<h1>Memecoin CopyTrader - Performance Report</h1>
<p>Generated: {datetime.now().isoformat()} | Mode: PAPER TRADING</p>

<h2>Portfolio Summary</h2>
<div class="metric">Account: {self.config.paper_account.name}</div>
<div class="metric">Initial Balance: ${self.config.paper_account.initial_balance_usd:,.2f}</div>
<div class="metric">Current Balance: ${balance:,.2f}</div>
<div class="metric">Portfolio Value: ${portfolio_value:,.2f}</div>
<div class="metric {'warn' if roi < 0 else ''}">ROI: {roi:+.2f}%</div>
<div class="metric {'danger' if drawdown_pct > 5 else 'warn' if drawdown_pct > 2 else ''}">Max Drawdown: {drawdown_pct:.2f}%</div>
<div class="metric">Peak Value: ${peak_value:,.2f}</div>

<h2>Trade Statistics</h2>
<div class="metric">Total Trades: {total_trades}</div>
<div class="metric">Winning: {winning} | Losing: {losing}</div>
<div class="metric">Win Rate: {win_rate:.1f}%</div>
<div class="metric">Total PnL: ${total_pnl:+,.2f}</div>
<div class="metric">Avg Latency: {avg_latency:.1f}ms (target: {self.config.execution.delay_ms}ms)</div>
<div class="metric">Avg Slippage: {avg_slippage:.1f}bps</div>
<div class="metric">Daily PnL: {daily_pnl_pct:+.2f}%</div>

<h2>Risk Parameters</h2>
<div class="metric">Max Risk/Trade: {self.config.risk.max_risk_per_trade_pct}%</div>
<div class="metric">Max Exposure: {self.config.risk.max_total_exposure_pct}%</div>

<h2>Recent Trades (last 20)</h2>
<table>
<tr><th>Time</th><th>Token</th><th>Side</th><th>Qty</th><th>Price</th><th>PnL</th><th>Source</th></tr>
"""
        for t in trade_history[-20:]:
            pnl_class = 'style="color:#00ff88"' if t.get("pnl_usd", 0) >= 0 else 'style="color:#ff4444"'
            html += f"""<tr>
<td>{t.get('timestamp', 'N/A')}</td>
<td>{t.get('token', 'N/A')}</td>
<td>{t.get('side', 'N/A')}</td>
<td>{t.get('qty', 0):.4f}</td>
<td>${t.get('price', 0):.6f}</td>
<td {pnl_class}>${t.get('pnl_usd', 0):+.4f}</td>
<td>{t.get('source_wallet', 'N/A')[:8]}...</td>
</tr>
"""

        html += """</table>
<p><em>This report is for paper trading analysis only. Not financial advice.</em></p>
</body></html>"""

        with open(report_path, "w") as f:
            f.write(html)

        self.logger.log_state_change({
            "action": "REPORT_GENERATED",
            "path": str(report_path),
            "trades_count": total_trades,
            "roi_pct": roi,
        })

        return str(report_path)

    def generate_shutdown_report(self, trade_history: list[dict],
                                 portfolio_value: float, balance: float,
                                 peak_value: float,
                                 kill_reason: Optional[str]) -> str:
        """Generate final shutdown report."""
        return self.generate_hourly_report(
            trade_history, portfolio_value, balance, peak_value,
            daily_pnl_pct=0.0,  # Final report
            drawdown_pct=((peak_value - portfolio_value) / peak_value * 100)
            if peak_value > 0 else 0
        )


# ============================================================
# REAL TRANSACTION BLOCKER
# ============================================================

class RealTransactionBlocker:
    """
    LAYER 4 of defense: Intercepts and blocks any attempt to send
    real transactions. Logs as CRITICAL_ERROR.
    """

    def __init__(self, logger: StructuredLogger):
        self.logger = logger
        self._blocked_count = 0

    def block(self, attempted_method: str, target: str,
              details: Optional[dict] = None):
        """Block a real transaction attempt and log CRITICAL_ERROR."""
        self._blocked_count += 1
        event = {
            "action": "CRITICAL_ERROR",
            "type": "REAL_TRANSACTION_BLOCKED",
            "attempted_method": attempted_method,
            "target": target,
            "block_number": self._blocked_count,
        }
        if details:
            event["details"] = details
        self.logger.log_critical_error(event)

    @property
    def blocked_count(self) -> int:
        return self._blocked_count
