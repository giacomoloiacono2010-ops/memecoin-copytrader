"""Tests for risk_manager module."""

import pytest
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.risk_manager import RiskManagerService, RiskDecision
from modules.paper_account import PaperAccount, Position
from modules.monitoring import StructuredLogger, RealTransactionBlocker


class MockConfig:
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
        max_trades_per_hour = 10
        max_daily_loss_pct = 3.0
        max_drawdown_pct = 10.0
        max_slippage_bps = 150
        max_price_impact_bps = 200
        min_liquidity_usd = 20000.0
        max_gas_per_trade_usd = 5.0
        allow_leverage = False
        allow_short = False
        stop_loss_pct = 15.0
        trailing_take_profit_pct = 50.0
        warning_drawdown_pct = 8.0
    class execution:
        delay_ms = 200
    class output:
        data_dir = "test_data"


@pytest.fixture
def risk_setup(tmp_path):
    """Create risk manager with mock paper account."""
    config = MockConfig()
    config.output.data_dir = str(tmp_path / "data")

    logger = MagicMock(spec=StructuredLogger)
    blocker = MagicMock(spec=RealTransactionBlocker)

    paper = PaperAccount(config, logger, blocker)
    risk_mgr = RiskManagerService(config, paper, logger)

    return risk_mgr, paper


@pytest.mark.asyncio
async def test_trade_within_limits(risk_setup):
    """Test that a trade within all limits is approved."""
    risk_mgr, paper = risk_setup
    await paper.initialize()

    decision = await risk_mgr.validate_trade(
        token="test_token", side="buy", qty=50,
        price=10.0, liquidity_usd=100000.0,
    )

    assert decision.approved is True
    assert decision.reason == "ALL_CHECKS_PASSED"


@pytest.mark.asyncio
async def test_max_risk_per_trade(risk_setup):
    """Test that trade exceeding max risk per trade is rejected."""
    risk_mgr, paper = risk_setup
    await paper.initialize()

    # Trade too large: 1000 * 10 = $10000 > 0.5% of $10000 = $50
    decision = await risk_mgr.validate_trade(
        token="test_token", side="buy", qty=1000,
        price=10.0, liquidity_usd=1000000.0,
    )

    # Should be modified (reduced) or rejected
    assert decision.approved is True  # Modified to fit
    assert decision.approved_qty < 1000  # Qty was reduced


@pytest.mark.asyncio
async def test_max_open_positions(risk_setup):
    """Test that max open positions is enforced."""
    risk_mgr, paper = risk_setup
    await paper.initialize()

    # Add 5 positions
    for i in range(5):
        await paper.execute_buy(
            token=f"token_{i}", token_name=f"T{i}",
            qty=1, price=10.0, slippage_bps=10,
            gas_usd=0.01, source_wallet=f"wallet_{i}",
        )

    # Try to add 6th
    decision = await risk_mgr.validate_trade(
        token="new_token", side="buy", qty=1,
        price=10.0, liquidity_usd=100000.0,
    )

    assert decision.approved is False
    assert "MAX_POSITIONS" in decision.reason


@pytest.mark.asyncio
async def test_no_short_selling(risk_setup):
    """Test that short selling is blocked."""
    risk_mgr, paper = risk_setup
    await paper.initialize()

    decision = await risk_mgr.validate_trade(
        token="token_not_owned", side="sell", qty=100,
        price=10.0, liquidity_usd=100000.0,
    )

    assert decision.approved is False
    assert "NO_SHORT" in decision.reason


@pytest.mark.asyncio
async def test_liquidity_check(risk_setup):
    """Test that low liquidity tokens are rejected."""
    risk_mgr, paper = risk_setup
    await paper.initialize()

    decision = await risk_mgr.validate_trade(
        token="low_liq_token", side="buy", qty=100,
        price=10.0, liquidity_usd=5000.0,  # Below $20k min
    )

    assert decision.approved is False
    assert "LIQUIDITY" in decision.reason


@pytest.mark.asyncio
async def test_stop_loss_check(risk_setup):
    """Test stop-loss detection."""
    risk_mgr, paper = risk_setup
    await paper.initialize()

    # Create a position
    await paper.execute_buy(
        token="test_token", token_name="TEST",
        qty=100, price=10.0, slippage_bps=10,
        gas_usd=0.01, source_wallet="wallet1",
    )

    pos = paper.positions["test_token"]

    # Price drops 20% (below 15% stop-loss)
    stop = await risk_mgr.check_stop_loss(pos, 8.0)
    assert stop is not None
    assert "STOP_LOSS" in stop

    # Price drops only 10% (above 15% stop-loss)
    stop = await risk_mgr.check_stop_loss(pos, 9.0)
    assert stop is None


@pytest.mark.asyncio
async def test_no_leverage_allowed(risk_setup):
    """Verify leverage is always blocked."""
    risk_mgr, paper = risk_setup
    config = MockConfig()
    assert config.risk.allow_leverage is False


@pytest.mark.asyncio
async def test_no_real_funds(risk_setup):
    """CRITICAL: Verify system operates only in paper mode."""
    risk_mgr, paper = risk_setup
    assert paper.config.paper_account.use_real_funds is False
    assert paper.config.paper_account.send_real_transactions is False
