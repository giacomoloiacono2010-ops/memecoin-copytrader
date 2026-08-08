"""
Module 2: Paper Account Service
================================
Manages the virtual portfolio: balance, positions, trade history.
All operations are simulated. NO real transactions are ever sent.
Persistence via SQLite.
"""

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from .config_loader import Config
from .monitoring import StructuredLogger, RealTransactionBlocker


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class Position:
    token: str
    token_name: str
    qty: float
    avg_entry_price: float
    current_price: float
    entry_time: str
    source_wallet: str
    highest_price: float = 0.0  # For trailing take-profit

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.qty * self.avg_entry_price

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100


@dataclass
class Trade:
    id: str
    timestamp: str
    token: str
    token_name: str
    side: str  # "buy" | "sell"
    qty: float
    price: float
    slippage_bps: float
    gas_usd: float
    source_wallet: str
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    signal_detection_time: float = 0.0
    signal_execution_time: float = 0.0
    actual_latency_ms: float = 0.0
    risk_decision: str = "APPROVED"
    rejection_reason: Optional[str] = None


@dataclass
class PortfolioSnapshot:
    timestamp: str
    balance_usd: float
    portfolio_value: float
    positions_count: int
    total_exposure_pct: float
    daily_pnl_pct: float
    drawdown_pct: float
    peak_value: float


# ============================================================
# PAPER ACCOUNT SERVICE
# ============================================================

class PaperAccount:
    """
    Virtual paper trading account.
    Manages balance, positions, and trade history.
    Persists state to SQLite.
    """

    def __init__(self, config: Config, logger: StructuredLogger,
                 blocker: RealTransactionBlocker):
        self.config = config
        self.logger = logger
        self.blocker = blocker
        self._balance = config.paper_account.initial_balance_usd
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []
        self._peak_value = config.paper_account.initial_balance_usd
        self._daily_pnl_start = config.paper_account.initial_balance_usd
        self._daily_trades = 0
        self._hourly_trades = 0
        self._hour_start = time.time()
        self._db_path = Path(config.output.data_dir if hasattr(config, 'output') else "data")
        self._db_path.mkdir(exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """Open database and load/create state."""
        self._db = await aiosqlite.connect(self._db_path / "portfolio.db")
        await self._create_tables()
        await self._load_state()

        self.logger.log_state_change({
            "action": "PAPER_ACCOUNT_INITIALIZED",
            "name": self.config.paper_account.name,
            "balance": self._balance,
            "positions": len(self._positions),
            "mode": "paper_trading",
        })

    async def _create_tables(self):
        """Create database tables if they don't exist."""
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                token TEXT PRIMARY KEY,
                token_name TEXT,
                qty REAL,
                avg_entry_price REAL,
                current_price REAL,
                entry_time TEXT,
                source_wallet TEXT,
                highest_price REAL DEFAULT 0.0
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                token TEXT,
                token_name TEXT,
                side TEXT,
                qty REAL,
                price REAL,
                slippage_bps REAL,
                gas_usd REAL,
                source_wallet TEXT,
                pnl_usd REAL DEFAULT 0.0,
                pnl_pct REAL DEFAULT 0.0,
                signal_detection_time REAL DEFAULT 0.0,
                signal_execution_time REAL DEFAULT 0.0,
                actual_latency_ms REAL DEFAULT 0.0,
                risk_decision TEXT DEFAULT 'APPROVED',
                rejection_reason TEXT
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS account_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await self._db.commit()

    async def _load_state(self):
        """Load state from database."""
        # Load balance
        async with self._db.execute(
            "SELECT value FROM account_state WHERE key = 'balance'"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                self._balance = float(row[0])

        # Load peak value
        async with self._db.execute(
            "SELECT value FROM account_state WHERE key = 'peak_value'"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                self._peak_value = float(row[0])

        # Load positions
        async with self._db.execute("SELECT * FROM positions") as cursor:
            async for row in cursor:
                pos = Position(
                    token=row[0], token_name=row[1], qty=row[2],
                    avg_entry_price=row[3], current_price=row[4],
                    entry_time=row[5], source_wallet=row[6],
                    highest_price=row[7] if row[7] else 0.0
                )
                self._positions[pos.token] = pos

        # Load recent trades (last 1000)
        async with self._db.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 1000"
        ) as cursor:
            async for row in cursor:
                trade = Trade(
                    id=row[0], timestamp=row[1], token=row[2],
                    token_name=row[3], side=row[4], qty=row[5],
                    price=row[6], slippage_bps=row[7], gas_usd=row[8],
                    source_wallet=row[9], pnl_usd=row[10],
                    pnl_pct=row[11], signal_detection_time=row[12],
                    signal_execution_time=row[13],
                    actual_latency_ms=row[14], risk_decision=row[15],
                    rejection_reason=row[16],
                )
                self._trades.append(trade)

    async def save_state(self):
        """Persist current state to database."""
        if not self._db:
            return

        # Save balance and peak
        await self._db.execute(
            "INSERT OR REPLACE INTO account_state (key, value) VALUES (?, ?)",
            ("balance", str(self._balance))
        )
        await self._db.execute(
            "INSERT OR REPLACE INTO account_state (key, value) VALUES (?, ?)",
            ("peak_value", str(self._peak_value))
        )

        # Save positions
        await self._db.execute("DELETE FROM positions")
        for pos in self._positions.values():
            await self._db.execute(
                "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pos.token, pos.token_name, pos.qty, pos.avg_entry_price,
                 pos.current_price, pos.entry_time, pos.source_wallet,
                 pos.highest_price)
            )

        await self._db.commit()

    # ---- TRADE EXECUTION (PAPER ONLY) ----

    async def execute_buy(self, token: str, token_name: str, qty: float,
                          price: float, slippage_bps: float, gas_usd: float,
                          source_wallet: str,
                          signal_detection_time: float = 0.0) -> Trade:
        """
        Execute a paper buy order.
        CRITICAL: This NEVER sends a real transaction.
        """
        # LAYER 3: Paper account is isolated - no send_transaction method exists
        trade_id = str(uuid.uuid4())[:8]
        execution_time = time.time()
        latency_ms = (execution_time - signal_detection_time) * 1000 if signal_detection_time > 0 else self.config.execution.delay_ms

        total_cost = qty * price + gas_usd

        if total_cost > self._balance:
            # Log insufficient funds
            trade = Trade(
                id=trade_id, timestamp=datetime.now(timezone.utc).isoformat(),
                token=token, token_name=token_name, side="buy",
                qty=qty, price=price, slippage_bps=slippage_bps,
                gas_usd=gas_usd, source_wallet=source_wallet,
                risk_decision="REJECTED",
                rejection_reason="INSUFFICIENT_FUNDS",
                signal_detection_time=signal_detection_time,
                signal_execution_time=execution_time,
                actual_latency_ms=latency_ms,
            )
            self.logger.log_trade({
                "action": "BUY_REJECTED", "reason": "INSUFFICIENT_FUNDS",
                "trade_id": trade_id, "token": token,
                "total_cost": total_cost, "balance": self._balance,
            })
            return trade

        # Deduct balance
        self._balance -= total_cost

        # Update or create position
        if token in self._positions:
            pos = self._positions[token]
            total_qty = pos.qty + qty
            total_cost_basis = (pos.avg_entry_price * pos.qty) + (price * qty)
            pos.avg_entry_price = total_cost_basis / total_qty
            pos.qty = total_qty
            pos.current_price = price
        else:
            self._positions[token] = Position(
                token=token, token_name=token_name, qty=qty,
                avg_entry_price=price, current_price=price,
                entry_time=datetime.now(timezone.utc).isoformat(),
                source_wallet=source_wallet, highest_price=price,
            )

        # Create trade record
        trade = Trade(
            id=trade_id, timestamp=datetime.now(timezone.utc).isoformat(),
            token=token, token_name=token_name, side="buy",
            qty=qty, price=price, slippage_bps=slippage_bps,
            gas_usd=gas_usd, source_wallet=source_wallet,
            signal_detection_time=signal_detection_time,
            signal_execution_time=execution_time,
            actual_latency_ms=latency_ms,
            risk_decision="APPROVED",
        )
        self._trades.append(trade)
        self._daily_trades += 1
        self._hourly_trades += 1

        # Persist
        await self._db.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade.id, trade.timestamp, trade.token, trade.token_name,
             trade.side, trade.qty, trade.price, trade.slippage_bps,
             trade.gas_usd, trade.source_wallet, trade.pnl_usd,
             trade.pnl_pct, trade.signal_detection_time,
             trade.signal_execution_time, trade.actual_latency_ms,
             trade.risk_decision, trade.rejection_reason)
        )
        await self.save_state()

        self.logger.log_trade({
            "action": "BUY_EXECUTED", "trade_id": trade_id,
            "token": token, "qty": qty, "price": price,
            "slippage_bps": slippage_bps, "gas_usd": gas_usd,
            "latency_ms": latency_ms, "source_wallet": source_wallet,
            "balance_after": self._balance,
        })

        return trade

    async def execute_sell(self, token: str, qty: float, price: float,
                           slippage_bps: float, gas_usd: float,
                           source_wallet: str = "",
                           signal_detection_time: float = 0.0) -> Trade:
        """
        Execute a paper sell order.
        CRITICAL: This NEVER sends a real transaction.
        """
        trade_id = str(uuid.uuid4())[:8]
        execution_time = time.time()
        latency_ms = (execution_time - signal_detection_time) * 1000 if signal_detection_time > 0 else self.config.execution.delay_ms

        if token not in self._positions:
            trade = Trade(
                id=trade_id, timestamp=datetime.now(timezone.utc).isoformat(),
                token=token, token_name=token, side="sell",
                qty=qty, price=price, slippage_bps=slippage_bps,
                gas_usd=gas_usd, source_wallet=source_wallet,
                risk_decision="REJECTED",
                rejection_reason="NO_POSITION",
                signal_detection_time=signal_detection_time,
                signal_execution_time=execution_time,
                actual_latency_ms=latency_ms,
            )
            self.logger.log_trade({
                "action": "SELL_REJECTED", "reason": "NO_POSITION",
                "trade_id": trade_id, "token": token,
            })
            return trade

        pos = self._positions[token]
        sell_qty = min(qty, pos.qty)
        proceeds = sell_qty * price - gas_usd

        # Calculate PnL
        pnl_usd = (price - pos.avg_entry_price) * sell_qty - gas_usd
        pnl_pct = ((price - pos.avg_entry_price) / pos.avg_entry_price * 100
                   ) if pos.avg_entry_price > 0 else 0

        # Add proceeds to balance
        self._balance += proceeds

        # Update position
        pos.qty -= sell_qty
        pos.current_price = price
        if pos.qty <= 1e-10:  # Dust threshold
            del self._positions[token]

        # Create trade record
        trade = Trade(
            id=trade_id, timestamp=datetime.now(timezone.utc).isoformat(),
            token=token, token_name=token, side="sell",
            qty=sell_qty, price=price, slippage_bps=slippage_bps,
            gas_usd=gas_usd, source_wallet=source_wallet or
            (pos.source_wallet if token in self._positions else ""),
            pnl_usd=pnl_usd, pnl_pct=pnl_pct,
            signal_detection_time=signal_detection_time,
            signal_execution_time=execution_time,
            actual_latency_ms=latency_ms,
            risk_decision="APPROVED",
        )
        self._trades.append(trade)
        self._daily_trades += 1
        self._hourly_trades += 1

        # Persist
        await self._db.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade.id, trade.timestamp, trade.token, trade.token_name,
             trade.side, trade.qty, trade.price, trade.slippage_bps,
             trade.gas_usd, trade.source_wallet, trade.pnl_usd,
             trade.pnl_pct, trade.signal_detection_time,
             trade.signal_execution_time, trade.actual_latency_ms,
             trade.risk_decision, trade.rejection_reason)
        )
        await self.save_state()

        self.logger.log_trade({
            "action": "SELL_EXECUTED", "trade_id": trade_id,
            "token": token, "qty": sell_qty, "price": price,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
            "latency_ms": latency_ms, "balance_after": self._balance,
        })

        return trade

    # ---- QUERIES ----

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions.copy()

    @property
    def open_positions_count(self) -> int:
        return len(self._positions)

    @property
    def trade_history(self) -> list[Trade]:
        return self._trades.copy()

    @property
    def daily_trades(self) -> int:
        return self._daily_trades

    @property
    def hourly_trades(self) -> int:
        # Reset hourly counter if hour has passed
        if time.time() - self._hour_start > 3600:
            self._hourly_trades = 0
            self._hour_start = time.time()
        return self._hourly_trades

    def portfolio_value(self, prices: dict[str, float]) -> float:
        """Calculate total portfolio value = balance + positions market value."""
        positions_value = sum(
            pos.qty * prices.get(pos.token, pos.current_price)
            for pos in self._positions.values()
        )
        return self._balance + positions_value

    def total_exposure_pct(self, prices: dict[str, float]) -> float:
        """Calculate total exposure as percentage of portfolio."""
        pv = self.portfolio_value(prices)
        if pv == 0:
            return 0.0
        positions_value = sum(
            pos.qty * prices.get(pos.token, pos.current_price)
            for pos in self._positions.values()
        )
        return (positions_value / pv) * 100

    def daily_pnl_pct(self, current_value: float) -> float:
        """Daily P&L percentage."""
        if self._daily_pnl_start == 0:
            return 0.0
        return ((current_value - self._daily_pnl_start)
                / self._daily_pnl_start * 100)

    def drawdown_pct(self, current_value: float) -> float:
        """Current drawdown from peak."""
        if current_value > self._peak_value:
            self._peak_value = current_value
        if self._peak_value == 0:
            return 0.0
        return ((self._peak_value - current_value) / self._peak_value * 100)

    def reset_daily(self):
        """Reset daily counters (called at midnight)."""
        self._daily_pnl_start = self._balance
        self._daily_trades = 0

    async def close(self):
        """Close database connection."""
        if self._db:
            await self.save_state()
            await self._db.close()
