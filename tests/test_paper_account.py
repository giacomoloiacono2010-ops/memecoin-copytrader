"""Tests for paper_account module."""

import pytest
import asyncio
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.paper_account import PaperAccount, Position, Trade
from modules.monitoring import StructuredLogger, RealTransactionBlocker


class MockConfig:
    """Minimal config for testing."""
    class paper_account:
        name = "TEST"
        initial_balance_usd = 10000.0
        mode = "paper_trading"
        use_real_funds = False
        send_real_transactions = False
    class risk:
        max_risk_per_trade_pct = 0.5
        max_total_exposure_pct = 20.0
        max_open_positions = 5
        max_daily_loss_pct = 3.0
        max_drawdown_pct = 10.0
    class execution:
        delay_ms = 200
    class output:
        data_dir = "test_data"


@pytest.fixture
def paper_account(tmp_path):
    """Create a paper account for testing."""
    import modules.paper_account as pa_mod
    config = MockConfig()
    config.output.data_dir = str(tmp_path / "data")

    # Create a mock logger
    logger = MagicMock(spec=StructuredLogger)
    blocker = MagicMock(spec=RealTransactionBlocker)

    account = PaperAccount(config, logger, blocker)
    return account


@pytest.mark.asyncio
async def test_initial_balance(paper_account):
    """Test initial balance is set correctly."""
    await paper_account.initialize()
    assert paper_account.balance == 10000.0


@pytest.mark.asyncio
async def test_buy_reduces_balance(paper_account):
    """Test that buying reduces balance."""
    await paper_account.initialize()

    trade = await paper_account.execute_buy(
        token="test_token",
        token_name="TEST",
        qty=100,
        price=10.0,
        slippage_bps=50,
        gas_usd=0.05,
        source_wallet="wallet123",
    )

    assert trade.side == "buy"
    assert trade.risk_decision == "APPROVED"
    assert paper_account.balance < 10000.0


@pytest.mark.asyncio
async def test_buy_creates_position(paper_account):
    """Test that buying creates a position."""
    await paper_account.initialize()

    await paper_account.execute_buy(
        token="test_token",
        token_name="TEST",
        qty=100,
        price=10.0,
        slippage_bps=50,
        gas_usd=0.05,
        source_wallet="wallet123",
    )

    assert "test_token" in paper_account.positions
    pos = paper_account.positions["test_token"]
    assert pos.qty == 100
    assert pos.avg_entry_price == 10.0


@pytest.mark.asyncio
async def test_sell_increases_balance(paper_account):
    """Test that selling increases balance."""
    await paper_account.initialize()

    # Buy first
    await paper_account.execute_buy(
        token="test_token", token_name="TEST",
        qty=100, price=10.0, slippage_bps=50,
        gas_usd=0.05, source_wallet="wallet123",
    )
    balance_after_buy = paper_account.balance

    # Sell
    trade = await paper_account.execute_sell(
        token="test_token", qty=50, price=12.0,
        slippage_bps=50, gas_usd=0.05,
    )

    assert trade.side == "sell"
    assert trade.pnl_usd > 0  # Should be profitable
    assert paper_account.balance > balance_after_buy


@pytest.mark.asyncio
async def test_sell_nonexistent_position(paper_account):
    """Test selling a position that doesn't exist."""
    await paper_account.initialize()

    trade = await paper_account.execute_sell(
        token="nonexistent", qty=100, price=10.0,
        slippage_bps=50, gas_usd=0.05,
    )

    assert trade.risk_decision == "REJECTED"
    assert trade.rejection_reason == "NO_POSITION"


@pytest.mark.asyncio
async def test_insufficient_funds(paper_account):
    """Test buying with insufficient funds."""
    await paper_account.initialize()

    trade = await paper_account.execute_buy(
        token="expensive_token", token_name="EXP",
        qty=10000, price=10.0, slippage_bps=50,
        gas_usd=0.05, source_wallet="wallet123",
    )

    assert trade.risk_decision == "REJECTED"
    assert trade.rejection_reason == "INSUFFICIENT_FUNDS"


@pytest.mark.asyncio
async def test_position_tracking(paper_account):
    """Test that positions are tracked correctly."""
    await paper_account.initialize()

    # Buy multiple tokens
    await paper_account.execute_buy(
        token="token_a", token_name="A",
        qty=100, price=10.0, slippage_bps=50,
        gas_usd=0.05, source_wallet="wallet1",
    )
    await paper_account.execute_buy(
        token="token_b", token_name="B",
        qty=50, price=20.0, slippage_bps=50,
        gas_usd=0.05, source_wallet="wallet2",
    )

    assert paper_account.open_positions_count == 2
    assert "token_a" in paper_account.positions
    assert "token_b" in paper_account.positions


@pytest.mark.asyncio
async def test_no_real_transactions(paper_account):
    """CRITICAL: Verify no real transactions are ever sent."""
    await paper_account.initialize()

    # The PaperAccount class has no send_transaction method
    assert not hasattr(paper_account, 'send_transaction')
    assert not hasattr(paper_account, 'sign_transaction')
    assert not hasattr(paper_account, 'submit_transaction')

    # All operations are purely in-memory/SQLite
    trade = await paper_account.execute_buy(
        token="test", token_name="T",
        qty=10, price=100.0, slippage_bps=10,
        gas_usd=0.01, source_wallet="w1",
    )
    assert trade.risk_decision == "APPROVED"
    # But nothing was sent anywhere


def test_position_properties():
    """Test Position dataclass properties."""
    pos = Position(
        token="test", token_name="TEST",
        qty=100, avg_entry_price=10.0,
        current_price=12.0, entry_time="2024-01-01",
        source_wallet="wallet123",
    )

    assert pos.market_value == 1200.0
    assert pos.cost_basis == 1000.0
    assert pos.unrealized_pnl == 200.0
    assert pos.unrealized_pnl_pct == 20.0
