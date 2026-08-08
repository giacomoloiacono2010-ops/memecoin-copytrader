"""Tests for token_safety module."""

import pytest
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.token_safety import TokenSafetyService, TokenSafetyReport
from modules.monitoring import StructuredLogger


class MockConfig:
    class token_safety:
        min_safety_score = 65
        max_top10_holders_pct = 40.0
        min_liquidity_usd = 20000.0
        max_token_age_hours = 72
        require_mint_revoked = True
        require_freeze_revoked = True
        min_burned_liquidity_pct = 50.0
    class risk:
        max_price_impact_bps = 200
        max_risk_per_trade_pct = 0.5
    class paper_account:
        initial_balance_usd = 10000.0
        mode = "paper_trading"


@pytest.fixture
def token_safety(tmp_path):
    """Create token safety service with mock market data."""
    config = MockConfig()
    logger = MagicMock(spec=StructuredLogger)

    # Mock market data
    market_data = AsyncMock()
    market_data.get_token_data = AsyncMock(return_value=None)  # Default: no data

    return TokenSafetyService(config, market_data, logger), market_data


@pytest.mark.asyncio
async def test_unsafe_when_no_data(token_safety):
    """Test that token is rejected when market data is unavailable."""
    safety, market_data = token_safety
    market_data.get_token_data = AsyncMock(return_value=None)

    report = await safety.analyze_token("unknown_token")

    assert report.is_safe is False
    assert report.safety_score < 65
    assert any("unavailable" in r.lower() for r in report.rejection_reasons)


@pytest.mark.asyncio
async def test_safe_token(token_safety):
    """Test that a token with good metrics is approved."""
    safety, market_data = token_safety

    # Mock good market data
    from modules.market_data import TokenMarketData
    good_data = TokenMarketData(
        token="good_token", name="Good Token", symbol="GOOD",
        price_usd=0.01, liquidity_usd=100000.0,
        volume_24h=50000.0, fdv=500000.0,
        price_impact_1pct=1.0, timestamp=9999999999.0,
        source="dexscreener",
    )
    market_data.get_token_data = AsyncMock(return_value=good_data)

    report = await safety.analyze_token("good_token")

    assert report.is_safe is True
    assert report.safety_score >= 65


@pytest.mark.asyncio
async def test_low_liquidity_rejected(token_safety):
    """Test that low liquidity tokens are rejected."""
    safety, market_data = token_safety

    from modules.market_data import TokenMarketData
    low_liq_data = TokenMarketData(
        token="low_liq", name="Low Liq", symbol="LOW",
        price_usd=0.001, liquidity_usd=5000.0,  # Below $20k
        volume_24h=1000.0, fdv=100000.0,
        price_impact_1pct=5.0, timestamp=9999999999.0,
        source="dexscreener",
    )
    market_data.get_token_data = AsyncMock(return_value=low_liq_data)

    report = await safety.analyze_token("low_liq")

    assert report.is_safe is False
    assert len(report.rejection_reasons) > 0  # Has rejection reasons


def test_safety_report_properties():
    """Test TokenSafetyReport dataclass."""
    report = TokenSafetyReport(
        token="test", token_name="Test", safety_score=80,
        is_safe=True, rejection_reasons=[],
        checks={"liquidity": {"pass": True}},
        analyzed_at=9999999999.0,
    )

    assert report.is_safe is True
    assert report.safety_score == 80
    assert len(report.rejection_reasons) == 0
