"""Tests for copy_engine module."""

import pytest
import asyncio
import time
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.copy_engine import CopyEngineService, TradeSignal, CopyTradeResult
from modules.paper_account import PaperAccount
from modules.token_safety import TokenSafetyService
from modules.risk_manager import RiskManagerService
from modules.wallet_scoring import WalletScoringService
from modules.monitoring import StructuredLogger, KillSwitch, RealTransactionBlocker


class MockConfig:
    class paper_account:
        name = "TEST"
        initial_balance_usd = 10000.0
        mode = "paper_trading"
        use_real_funds = False
        send_real_transactions = False
    class execution:
        delay_ms = 200
        max_signal_age_ms = 800
        default_slippage_bps = 50
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
    class token_safety:
        min_safety_score = 65
        max_top10_holders_pct = 40.0
        min_liquidity_usd = 20000.0
        max_token_age_hours = 72
        require_mint_revoked = True
        require_freeze_revoked = True
        min_burned_liquidity_pct = 50.0
    class wallet_scoring:
        weights = {
            "profit_consistency": 0.25,
            "diversification": 0.15,
            "temporal_pattern": 0.15,
            "position_sizing": 0.15,
            "wallet_age": 0.10,
            "relative_volume": 0.20,
        }
        min_score = 60
        bot_detection = {
            "max_trades_per_hour": 50,
            "identical_pattern_threshold": 0.95,
        }
    class output:
        data_dir = "test_data"


@pytest.fixture
def copy_engine_setup(tmp_path):
    """Create copy engine with mocked dependencies."""
    config = MockConfig()
    config.output.data_dir = str(tmp_path / "data")

    logger = MagicMock(spec=StructuredLogger)
    blocker = MagicMock(spec=RealTransactionBlocker)

    paper = PaperAccount(config, logger, blocker)
    market_data = AsyncMock()
    token_safety = AsyncMock()
    risk_manager = AsyncMock()
    wallet_scoring = MagicMock(spec=WalletScoringService)
    kill_switch = MagicMock(spec=KillSwitch)
    kill_switch.is_triggered = False

    engine = CopyEngineService(
        config, paper, market_data, token_safety,
        risk_manager, wallet_scoring, logger, kill_switch,
    )

    return engine, paper, token_safety, risk_manager, kill_switch


@pytest.mark.asyncio
async def test_signal_rejected_when_kill_switch_active(copy_engine_setup):
    """Test that signals are rejected when kill switch is active."""
    engine, paper, _, _, kill_switch = copy_engine_setup
    kill_switch.is_triggered = True

    signal = TradeSignal(
        id="test_signal", token="test_token", token_name="TEST",
        side="buy", qty=100, price=10.0,
        source_wallet="wallet123",
        detection_time=time.time(), signal_age_ms=100,
        liquidity_usd=100000,
    )

    result = await engine.process_signal(signal)

    assert result.executed is False
    assert result.rejection_reason == "KILL_SWITCH_ACTIVE"


@pytest.mark.asyncio
async def test_signal_rejected_when_too_old(copy_engine_setup):
    """Test that old signals are rejected."""
    engine, paper, _, _, kill_switch = copy_engine_setup
    kill_switch.is_triggered = False

    signal = TradeSignal(
        id="old_signal", token="test_token", token_name="TEST",
        side="buy", qty=100, price=10.0,
        source_wallet="wallet123",
        detection_time=time.time() - 10,  # 10 seconds ago
        signal_age_ms=10000,  # 10 seconds > 800ms max
        liquidity_usd=100000,
    )

    result = await engine.process_signal(signal)

    assert result.executed is False
    assert "too old" in result.rejection_reason.lower()


@pytest.mark.asyncio
async def test_delay_applied(copy_engine_setup):
    """Test that the 200ms delay is applied."""
    engine, paper, token_safety_mock, risk_mock, kill_switch = copy_engine_setup
    kill_switch.is_triggered = False

    # Mock token safety to return safe
    from modules.token_safety import TokenSafetyReport
    safe_report = TokenSafetyReport(
        token="test_token", token_name="TEST", safety_score=85,
        is_safe=True, rejection_reasons=[], checks={},
        analyzed_at=time.time(),
    )
    token_safety_mock.analyze_token = AsyncMock(return_value=safe_report)

    # Mock risk manager to approve
    from modules.risk_manager import RiskDecision
    risk_mock.validate_trade = AsyncMock(return_value=RiskDecision(
        approved=True, modified=False, original_qty=100,
        approved_qty=100, reason="ALL_CHECKS_PASSED",
        risk_metrics={"trade_value": 1000},
    ))

    # Initialize paper account
    await paper.initialize()

    signal = TradeSignal(
        id="delay_test", token="test_token", token_name="TEST",
        side="buy", qty=10, price=10.0,
        source_wallet="wallet123",
        detection_time=time.time(), signal_age_ms=50,
        liquidity_usd=100000,
    )

    start = time.time()
    result = await engine.process_signal(signal)
    elapsed = (time.time() - start) * 1000

    # Should have taken at least 200ms
    assert elapsed >= 180  # Allow some tolerance
    assert result.delay_ms == 200


@pytest.mark.asyncio
async def test_no_real_transactions_sent(copy_engine_setup):
    """CRITICAL: Verify no real transactions are sent."""
    engine, paper, _, _, _ = copy_engine_setup

    # Paper account has no method to send real transactions
    assert not hasattr(paper, 'send_transaction')
    assert not hasattr(paper, 'sign_transaction')
    assert not hasattr(paper, 'submit_transaction')

    # Copy engine has no method to send real transactions
    assert not hasattr(engine, 'send_real_trade')


@pytest.mark.asyncio
async def test_slippage_simulation(copy_engine_setup):
    """Test that slippage is simulated."""
    engine, paper, _, _, _ = copy_engine_setup

    slippage = engine._simulate_slippage(
        qty=100, price=10.0, liquidity=100000.0,
    )

    # Slippage should be positive and within bounds
    assert slippage >= 0
    assert slippage <= 150  # max_slippage_bps


def test_trade_signal_dataclass():
    """Test TradeSignal dataclass."""
    signal = TradeSignal(
        id="test", token="token123", token_name="TEST",
        side="buy", qty=100, price=10.0,
        source_wallet="wallet123",
        detection_time=time.time(), signal_age_ms=100,
        liquidity_usd=50000,
    )

    assert signal.id == "test"
    assert signal.side == "buy"
    assert signal.signal_age_ms == 100
