"""
Module 1: Config Loader
========================
Loads, validates, and exposes the immutable configuration.
CRITICAL: Rejects startup if use_real_funds != false or send_real_transactions != false.
"""

import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PaperAccountConfig:
    name: str
    initial_balance_usd: float
    mode: str
    use_real_funds: bool
    send_real_transactions: bool


@dataclass(frozen=True)
class ExecutionConfig:
    delay_ms: int
    max_signal_age_ms: int
    slippage_model: str
    default_slippage_bps: int


@dataclass(frozen=True)
class RiskConfig:
    max_risk_per_trade_pct: float
    max_total_exposure_pct: float
    max_open_positions: int
    max_trades_per_hour: int
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_slippage_bps: int
    max_price_impact_bps: int
    min_liquidity_usd: float
    max_gas_per_trade_usd: float
    allow_leverage: bool
    allow_short: bool
    stop_loss_pct: float
    trailing_take_profit_pct: float
    warning_drawdown_pct: float


@dataclass(frozen=True)
class WalletDiscoveryConfig:
    max_wallets_to_copy: int
    min_trades_per_wallet: int
    min_win_rate_pct: float
    min_profit_factor: float
    max_single_token_profit_pct: float
    rebalance_interval_hours: int
    top_tokens_scan_count: int
    top_traders_per_token: int


@dataclass(frozen=True)
class WalletScoringConfig:
    weights: dict
    min_score: int
    bot_detection: dict


@dataclass(frozen=True)
class TokenSafetyConfig:
    min_safety_score: int
    max_top10_holders_pct: float
    min_liquidity_usd: float
    max_token_age_hours: int
    require_mint_revoked: bool
    require_freeze_revoked: bool
    min_burned_liquidity_pct: float


@dataclass(frozen=True)
class SolanaRpcConfig:
    url: str
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True)
class BirdeyeConfig:
    base_url: str
    api_key: str
    rate_limit_per_second: float
    cache_ttl_seconds: int


@dataclass(frozen=True)
class DexScreenerConfig:
    base_url: str
    rate_limit_per_second: float
    cache_ttl_seconds: int


@dataclass(frozen=True)
class ApiConfig:
    solana_rpc: SolanaRpcConfig
    birdeye: BirdeyeConfig
    dexscreener: DexScreenerConfig


@dataclass(frozen=True)
class MonitoringConfig:
    poll_interval_seconds: int
    health_check_interval_seconds: int
    report_interval_hours: int
    test_duration_hours: int
    log_level: str
    kill_switch_check_seconds: int
    graceful_shutdown_timeout_seconds: int


@dataclass(frozen=True)
class Config:
    """Immutable master configuration. Created once at startup."""
    paper_account: PaperAccountConfig
    execution: ExecutionConfig
    risk: RiskConfig
    wallet_discovery: WalletDiscoveryConfig
    wallet_scoring: WalletScoringConfig
    token_safety: TokenSafetyConfig
    api: ApiConfig
    monitoring: MonitoringConfig


class ConfigValidationError(Exception):
    """Raised when config validation fails. System cannot start."""
    pass


def _validate_critical_constraints(raw: dict) -> None:
    """
    CRITICAL validation: blocks startup if real funds/transactions are enabled.
    This is the FIRST line of defense.
    """
    paper = raw.get("paper_account", {})

    # Check mode
    mode = paper.get("mode", "")
    if mode != "paper_trading":
        raise ConfigValidationError(
            f"CRITICAL: mode must be 'paper_trading', got '{mode}'. "
            f"System refuses to start in any other mode."
        )

    # Check use_real_funds
    if paper.get("use_real_funds", True) is True:
        raise ConfigValidationError(
            "CRITICAL: use_real_funds must be false. "
            "This system operates ONLY in paper trading mode."
        )

    # Check send_real_transactions
    if paper.get("send_real_transactions", True) is True:
        raise ConfigValidationError(
            "CRITICAL: send_real_transactions must be false. "
            "No real transactions will ever be sent."
        )

    # Check leverage and shorting
    risk = raw.get("risk", {})
    if risk.get("allow_leverage", False) is True:
        raise ConfigValidationError(
            "CRITICAL: allow_leverage must be false. "
            "Leverage is not permitted in this system."
        )
    if risk.get("allow_short", False) is True:
        raise ConfigValidationError(
            "CRITICAL: allow_short must be false. "
            "Short selling is not permitted in this system."
        )


def load_config(config_path: str = "config.yaml") -> Config:
    """
    Load and validate configuration from YAML file.
    Returns immutable Config object.
    Raises ConfigValidationError if any constraint is violated.
    """
    path = Path(config_path)
    if not path.exists():
        raise ConfigValidationError(f"Config file not found: {config_path}")

    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigValidationError("Config file is empty or invalid YAML.")

    # CRITICAL: validate real funds/transactions BEFORE building config
    _validate_critical_constraints(raw)

    # Build immutable config objects
    try:
        pa_raw = raw["paper_account"]
        paper_account = PaperAccountConfig(
            name=pa_raw["name"],
            initial_balance_usd=float(pa_raw["initial_balance_usd"]),
            mode=pa_raw["mode"],
            use_real_funds=bool(pa_raw["use_real_funds"]),
            send_real_transactions=bool(pa_raw["send_real_transactions"]),
        )

        ex_raw = raw["execution"]
        execution = ExecutionConfig(
            delay_ms=int(ex_raw["delay_ms"]),
            max_signal_age_ms=int(ex_raw["max_signal_age_ms"]),
            slippage_model=str(ex_raw["slippage_model"]),
            default_slippage_bps=int(ex_raw["default_slippage_bps"]),
        )

        r_raw = raw["risk"]
        risk = RiskConfig(
            max_risk_per_trade_pct=float(r_raw["max_risk_per_trade_pct"]),
            max_total_exposure_pct=float(r_raw["max_total_exposure_pct"]),
            max_open_positions=int(r_raw["max_open_positions"]),
            max_trades_per_hour=int(r_raw["max_trades_per_hour"]),
            max_daily_loss_pct=float(r_raw["max_daily_loss_pct"]),
            max_drawdown_pct=float(r_raw["max_drawdown_pct"]),
            max_slippage_bps=int(r_raw["max_slippage_bps"]),
            max_price_impact_bps=int(r_raw["max_price_impact_bps"]),
            min_liquidity_usd=float(r_raw["min_liquidity_usd"]),
            max_gas_per_trade_usd=float(r_raw["max_gas_per_trade_usd"]),
            allow_leverage=bool(r_raw["allow_leverage"]),
            allow_short=bool(r_raw["allow_short"]),
            stop_loss_pct=float(r_raw["stop_loss_pct"]),
            trailing_take_profit_pct=float(r_raw["trailing_take_profit_pct"]),
            warning_drawdown_pct=float(r_raw["warning_drawdown_pct"]),
        )

        wd_raw = raw["wallet_discovery"]
        wallet_discovery = WalletDiscoveryConfig(
            max_wallets_to_copy=int(wd_raw["max_wallets_to_copy"]),
            min_trades_per_wallet=int(wd_raw["min_trades_per_wallet"]),
            min_win_rate_pct=float(wd_raw["min_win_rate_pct"]),
            min_profit_factor=float(wd_raw["min_profit_factor"]),
            max_single_token_profit_pct=float(wd_raw["max_single_token_profit_pct"]),
            rebalance_interval_hours=int(wd_raw["rebalance_interval_hours"]),
            top_tokens_scan_count=int(wd_raw["top_tokens_scan_count"]),
            top_traders_per_token=int(wd_raw["top_traders_per_token"]),
        )

        ws_raw = raw["wallet_scoring"]
        wallet_scoring = WalletScoringConfig(
            weights=ws_raw["weights"],
            min_score=int(ws_raw["min_score"]),
            bot_detection=ws_raw["bot_detection"],
        )

        ts_raw = raw["token_safety"]
        token_safety = TokenSafetyConfig(
            min_safety_score=int(ts_raw["min_safety_score"]),
            max_top10_holders_pct=float(ts_raw["max_top10_holders_pct"]),
            min_liquidity_usd=float(ts_raw["min_liquidity_usd"]),
            max_token_age_hours=int(ts_raw["max_token_age_hours"]),
            require_mint_revoked=bool(ts_raw["require_mint_revoked"]),
            require_freeze_revoked=bool(ts_raw["require_freeze_revoked"]),
            min_burned_liquidity_pct=float(ts_raw["min_burned_liquidity_pct"]),
        )

        api_raw = raw["api"]
        api = ApiConfig(
            solana_rpc=SolanaRpcConfig(
                url=api_raw["solana_rpc"]["url"],
                timeout_seconds=int(api_raw["solana_rpc"]["timeout_seconds"]),
                max_retries=int(api_raw["solana_rpc"]["max_retries"]),
            ),
            birdeye=BirdeyeConfig(
                base_url=api_raw["birdeye"]["base_url"],
                api_key=str(api_raw["birdeye"].get("api_key", "")),
                rate_limit_per_second=float(api_raw["birdeye"]["rate_limit_per_second"]),
                cache_ttl_seconds=int(api_raw["birdeye"]["cache_ttl_seconds"]),
            ),
            dexscreener=DexScreenerConfig(
                base_url=api_raw["dexscreener"]["base_url"],
                rate_limit_per_second=float(api_raw["dexscreener"]["rate_limit_per_second"]),
                cache_ttl_seconds=int(api_raw["dexscreener"]["cache_ttl_seconds"]),
            ),
        )

        m_raw = raw["monitoring"]
        monitoring = MonitoringConfig(
            poll_interval_seconds=int(m_raw["poll_interval_seconds"]),
            health_check_interval_seconds=int(m_raw["health_check_interval_seconds"]),
            report_interval_hours=int(m_raw["report_interval_hours"]),
            test_duration_hours=int(m_raw["test_duration_hours"]),
            log_level=str(m_raw["log_level"]),
            kill_switch_check_seconds=int(m_raw["kill_switch_check_seconds"]),
            graceful_shutdown_timeout_seconds=int(m_raw["graceful_shutdown_timeout_seconds"]),
        )

    except KeyError as e:
        raise ConfigValidationError(f"Missing required config key: {e}")
    except (ValueError, TypeError) as e:
        raise ConfigValidationError(f"Invalid config value: {e}")

    return Config(
        paper_account=paper_account,
        execution=execution,
        risk=risk,
        wallet_discovery=wallet_discovery,
        wallet_scoring=wallet_scoring,
        token_safety=token_safety,
        api=api,
        monitoring=monitoring,
    )
