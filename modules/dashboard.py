"""
Memecoin CopyTrader — Live Web Dashboard
==========================================
Access from phone: http://YOUR_IP:8080
"""

import asyncio
import json
import time
import sys
import os
from pathlib import Path
from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Memecoin CopyTrader</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,sans-serif; background:#0a0a1a; color:#e0e0e0; padding:10px; max-width:600px; margin:0 auto; }
h1 { color:#00ff88; font-size:18px; text-align:center; padding:10px 0; border-bottom:1px solid #222; }
.status-bar { display:flex; justify-content:space-between; padding:8px; background:#111; border-radius:8px; margin:10px 0; font-size:12px; }
.status-dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:4px; }
.dot-green { background:#00ff88; }
.dot-red { background:#ff4444; }
.dot-yellow { background:#ffaa00; }
.card { background:#111; border-radius:10px; padding:12px; margin:8px 0; border:1px solid #222; }
.card h2 { color:#00aaff; font-size:14px; margin-bottom:8px; }
.metric { display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #1a1a2a; font-size:13px; }
.metric:last-child { border:none; }
.metric .label { color:#888; }
.metric .value { font-weight:bold; }
.positive { color:#00ff88; }
.negative { color:#ff4444; }
.neutral { color:#ffaa00; }
.trade-row { display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid #1a1a2a; font-size:12px; }
.btn { width:100%; padding:12px; border:none; border-radius:8px; font-size:16px; font-weight:bold; cursor:pointer; margin:4px 0; }
.btn-kill { background:#ff4444; color:white; }
.btn-kill:hover { background:#cc0000; }
.btn-refresh { background:#222; color:#00ff88; border:1px solid #00ff88; }
.positions-list { max-height:200px; overflow-y:auto; }
.empty { color:#555; text-align:center; padding:20px; font-style:italic; }
</style>
</head>
<body>
<h1>🤖 Memecoin CopyTrader</h1>

<div class="status-bar">
  <span><span class="status-dot dot-green" id="statusDot"></span><span id="statusText">PAPER TRADING</span></span>
  <span id="uptime">0h</span>
</div>

<div class="card">
  <h2>💰 Portfolio</h2>
  <div class="metric"><span class="label">Balance</span><span class="value" id="balance">$0.00</span></div>
  <div class="metric"><span class="label">Positions</span><span class="value" id="positions">0</span></div>
  <div class="metric"><span class="label">Exposure</span><span class="value" id="exposure">0%</span></div>
  <div class="metric"><span class="label">Trades today</span><span class="value" id="tradesToday">0</span></div>
  <div class="metric"><span class="label">Real tx sent</span><span class="value positive">0 ✓</span></div>
</div>

<div class="card">
  <h2>📊 Positions</h2>
  <div class="positions-list" id="positionsList">
    <div class="empty">No positions</div>
  </div>
</div>

<div class="card">
  <h2>📜 Recent Trades</h2>
  <div id="tradesList">
    <div class="empty">No trades yet</div>
  </div>
</div>

<div class="card">
  <h2>🎯 Copy Targets</h2>
  <div id="targetsList">
    <div class="empty">Scanning...</div>
  </div>
</div>

<div class="card">
  <h2>🛡️ Kill Switch</h2>
  <button class="btn btn-kill" onclick="killSwitch()">⚡ KILL SWITCH</button>
</div>

<button class="btn btn-refresh" onclick="location.reload()">🔄 Refresh</button>

<script>
const API = window.location.origin + '/api';
let startTime = Date.now();

function fmt(n,d=2){return '$'+parseFloat(n||0).toFixed(d);}
function pct(n){return parseFloat(n||0).toFixed(1)+'%';}
function cls(n){return parseFloat(n)>=0?'positive':'negative';}

async function refresh(){
  try{
    const r = await fetch(API+'/status');
    const d = await r.json();

    document.getElementById('balance').textContent = fmt(d.balance);
    document.getElementById('balance').className = 'value ' + cls(d.daily_pnl);
    document.getElementById('positions').textContent = d.positions;
    document.getElementById('exposure').textContent = pct(d.exposure);
    document.getElementById('tradesToday').textContent = d.trades_today;

    const up = Math.floor((Date.now()-startTime)/1000/3600);
    document.getElementById('uptime').textContent = up+'h';

    // Positions
    const pl = document.getElementById('positionsList');
    if(d.position_list && d.position_list.length > 0){
      pl.innerHTML = d.position_list.map(p=>`
        <div class="trade-row">
          <span>${p.name}</span>
          <span class="${cls(p.pnl)}">${fmt(p.pnl)}</span>
        </div>`).join('');
    } else {
      pl.innerHTML = '<div class="empty">No positions</div>';
    }

    // Trades
    const tl = document.getElementById('tradesList');
    if(d.recent_trades && d.recent_trades.length > 0){
      tl.innerHTML = d.recent_trades.map(t=>`
        <div class="trade-row">
          <span>${t.token}</span>
          <span>${t.side}</span>
          <span>${t.qty}</span>
          <span class="${cls(t.pnl)}">${fmt(t.pnl)}</span>
        </div>`).join('');
    }

    // Targets
    const tgt = document.getElementById('targetsList');
    if(d.copy_targets && d.copy_targets.length > 0){
      tgt.innerHTML = d.copy_targets.map(t=>`
        <div class="trade-row">
          <span>${t.name} (${t.symbol})</span>
          <span>score=${t.score}</span>
          <span class="${t.safe?'positive':'negative'}">${t.safe?'✅':'⚠️'}</span>
        </div>`).join('');
    }

  }catch(e){console.error(e);}
}

async function killSwitch(){
  if(!confirm('KILL SWITCH? Il bot si ferm immediatamente.')) return;
  try{
    const r = await fetch(API+'/kill',{method:'POST'});
    const d = await r.json();
    alert(d.message || 'Kill switch attivato!');
    document.getElementById('statusDot').className = 'status-dot dot-red';
    document.getElementById('statusText').textContent = 'KILLED';
  }catch(e){alert('Errore: '+e);}
}

setInterval(refresh, 5000);
refresh();
</script>
</body>
</html>"""


class WebDashboard:
    def __init__(self, host="0.0.0.0", port=8080):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/api/status", self.api_status)
        self.app.router.add_post("/api/kill", self.api_kill)
        self.app.router.add_get("/api/trades", self.api_trades)
        self.app.router.add_get("/api/positions", self.api_positions)
        self.runner = None
        self.state = {
            "balance": 25.0,
            "positions": 0,
            "exposure": 0.0,
            "trades_today": 0,
            "position_list": [],
            "recent_trades": [],
            "copy_targets": [],
            "daily_pnl": 0.0,
            "kill_active": False,
        }
        self.kill_callback = None

    def update(self, **kwargs):
        self.state.update(kwargs)

    async def index(self, request):
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    async def api_status(self, request):
        return web.json_response(self.state)

    async def api_kill(self, request):
        self.state["kill_active"] = True
        if self.kill_callback:
            await self.kill_callback()
        return web.json_response({"status": "killed", "message": "Kill switch attivato!"})

    async def api_trades(self, request):
        return web.json_response(self.state.get("recent_trades", []))

    async def api_positions(self, request):
        return web.json_response(self.state.get("position_list", []))

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        print(f"  🌐 Dashboard: http://0.0.0.0:{self.port}")
        print(f"  🌐 From phone: http://YOUR_PHONE_IP:{self.port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
