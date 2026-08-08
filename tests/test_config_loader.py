"""Tests for config_loader module."""

import pytest
import tempfile
import os
from pathlib import Path

# Minimal valid config for testing
VALID_CONFIG = """
paper_account:
  name: "TEST_ACCOUNT"
  initial_balance_usd: 10000.0
  mode: "paper_trading"
  use_real_funds: false
  send_real_transactions: false
execution:
  delay_ms: 200
  max_signal_age_ms: 800
  slippage_model: "linear"
  default_slippage_bps: 50
risk:
  max_risk_per_trade_pct: 0.5
  max_total_exposure_pct: 20.0
  max_open_positions: 5
  max_trades_per_hour: 10
  max_daily_loss_pct: 3.0
  max_drawdown_pct: 10.0
  max_slippage_bps: 150
  max_price_impact_bps: 200
  min_liquidity_usd: 20000.0
  max_gas_per_trade_usd: 5.0
  allow_leverage: false
  allow_short: false
  stop_loss_pct: 15.0
  trailing_take_profit_pct: 50.0
  warning_drawdown_pct: 8.0
wallet_discovery:
  max_wallets_to_copy: 10
  min_trades_per_wallet: 20
  min_win_rate_pct: 45.0
  min_profit_factor: 1.4
  max_single_token_profit_pct: 60.0
  rebalance_interval_hours: 4
  top_tokens_scan_count: 20
  top_traders_per_token: 50
wallet_scoring:
  weights:
    profit_consistency: 0.25
    diversification: 0.15
    temporal_pattern: 0.15
    position_sizing: 0.15
    wallet_age: 0.10
    relative_volume: 0.20
  min_score: 60
  bot_detection:
    max_trades_per_hour: 50
    identical_pattern_threshold: 0.95
token_safety:
  min_safety_score: 65
  max_top10_holders_pct: 40.0
  min_liquidity_usd: 20000.0
  max_token_age_hours: 72
  require_mint_revoked: true
  require_freeze_revoked: true
  min_burned_liquidity_pct: 50.0
api:
  solana_rpc:
    url: "https://api.mainnet-beta.solana.com"
    timeout_seconds: 10
    max_retries: 3
  birdeye:
    base_url: "https://public-api.birdeye.so"
    api_key: ""
    rate_limit_per_second: 0.5
    cache_ttl_seconds: 5
  dexscreener:
    base_url: "https://api.dexscreener.com/latest/dex"
    rate_limit_per_second: 0.2
    cache_ttl_seconds: 30
monitoring:
  poll_interval_seconds: 2
  health_check_interval_seconds: 30
  report_interval_hours: 1
  test_duration_hours: 72
  log_level: "INFO"
  kill_switch_check_seconds: 1
  graceful_shutdown_timeout_seconds: 5
"""

REAL_FUNDS_CONFIG = """
paper_account:
  name: "BAD_CONFIG"
  initial_balance_usd: 10000.0
  mode: "paper_trading"
  use_real_funds: true
  send_real_transactions: false
execution:
  delay_ms: 200
  max_signal_age_ms: 800
  slippage_model: "linear"
  default_slippage_bps: 50
risk:
  max_risk_per_trade_pct: 0.5
  max_total_exposure_pct: 20.0
  max_open_positions: 5
  max_trades_per_hour: 10
  max_daily_loss_pct: 3.0
  max_drawdown_pct: 10.0
  max_slippage_bps: 150
  max_price_impact_bps: 200
  min_liquidity_usd: 20000.0
  max_gas_per_trade_usd: 5.0
  allow_leverage: false
  allow_short: false
  stop_loss_pct: 15.0
  trailing_take_profit_pct: 50.0
  warning_drawdown_pct: 8.0
wallet_discovery:
  max_wallets_to_copy: 10
  min_trades_per_wallet: 20
  min_win_rate_pct: 45.0
  min_profit_factor: 1.4
  max_single_token_profit_pct: 60.0
  rebalance_interval_hours: 4
  top_tokens_scan_count: 20
  top_traders_per_token: 50
wallet_scoring:
  weights:
    profit_consistency: 0.25
    diversification: 0.15
    temporal_pattern: 0.15
    position_sizing: 0.15
    wallet_age: 0.10
    relative_volume: 0.20
  min_score: 60
  bot_detection:
    max_trades_per_hour: 50
    identical_pattern_threshold: 0.95
token_safety:
  min_safety_score: 65
  max_top10_holders_pct: 40.0
  min_liquidity_usd: 20000.0
  max_token_age_hours: 72
  require_mint_revoked: true
  require_freeze_revoked: true
  min_burned_liquidity_pct: 50.0
api:
  solana_rpc:
    url: "https://api.mainnet-beta.solana.com"
    timeout_seconds: 10
    max_retries: 3
  birdeye:
    base_url: "https://public-api.birdeye.so"
    api_key: ""
    rate_limit_per_second: 0.5
    cache_ttl_seconds: 5
  dexscreener:
    base_url: "https://api.dexscreener.com/latest/dex"
    rate_limit_per_second: 0.2
    cache_ttl_seconds: 30
monitoring:
  poll_interval_seconds: 2
  health_check_interval_seconds: 30
  report_interval_hours: 1
  test_duration_hours: 72
  log_level: "INFO"
  kill_switch_check_seconds: 1
  graceful_shutdown_timeout_seconds: 5
"""

LEVERAGE_CONFIG = """
paper_account:
  name: "BAD_CONFIG"
  initial_balance_usd: 10000.0
  mode: "paper_trading"
  use_real_funds: false
  send_real_transactions: false
execution:
  delay_ms: 200
  max_signal_age_ms: 800
  slippage_model: "linear"
  default_slippage_bps: 50
risk:
  max_risk_per_trade_pct: 0.5
  max_total_exposure_pct: 20.0
  max_open_positions: 5
  max_trades_per_hour: 10
  max_daily_loss_pct: 3.0
  max_drawdown_pct: 10.0
  max_slippage_bps: 150
  max_price_impact_bps: 200
  min_liquidity_usd: 20000.0
  max_gas_per_trade_usd: 5.0
  allow_leverage: true
  allow_short: false
  stop_loss_pct: 15.0
  trailing_take_profit_pct: 50.0
  warning_drawdown_pct: 8.0
wallet_discovery:
  max_wallets_to_copy: 10
  min_trades_per_wallet: 20
  min_win_rate_pct: 45.0
  min_profit_factor: 1.4
  max_single_token_profit_pct: 60.0
  rebalance_interval_hours: 4
  top_tokens_scan_count: 20
  top_traders_per_token: 50
wallet_scoring:
  weights:
    profit_consistency: 0.25
    diversification: 0.15
    temporal_pattern: 0.15
    position_sizing: 0.15
    wallet_age: 0.10
    relative_volume: 0.20
  min_score: 60
  bot_detection:
    max_trades_per_hour: 50
    identical_pattern_threshold: 0.95
token_safety:
  min_safety_score: 65
  max_top10_holders_pct: 40.0
  min_liquidity_usd: 20000.0
  max_token_age_hours: 72
  require_mint_revoked: true
  require_freeze_revoked: true
  min_burned_liquidity_pct: 50.0
api:
  solana_rpc:
    url: "https://api.mainnet-beta.solana.com"
    timeout_seconds: 10
    max_retries: 3
  birdeye:
    base_url: "https://public-api.birdeye.so"
    api_key: ""
    rate_limit_per_second: 0.5
    cache_ttl_seconds: 5
  dexscreener:
    base_url: "https://api.dexscreener.com/latest/dex"
    rate_limit_per_second: 0.2
    cache_ttl_seconds: 30
monitoring:
  poll_interval_seconds: 2
  health_check_interval_seconds: 30
  report_interval_hours: 1
  test_duration_hours: 72
  log_level: "INFO"
  kill_switch_check_seconds: 1
  graceful_shutdown_timeout_seconds: 5
"""


@pytest.fixture
def valid_config_file(tmp_path):
    """Create a valid config file for testing."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(VALID_CONFIG)
    return str(config_file)


@pytest.fixture
def real_funds_config_file(tmp_path):
    """Create a config with use_real_funds=true."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(REAL_FUNDS_CONFIG)
    return str(config_file)


@pytest.fixture
def leverage_config_file(tmp_path):
    """Create a config with allow_leverage=true."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(LEVERAGE_CONFIG)
    return str(config_file)


def test_valid_config_loads(valid_config_file):
    """Test that valid config loads successfully."""
    from modules.config_loader import load_config
    config = load_config(valid_config_file)

    assert config.paper_account.mode == "paper_trading"
    assert config.paper_account.use_real_funds is False
    assert config.paper_account.send_real_transactions is False
    assert config.risk.allow_leverage is False
    assert config.risk.allow_short is False
    assert config.execution.delay_ms == 200


def test_real_funds_rejected(real_funds_config_file):
    """Test that config with use_real_funds=true is rejected."""
    from modules.config_loader import load_config, ConfigValidationError

    with pytest.raises(ConfigValidationError, match="use_real_funds must be false"):
        load_config(real_funds_config_file)


def test_leverage_rejected(leverage_config_file):
    """Test that config with allow_leverage=true is rejected."""
    from modules.config_loader import load_config, ConfigValidationError

    with pytest.raises(ConfigValidationError, match="allow_leverage must be false"):
        load_config(leverage_config_file)


def test_missing_config_file():
    """Test that missing config file raises error."""
    from modules.config_loader import load_config, ConfigValidationError

    with pytest.raises(ConfigValidationError, match="Config file not found"):
        load_config("nonexistent.yaml")


def test_config_is_immutable(valid_config_file):
    """Test that config objects are frozen (immutable)."""
    from modules.config_loader import load_config

    config = load_config(valid_config_file)

    with pytest.raises(AttributeError):
        config.paper_account.mode = "hacked"


def test_paper_trading_only(valid_config_file):
    """Test that mode is locked to paper_trading."""
    from modules.config_loader import load_config

    config = load_config(valid_config_file)
    assert config.paper_account.mode == "paper_trading"


def test_default_values(valid_config_file):
    """Test that default values match spec."""
    from modules.config_loader import load_config

    config = load_config(valid_config_file)

    assert config.paper_account.initial_balance_usd == 10000.0
    assert config.execution.delay_ms == 200
    assert config.execution.max_signal_age_ms == 800
    assert config.risk.max_risk_per_trade_pct == 0.5
    assert config.risk.max_total_exposure_pct == 20.0
    assert config.risk.max_open_positions == 5
    assert config.risk.max_daily_loss_pct == 3.0
    assert config.risk.max_drawdown_pct == 10.0
    assert config.risk.max_slippage_bps == 150
    assert config.wallet_discovery.max_wallets_to_copy == 10
    assert config.wallet_discovery.min_win_rate_pct == 45.0
    assert config.token_safety.min_safety_score == 65
