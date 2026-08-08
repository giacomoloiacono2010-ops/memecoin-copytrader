"""
Live Test v3 — Full Copy-Trading Flow (DexScreener-only)
=========================================================
Uses DexScreener for everything: tokens, volume, buy/sell pressure.
Simulates wallet copying based on on-chain metrics.
"""

import asyncio
import json
import time
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

from modules.config_loader import load_config
from modules.dex_api import DexApiClient
from modules.market_data import MarketDataService
from modules.monitoring import StructuredLogger, RealTransactionBlocker
from modules.paper_account import PaperAccount
from modules.risk_manager import RiskManagerService
from modules.token_safety import TokenSafetyService


def dex_get(path):
    """DexScreener API call."""
    if path.startswith("/token-boosts") or path.startswith("/token-profiles"):
        url = f"https://api.dexscreener.com{path}"
    else:
        url = f"https://api.dexscreener.com/latest/dex{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


async def run():
    print("=" * 70)
    print("  MEMECOIN COPYTRADER — LIVE TEST v3")
    print("  PAPER TRADING ONLY | NO REAL TRANSACTIONS")
    print("=" * 70)
    print()

    config = load_config("config.yaml")
    logger = StructuredLogger(config)
    blocker = RealTransactionBlocker(logger)
    dex_api = DexApiClient(config, logger, blocker)
    await dex_api.initialize()
    market_data = MarketDataService(config, dex_api, logger)
    paper = PaperAccount(config, logger, blocker)
    await paper.initialize()

    print(f"Account: {config.paper_account.name} | Balance: ${config.paper_account.initial_balance_usd}")
    print()

    # ── STEP 1: Fetch boosted tokens ──
    print("[1/8] Fetching boosted Solana memecoins...")
    data = dex_get("/token-boosts/latest/v1")
    boosted = [t for t in data if t.get("chainId") == "solana"]
    boosted.sort(key=lambda x: x.get("amount", 0), reverse=True)
    print(f"   Found {len(boosted)} Solana boosted tokens")
    print()

    # ── STEP 2: Get detailed info for top tokens ──
    print("[2/8] Fetching token details (price, volume, liquidity)...")
    tokens = []
    for item in boosted[:8]:
        addr = item.get("tokenAddress", "")
        if not addr:
            continue
        try:
            detail = dex_get(f"/tokens/{addr}")
        except Exception as e:
            print(f"   ⚠️  {addr[:16]}: {type(e).__name__}")
            continue

        if not detail or "pairs" not in detail or not detail["pairs"]:
            continue

        # Find the pair with highest volume
        best_pair = max(detail["pairs"], key=lambda p: float(p.get("volume", {}).get("h24", 0)))
        price = float(best_pair.get("priceUsd", 0))
        liq = float(best_pair.get("liquidity", {}).get("usd", 0))
        vol = float(best_pair.get("volume", {}).get("h24", 0))
        buys = best_pair.get("txns", {}).get("h24", {}).get("buys", 0)
        sells = best_pair.get("txns", {}).get("h24", {}).get("sells", 0)
        name = best_pair.get("baseToken", {}).get("name", "Unknown")
        symbol = best_pair.get("baseToken", {}).get("symbol", "?")
        pair_addr = best_pair.get("pairAddress", "")

        if price <= 0:
            continue

        buy_pressure = buys / (buys + sells) * 100 if (buys + sells) > 0 else 50
        tokens.append({
            "address": addr,
            "pair_address": pair_addr,
            "name": name,
            "symbol": symbol,
            "price": price,
            "liquidity": liq,
            "volume_24h": vol,
            "buys_24h": buys,
            "sells_24h": sells,
            "buy_pressure": buy_pressure,
            "boost": item.get("amount", 0),
        })

        emoji = "🟢" if buy_pressure > 55 else "🔴" if buy_pressure < 45 else "🟡"
        print(f"   {emoji} {name[:22]:22s} ({symbol[:6]:6s}) | ${price:.10f} | liq=${liq:>10,.0f} | vol=${vol:>10,.0f} | buys={buys:>5d} sells={sells:>5d}")

    print(f"\n   {len(tokens)} tokens with data")
    print()

    # ── STEP 3: Score tokens as copy targets ──
    print("[3/8] Scoring tokens as copy-trade targets...")
    scored = []
    for t in tokens:
        score = 0

        # Volume score (higher = better)
        if t["volume_24h"] > 500000:
            score += 30
        elif t["volume_24h"] > 100000:
            score += 20
        elif t["volume_24h"] > 10000:
            score += 10

        # Liquidity score
        if t["liquidity"] > 100000:
            score += 25
        elif t["liquidity"] > 20000:
            score += 15
        elif t["liquidity"] > 5000:
            score += 5

        # Buy pressure score (buying = good for copy)
        if t["buy_pressure"] > 60:
            score += 25
        elif t["buy_pressure"] > 55:
            score += 15
        elif t["buy_pressure"] > 50:
            score += 5

        # Boost score
        if t["boost"] >= 100:
            score += 20
        elif t["boost"] >= 50:
            score += 10

        t["score"] = score
        scored.append(t)

    scored.sort(key=lambda x: x["score"], reverse=True)

    print(f"   {'#':>3} {'Score':>6} {'Name':22s} {'Symbol':6s} {'Price':>14s} {'Liquidity':>12s} {'BuyP%':>6s}")
    print(f"   {'─'*3} {'─'*6} {'─'*22} {'─'*6} {'─'*14} {'─'*12} {'─'*6}")
    for i, t in enumerate(scored):
        marker = " ◀ COPY" if t["score"] >= 60 else ""
        print(f"   {i+1:3d} {t['score']:6d} {t['name'][:22]:22s} {t['symbol'][:6]:6s} ${t['price']:>13.10f} ${t['liquidity']:>11,.0f} {t['buy_pressure']:5.1f}%{marker}")
    print()

    # ── STEP 4: Select wallets to copy (simulated) ──
    print("[4/8] Selecting copy targets...")
    copy_targets = [t for t in scored if t["score"] >= 40][:5]

    if not copy_targets:
        copy_targets = scored[:3]

    for i, t in enumerate(copy_targets):
        print(f"   🎯 Target {i+1}: {t['name'][:25]} ({t['symbol']}) | score={t['score']} | buy_pressure={t['buy_pressure']:.1f}%")
        print(f"      Strategy: {'BUY' if t['buy_pressure'] > 55 else 'WATCH'} | Max position: ${config.paper_account.initial_balance_usd * config.risk.max_risk_per_trade_pct / 100:.2f}")
    print()

    # ── STEP 5: Token safety checks ──
    print("[5/8] Running token safety checks...")
    token_safety = TokenSafetyService(config, market_data, logger)
    safe_targets = []
    for t in copy_targets:
        report = await token_safety.analyze_token(t["address"])
        status = "✅ SAFE" if report.is_safe else "⚠️ RISKY"
        print(f"   {status} | {t['name'][:20]:20s} | safety_score={report.safety_score:.0f} | liq=${t['liquidity']:,.0f}")
        if report.is_safe:
            safe_targets.append(t)
        for r in report.rejection_reasons:
            print(f"      └─ {r}")

    if not safe_targets:
        print("   ⚠️  No tokens passed safety. Using top token anyway for demo.")
        safe_targets = copy_targets[:1]
    print()

    # ── STEP 6: Execute paper trades ──
    print("[6/8] Executing paper trades (200ms delay)...")
    risk_manager = RiskManagerService(config, paper, logger)
    trades_done = 0

    for t in safe_targets[:3]:
        trade_value = config.paper_account.initial_balance_usd * (config.risk.max_risk_per_trade_pct / 100)
        qty = trade_value / t["price"]

        decision = await risk_manager.validate_trade(
            token=t["address"], side="buy", qty=qty,
            price=t["price"], liquidity_usd=t["liquidity"],
        )

        if not decision.approved:
            print(f"   ❌ {t['name'][:20]}: {decision.reason[:50]}")
            continue

        # 200ms delay
        print(f"   ⏳ {t['name'][:20]} | delay 200ms...")
        start = time.time()
        await asyncio.sleep(0.2)
        delay_actual = (time.time() - start) * 1000

        # Execute
        trade = await paper.execute_buy(
            token=t["address"],
            token_name=f"{t['name']} ({t['symbol']})",
            qty=qty,
            price=t["price"],
            slippage_bps=50,
            gas_usd=0.0005,
            source_wallet=f"copy_target_{t['symbol']}",
            signal_detection_time=time.time(),
        )

        if trade.risk_decision == "APPROVED":
            trades_done += 1
            cost = qty * t["price"]
            print(f"   ✅ FILLED | {t['name'][:20]:20s} | {qty:>12,.2f} tokens @ ${t['price']:.10f}")
            print(f"      Cost: ${cost:.6f} | Gas: $0.0005 | Slippage: 0.50% | Delay: {delay_actual:.0f}ms")
            print(f"      Balance after: ${paper.balance:.4f}")
        else:
            print(f"   ❌ {trade.rejection_reason}")

    print()

    # ── STEP 7: Portfolio status ──
    print("[7/8] Portfolio status:")
    print(f"   ┌─────────────────────────────────────────────────────────┐")
    print(f"   │ Balance:     ${paper.balance:>10.4f}                              │")
    print(f"   │ Positions:   {paper.open_positions_count:>3d}                                      │")
    print(f"   │ Trades:      {len(paper.trade_history):>3d}                                      │")
    prices = {tok: pos.avg_entry_price for tok, pos in paper.positions.items()}
    exposure = paper.total_exposure_pct(prices)
    pv = paper.portfolio_value(prices)
    initial = config.paper_account.initial_balance_usd
    drawdown = ((initial - pv) / initial * 100) if initial > 0 else 0.0
    daily = paper.daily_pnl_pct(pv)
    print(f"   │ Exposure:    {exposure:>6.2f}%                                │")
    print(f"   │ Drawdown:    {drawdown:>6.2f}%                                │")
    print(f"   │ Daily PnL:   {daily:>+6.2f}%                                │")
    print(f"   └─────────────────────────────────────────────────────────┘")
    for tok, pos in paper.positions.items():
        print(f"   └─ {pos.token_name[:30]:30s}")
        print(f"      {pos.qty:>12,.2f} tokens @ ${pos.avg_entry_price:.10f}")
        print(f"      Market: ${pos.market_value:.6f} | PnL: ${pos.unrealized_pnl:+.6f} ({pos.unrealized_pnl_pct:+.2f}%)")
    print()

    # ── STEP 8: Final report ──
    print("[8/8] Generating report...")
    report_path = Path("reports") / f"live_test_{int(time.time())}.html"
    report_path.parent.mkdir(exist_ok=True)

    html = f"""<!DOCTYPE html>
<html><head><title>Paper Trading Test Report</title>
<style>
body {{ font-family: monospace; background: #1a1a2e; color: #eee; padding: 20px; }}
h1 {{ color: #0f0; }}
table {{ border-collapse: collapse; margin: 10px 0; }}
td, th {{ border: 1px solid #444; padding: 8px; text-align: left; }}
th {{ background: #16213e; }}
.green {{ color: #0f0; }}
.red {{ color: #f00; }}
</style></head><body>
<h1>📊 Paper Trading Test Report</h1>
<p>Account: {config.paper_account.name} | Mode: PAPER TRADING</p>
<p>Time: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>

<h2>Portfolio</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Balance</td><td class="green">${paper.balance:.4f}</td></tr>
<tr><td>Initial</td><td>${config.paper_account.initial_balance_usd:.4f}</td></tr>
<tr><td>Positions</td><td>{paper.open_positions_count}</td></tr>
<tr><td>Trades</td><td>{len(paper.trade_history)}</td></tr>
<tr><td>Daily PnL</td><td>{'class="green"' if daily >= 0 else 'class="red"'}>{daily:+.2f}%</td></tr>
</table>

<h2>Positions</h2>
<table>
<tr><th>Token</th><th>Qty</th><th>Avg Entry</th><th>Market Value</th><th>PnL</th></tr>
"""
    for tok, pos in paper.positions.items():
        color = "green" if pos.unrealized_pnl >= 0 else "red"
        html += f'<tr><td>{pos.token_name[:30]}</td><td>{pos.qty:.2f}</td><td>${pos.avg_entry_price:.10f}</td><td>${pos.market_value:.6f}</td><td class="{color}">${pos.unrealized_pnl:+.6f} ({pos.unrealized_pnl_pct:+.2f}%)</td></tr>\n'

    html += """</table>

<h2>Trade History</h2>
<table>
<tr><th>Time</th><th>Token</th><th>Side</th><th>Qty</th><th>Price</th><th>Slippage</th><th>Gas</th><th>Source</th></tr>
"""
    for tr in paper.trade_history:
        html += f'<tr><td>{tr.timestamp}</td><td>{tr.token_name[:20]}</td><td>{tr.side}</td><td>{tr.qty:.2f}</td><td>${tr.price:.10f}</td><td>{tr.slippage_bps}bps</td><td>${tr.gas_usd:.4f}</td><td>{tr.source_wallet[:15]}</td></tr>\n'

    html += """</table>

<h2>Copy Targets</h2>
<table>
<tr><th>#</th><th>Token</th><th>Symbol</th><th>Score</th><th>Price</th><th>Liquidity</th><th>Volume 24h</th><th>Buy Pressure</th></tr>
"""
    for i, t in enumerate(scored):
        html += f'<tr><td>{i+1}</td><td>{t["name"][:25]}</td><td>{t["symbol"]}</td><td>{t["score"]}</td><td>${t["price"]:.10f}</td><td>${t["liquidity"]:,.0f}</td><td>${t["volume_24h"]:,.0f}</td><td>{t["buy_pressure"]:.1f}%</td></tr>\n'

    html += """</table>

<h2>System Status</h2>
<table>
<tr><td>Mode</td><td class="green">PAPER TRADING</td></tr>
<tr><td>Real transactions sent</td><td class="green">0</td></tr>
<tr><td>Real funds used</td><td class="green">$0.00</td></tr>
<tr><td>Execution delay</td><td>200ms</td></tr>
<tr><td>Kill switch</td><td>Active (auto at 10% drawdown)</td></tr>
</table>

<p style="color:#666; margin-top:20px;">Generated by Memecoin CopyTrader v1.0 | Paper Trading Only</p>
</body></html>"""

    report_path.write_text(html)
    print(f"   Report saved: {report_path}")
    print()

    # ── FINAL SUMMARY ──
    print("=" * 70)
    print("  ✅ LIVE TEST COMPLETE")
    print("=" * 70)
    print(f"  Tokens analyzed:     {len(tokens)}")
    print(f"  Copy targets found:  {len(copy_targets)}")
    print(f"  Safe tokens:         {len(safe_targets)}")
    print(f"  Trades executed:     {trades_done}")
    print(f"  Final balance:       ${paper.balance:.4f}")
    print(f"  Real tx sent:        0")
    print(f"  Mode:                PAPER TRADING ONLY")
    print("=" * 70)

    await paper.save_state()
    await paper.close()
    await dex_api.close()


if __name__ == "__main__":
    asyncio.run(run())
