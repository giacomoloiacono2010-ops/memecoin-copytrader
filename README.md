# Memecoin CopyTrader — Paper Trading Bot

> ⚠️ **PAPER TRADING ONLY** — No real funds, no real transactions, no private keys.

## Overview

A modular, testable paper trading bot that copies memecoin trades from profitable Solana wallets. Designed for experimentation and learning only.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  ORCHESTRATOR (main.py)          │
├─────────┬──────────┬──────────┬─────────────────┤
│ DISCOVER │ SCORING  │ MONITOR  │ REPORTER        │
│ wallets  │ wallets  │ signals  │ performance     │
├─────────┴──────────┴──────────┴─────────────────┤
│              COPY ENGINE (copy_engine.py)         │
│   signal → safety check → risk check → delay →   │
│   paper execution                                │
├─────────────────────────────────────────────────┤
│              PAPER ACCOUNT (paper_account.py)    │
│   virtual balance, positions, trade history      │
├─────────────────────────────────────────────────┤
│              RISK MANAGER (risk_manager.py)      │
│   position sizing, stops, drawdown monitoring    │
├─────────────────────────────────────────────────┤
│              MONITORING (monitoring.py)          │
│   logging, kill switch, health checks            │
└─────────────────────────────────────────────────┘
```

## Modules

| # | Module | File | Responsibility |
|---|---|---|---|
| 1 | Config Loader | `config_loader.py` | Load, validate, expose immutable config |
| 2 | Paper Account | `paper_account.py` | Virtual portfolio management |
| 3 | Market Data | `market_data.py` | Price feeds, liquidity data |
| 4 | Dex API | `dex_api.py` | HTTP client for Birdeye/DexScreener/Solana RPC |
| 5 | Wallet Discovery | `wallet_discovery.py` | Find profitable wallets |
| 6 | Wallet Scoring | `wallet_scoring.py` | Score and filter wallets |
| 7 | Token Safety | `token_safety.py` | Evaluate token safety |
| 8 | Copy Engine | `copy_engine.py` | Core trade processing with 200ms delay |
| 9 | Risk Manager | `risk_manager.py` | Position sizing, drawdown, stops |
| 10 | Monitoring | `monitoring.py` | Logging, kill switch, reports |

## Safety Features

1. **Config Validation**: System refuses to start with `use_real_funds: true`
2. **No Crypto SDKs**: No `solana-py`, `solders`, or signing libraries installed
3. **Isolated Paper Account**: No `send_transaction` method exists
4. **Real Transaction Blocker**: Intercepts and blocks any write RPC calls
5. **No Key Storage**: Never asks for or stores private keys/seed phrases

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python main.py

# Kill switch (remote)
python kill.py
```

## Configuration

All parameters are in `config.yaml`. Key settings:

```yaml
paper_account:
  initial_balance_usd: 10000
  mode: paper_trading          # LOCKED — cannot be changed
  use_real_funds: false        # LOCKED — cannot be changed
  send_real_transactions: false # LOCKED — cannot be changed

execution:
  delay_ms: 200                # Simulated delay before execution

risk:
  max_risk_per_trade_pct: 0.5
  max_total_exposure_pct: 20.0
  max_drawdown_pct: 10.0       # Triggers kill switch
  max_daily_loss_pct: 3.0      # Blocks trading for the day
```

## Kill Switch

Three ways to trigger:

1. **Manual**: `python kill.py` (creates KILL file)
2. **Automatic**: Drawdown > 10% triggers kill switch
3. **Signal**: `Ctrl+C` in terminal

## Reports

Generated every hour and at shutdown in `reports/`:

- Portfolio value, ROI, drawdown
- Trade statistics (win rate, PnL, slippage, latency)
- Risk metrics
- Recent trade log

## Logging

Structured JSON-lines logs in `logs/`:

- `trades.jsonl` — Every trade execution
- `signals.jsonl` — Every detected signal
- `errors.jsonl` — All errors
- `state_changes.jsonl` — System state changes
- `risk_decisions.jsonl` — Risk manager decisions
- `wallet_analysis.jsonl` — Wallet scoring results
- `health.jsonl` — Periodic health checks

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=modules --cov-report=term
```

## Disclaimer

⚠️ **This system is EXCLUSIVELY experimental and educational.**

- No financial advice is provided or implied.
- Paper trading results do NOT reflect real trading performance.
- Memecoin markets are extremely volatile and high-risk.
- Never invest money you cannot afford to lose.

## License

MIT — For educational purposes only.
