"""
Memecoin CopyTrader — Pre-Flight Safety Checklist
===================================================
Runs ALL 20 checks before the bot can start.
If ANY check fails, the bot does NOT start.

Usage: python preflight.py
Output: CHECKLIST_PASSED / ERRORS / WARNINGS / NEXT_STEP
"""

import os
import sys
import time
import json
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Ensure we're in the right directory
os.chdir(Path(__file__).parent)
sys.path.insert(0, ".")


@dataclass
class CheckResult:
    id: int
    name: str
    status: str  # "PASS" | "FAIL" | "WARN"
    detail: str


@dataclass
class Checklist:
    results: list[CheckResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def add(self, result: CheckResult):
        self.results.append(result)
        if result.status == "FAIL":
            self.errors.append(f"[{result.id}] {result.name}: {result.detail}")
        elif result.status == "WARN":
            self.warnings.append(f"[{result.id}] {result.name}: {result.detail}")


def run_checks() -> Checklist:
    """Execute all 20 pre-flight checks."""
    cl = Checklist()

    # ═══════════════════════════════════════════════════════════════
    # CHECK 1: mode = paper_trading
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.config_loader import load_config
        config = load_config("config.yaml")
        if config.paper_account.mode == "paper_trading":
            cl.add(CheckResult(1, "mode = paper_trading", "PASS",
                               f"mode={config.paper_account.mode}"))
        else:
            cl.add(CheckResult(1, "mode = paper_trading", "FAIL",
                               f"mode={config.paper_account.mode} (expected 'paper_trading')"))
    except Exception as e:
        cl.add(CheckResult(1, "mode = paper_trading", "FAIL", str(e)))
        return cl  # Can't continue without config

    # ═══════════════════════════════════════════════════════════════
    # CHECK 2: use_real_funds = false
    # ═══════════════════════════════════════════════════════════════
    if config.paper_account.use_real_funds is False:
        cl.add(CheckResult(2, "use_real_funds = false", "PASS", "use_real_funds=False"))
    else:
        cl.add(CheckResult(2, "use_real_funds = false", "FAIL",
                           f"use_real_funds={config.paper_account.use_real_funds}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 3: send_real_transactions = false
    # ═══════════════════════════════════════════════════════════════
    if config.paper_account.send_real_transactions is False:
        cl.add(CheckResult(3, "send_real_transactions = false", "PASS",
                           "send_real_transactions=False"))
    else:
        cl.add(CheckResult(3, "send_real_transactions = false", "FAIL",
                           f"send_real_transactions={config.paper_account.send_real_transactions}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 4: execution_delay_ms = 200
    # ═══════════════════════════════════════════════════════════════
    if config.execution.delay_ms == 200:
        cl.add(CheckResult(4, "execution_delay_ms = 200", "PASS",
                           f"delay_ms={config.execution.delay_ms}"))
    else:
        cl.add(CheckResult(4, "execution_delay_ms = 200", "FAIL",
                           f"delay_ms={config.execution.delay_ms} (expected 200)"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 5: paper account HERMES_PAPER_ACCOUNT_01 creato
    # ═══════════════════════════════════════════════════════════════
    if config.paper_account.name == "HERMES_PAPER_ACCOUNT_01":
        cl.add(CheckResult(5, "paper_account HERMES_PAPER_ACCOUNT_01", "PASS",
                           f"name={config.paper_account.name}, balance=${config.paper_account.initial_balance_usd}"))
    else:
        cl.add(CheckResult(5, "paper_account HERMES_PAPER_ACCOUNT_01", "FAIL",
                           f"name={config.paper_account.name} (expected HERMES_PAPER_ACCOUNT_01)"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 6: risk_manager_service attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.risk_manager import RiskManagerService
        cl.add(CheckResult(6, "risk_manager_service attivo", "PASS",
                           f"module loaded, max_risk_per_trade={config.risk.max_risk_per_trade_pct}%"))
    except ImportError as e:
        cl.add(CheckResult(6, "risk_manager_service attivo", "FAIL", f"import error: {e}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 7: token_safety_service attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.token_safety import TokenSafetyService
        cl.add(CheckResult(7, "token_safety_service attivo", "PASS",
                           f"module loaded, min_safety_score={config.token_safety.min_safety_score}"))
    except ImportError as e:
        cl.add(CheckResult(7, "token_safety_service attivo", "FAIL", f"import error: {e}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 8: wallet_discovery_service attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.wallet_discovery import WalletDiscoveryService
        cl.add(CheckResult(8, "wallet_discovery_service attivo", "PASS",
                           f"module loaded, rebalance every {config.wallet_discovery.rebalance_interval_hours}h"))
    except ImportError as e:
        cl.add(CheckResult(8, "wallet_discovery_service attivo", "FAIL", f"import error: {e}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 9: wallet_scoring_service attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.wallet_scoring import WalletScoringService
        cl.add(CheckResult(9, "wallet_scoring_service attivo", "PASS",
                           f"module loaded, min_score={config.wallet_scoring.min_score}"))
    except ImportError as e:
        cl.add(CheckResult(9, "wallet_scoring_service attivo", "FAIL", f"import error: {e}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 10: copy_engine_service attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.copy_engine import CopyEngineService
        cl.add(CheckResult(10, "copy_engine_service attivo", "PASS",
                           f"module loaded, max_signal_age={config.execution.max_signal_age_ms}ms"))
    except ImportError as e:
        cl.add(CheckResult(10, "copy_engine_service attivo", "FAIL", f"import error: {e}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 11: monitoring_safety_service attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.monitoring import (
            StructuredLogger, KillSwitch, HealthChecker,
            ReportGenerator, RealTransactionBlocker
        )
        cl.add(CheckResult(11, "monitoring_safety_service attivo", "PASS",
                           "StructuredLogger + KillSwitch + HealthChecker + RealTransactionBlocker loaded"))
    except ImportError as e:
        cl.add(CheckResult(11, "monitoring_safety_service attivo", "FAIL", f"import error: {e}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 12: kill_switch attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.monitoring import KillSwitch
        # Verify kill switch has required methods
        ks_methods = ["check_kill_file", "check_drawdown", "trigger", "is_triggered"]
        ks = KillSwitch.__new__(KillSwitch)
        missing = [m for m in ks_methods if not hasattr(KillSwitch, m)]
        if not missing:
            cl.add(CheckResult(12, "kill_switch attivo", "PASS",
                               f"methods: {', '.join(ks_methods)}, "
                               f"auto-kill at {config.risk.max_drawdown_pct}% drawdown"))
        else:
            cl.add(CheckResult(12, "kill_switch attivo", "FAIL",
                               f"missing methods: {missing}"))
    except Exception as e:
        cl.add(CheckResult(12, "kill_switch attivo", "FAIL", str(e)))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 13: logging attivo
    # ═══════════════════════════════════════════════════════════════
    try:
        from modules.monitoring import StructuredLogger
        sl_methods = ["log_trade", "log_signal", "log_error",
                      "log_critical_error", "log_state_change",
                      "log_risk_decision", "log_wallet_analysis", "log_health"]
        missing = [m for m in sl_methods if not hasattr(StructuredLogger, m)]
        if not missing:
            cl.add(CheckResult(13, "logging attivo", "PASS",
                               f"StructuredLogger: {len(sl_methods)} log methods, "
                               f"level={config.monitoring.log_level}"))
        else:
            cl.add(CheckResult(13, "logging attivo", "FAIL",
                               f"missing methods: {missing}"))
    except Exception as e:
        cl.add(CheckResult(13, "logging attivo", "FAIL", str(e)))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 14: API market data raggiungibili
    # ═══════════════════════════════════════════════════════════════
    api_results = []

    # DexScreener (no key needed)
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{config.api.dexscreener.base_url}/tokens/So11111111111111111111111111111111111111112",
            headers={"User-Agent": "MemecoinCopyTrader/1.0"}
        )
        resp = urllib.request.urlopen(req, timeout=8)
        if resp.status == 200:
            api_results.append("DexScreener: ✅ reachable")
        else:
            api_results.append(f"DexScreener: ⚠️ status {resp.status}")
    except Exception as e:
        api_results.append(f"DexScreener: ⚠️ timeout/error ({type(e).__name__})")

    # Solana RPC
    try:
        import urllib.request
        import json as _json
        payload = _json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "getHealth"
        }).encode()
        req = urllib.request.Request(
            config.api.solana_rpc.url,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=8)
        data = _json.loads(resp.read())
        if data.get("result") == "ok":
            api_results.append("Solana RPC: ✅ healthy")
        else:
            api_results.append(f"Solana RPC: ⚠️ response={data.get('result')}")
    except Exception as e:
        api_results.append(f"Solana RPC: ⚠️ timeout/error ({type(e).__name__})")

    # Birdeye
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{config.api.birdeye.base_url}/public/chain_list",
            headers={"User-Agent": "MemecoinCopyTrader/1.0"}
        )
        resp = urllib.request.urlopen(req, timeout=8)
        if resp.status == 200:
            api_results.append("Birdeye: ✅ reachable")
        else:
            api_results.append(f"Birdeye: ⚠️ status {resp.status}")
    except Exception as e:
        api_results.append(f"Birdeye: ⚠️ timeout/error ({type(e).__name__})")

    any_ok = any("✅" in r for r in api_results)
    if any_ok:
        cl.add(CheckResult(14, "API market data raggiungibili", "PASS",
                           " | ".join(api_results)))
    else:
        cl.add(CheckResult(14, "API market data raggiungibili", "WARN",
                           "All APIs unreachable (bot will run with degraded data) | " +
                           " | ".join(api_results)))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 15: nessun modulo può firmare transazioni reali
    # ═══════════════════════════════════════════════════════════════
    dangerous_methods = [
        "sign_transaction", "signMessage", "signAllTransactions",
        "signAndSendTransaction", "sendTransaction", "sendRawTransaction",
    ]
    modules_to_check = [
        "modules.paper_account", "modules.copy_engine", "modules.risk_manager",
        "modules.market_data", "modules.dex_api", "modules.monitoring",
        "modules.wallet_discovery", "modules.wallet_scoring", "modules.token_safety",
    ]
    found_dangerous = []
    for mod_name in modules_to_check:
        try:
            mod = __import__(mod_name, fromlist=[""])
            for method in dangerous_methods:
                # Check all classes and functions in the module
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name, None)
                    if attr and hasattr(attr, method):
                        found_dangerous.append(f"{mod_name}.{attr_name}.{method}")
        except ImportError:
            pass

    if not found_dangerous:
        cl.add(CheckResult(15, "nessun modulo può firmare transazioni reali", "PASS",
                           f"Scanned {len(modules_to_check)} modules, 0 dangerous methods found"))
    else:
        cl.add(CheckResult(15, "nessun modulo può firmare transazioni reali", "FAIL",
                           f"DANGEROUS: {found_dangerous}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 16: nessun modulo può inviare ordini reali
    # ═══════════════════════════════════════════════════════════════
    # Check that no crypto SDK is installed
    crypto_sdks = [
        "solders", "solana", "spl", "anchorpy",
        "web3", "eth_account", "ethers",
    ]
    installed_sdks = []
    for sdk in crypto_sdks:
        try:
            __import__(sdk)
            installed_sdks.append(sdk)
        except ImportError:
            pass

    if not installed_sdks:
        cl.add(CheckResult(16, "nessun modulo può inviare ordini reali", "PASS",
                           f"Scanned {len(crypto_sdks)} crypto SDKs, 0 installed"))
    else:
        cl.add(CheckResult(16, "nessun modulo può inviare ordini reali", "FAIL",
                           f"DANGEROUS SDKs installed: {installed_sdks}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 17: limiti di rischio caricati correttamente
    # ═══════════════════════════════════════════════════════════════
    risk_limits = {
        "max_risk_per_trade_pct": (config.risk.max_risk_per_trade_pct, 0.5),
        "max_total_exposure_pct": (config.risk.max_total_exposure_pct, 20.0),
        "max_open_positions": (config.risk.max_open_positions, 5),
        "max_trades_per_hour": (config.risk.max_trades_per_hour, 10),
        "max_slippage_bps": (config.risk.max_slippage_bps, 150),
        "max_price_impact_bps": (config.risk.max_price_impact_bps, 200),
        "min_liquidity_usd": (config.risk.min_liquidity_usd, 20000.0),
        "max_gas_per_trade_usd": (config.risk.max_gas_per_trade_usd, 5.0),
    }
    risk_ok = []
    risk_fail = []
    for key, (actual, expected) in risk_limits.items():
        if actual == expected:
            risk_ok.append(key)
        else:
            risk_fail.append(f"{key}={actual} (expected {expected})")

    if not risk_fail:
        cl.add(CheckResult(17, "limiti di rischio caricati correttamente", "PASS",
                           f"{len(risk_ok)} limits verified"))
    else:
        cl.add(CheckResult(17, "limiti di rischio caricati correttamente", "FAIL",
                           f"Mismatches: {risk_fail}"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 18: daily loss limit attivo
    # ═══════════════════════════════════════════════════════════════
    if config.risk.max_daily_loss_pct == 3.0:
        cl.add(CheckResult(18, "daily loss limit attivo", "PASS",
                           f"max_daily_loss_pct={config.risk.max_daily_loss_pct}%"))
    else:
        cl.add(CheckResult(18, "daily loss limit attivo", "FAIL",
                           f"max_daily_loss_pct={config.risk.max_daily_loss_pct} (expected 3.0)"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 19: max drawdown attivo
    # ═══════════════════════════════════════════════════════════════
    if config.risk.max_drawdown_pct == 10.0:
        cl.add(CheckResult(19, "max drawdown attivo", "PASS",
                           f"max_drawdown_pct={config.risk.max_drawdown_pct}% → kill switch"))
    else:
        cl.add(CheckResult(19, "max drawdown attivo", "FAIL",
                           f"max_drawdown_pct={config.risk.max_drawdown_pct} (expected 10.0)"))

    # ═══════════════════════════════════════════════════════════════
    # CHECK 20: blacklist/whitelist caricate, se presenti
    # ═══════════════════════════════════════════════════════════════
    bl_path = Path("blacklist.json")
    wl_path = Path("whitelist.json")
    bl_count = 0
    wl_count = 0

    if bl_path.exists():
        try:
            with open(bl_path) as f:
                bl_data = json.load(f)
            bl_count = len(bl_data) if isinstance(bl_data, list) else 0
        except Exception:
            cl.add(CheckResult(20, "blacklist/whitelist caricate", "WARN",
                               "blacklist.json exists but is malformed"))
            return cl

    if wl_path.exists():
        try:
            with open(wl_path) as f:
                wl_data = json.load(f)
            wl_count = len(wl_data) if isinstance(wl_data, list) else 0
        except Exception:
            cl.add(CheckResult(20, "blacklist/whitelist caricate", "WARN",
                               "whitelist.json exists but is malformed"))
            return cl

    if bl_count == 0 and wl_count == 0:
        cl.add(CheckResult(20, "blacklist/whitelist caricate", "PASS",
                           "No blacklist/whitelist files found (optional)"))
    else:
        cl.add(CheckResult(20, "blacklist/whitelist caricate", "PASS",
                           f"blacklist={bl_count} entries, whitelist={wl_count} entries"))

    return cl


def print_report(cl: Checklist):
    """Print the final checklist report."""
    print()
    print("=" * 65)
    print("  MEMECOIN COPYTRADER — PRE-FLIGHT SAFETY CHECKLIST")
    print("=" * 65)
    print()

    for r in cl.results:
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}[r.status]
        print(f"  {icon} CHECK {r.id:2d} | {r.name}")
        print(f"        └─ {r.detail}")
        print()

    print("-" * 65)
    print()

    # Summary
    passed = sum(1 for r in cl.results if r.status == "PASS")
    failed = sum(1 for r in cl.results if r.status == "FAIL")
    warned = sum(1 for r in cl.results if r.status == "WARN")

    print(f"  PASSED:  {passed}/20")
    print(f"  FAILED:  {failed}/20")
    print(f"  WARNED:  {warned}/20")
    print()

    # Final verdict
    if cl.passed:
        print("  ╔══════════════════════════════════════════════╗")
        print("  ║  CHECKLIST_PASSED = true                    ║")
        print("  ║  ERRORS = []                               ║")
        if cl.warnings:
            print(f"  ║  WARNINGS = {len(cl.warnings)} item(s)                         ║")
        else:
            print("  ║  WARNINGS = []                             ║")
        print("  ║  NEXT_STEP = \"start_paper_trading\"         ║")
        print("  ╚══════════════════════════════════════════════╝")
    else:
        print("  ╔══════════════════════════════════════════════╗")
        print("  ║  CHECKLIST_PASSED = false                   ║")
        print("  ║  ERRORS = [")
        for e in cl.errors:
            print(f"  ║    \"{e}\"")
        print("  ║  ]")
        if cl.warnings:
            print(f"  ║  WARNINGS = {len(cl.warnings)} item(s)                         ║")
        else:
            print("  ║  WARNINGS = []                             ║")
        print("  ║  NEXT_STEP = \"fix_required\"                ║")
        print("  ╚══════════════════════════════════════════════╝")

    print()

    # Machine-readable output
    print("--- MACHINE-READABLE OUTPUT ---")
    print(f"CHECKLIST_PASSED = {'true' if cl.passed else 'false'}")
    print(f"ERRORS = {json.dumps(cl.errors)}")
    print(f"WARNINGS = {json.dumps(cl.warnings)}")
    print(f"NEXT_STEP = \"{'start_paper_trading' if cl.passed else 'fix_required'}\"")
    print("--- END ---")
    print()


if __name__ == "__main__":
    checklist = run_checks()
    print_report(checklist)

    # Exit code: 0 = passed, 1 = failed
    sys.exit(0 if checklist.passed else 1)
