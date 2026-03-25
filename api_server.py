"""
API Server - Espone lo stato del bot all'app iOS via HTTP.
Endpoint /master/* protetti da token per documentazione automatica.
"""

import asyncio
import os
import time
from aiohttp import web
from loguru import logger
from datetime import datetime
from config import save_state
import database as db


MASTER_TOKEN = os.environ.get("MASTER_TOKEN", "scalpbot_master_2024")


class APIServer:
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.port = int(os.environ.get("PORT", 8080))
        self._app = web.Application()
        self._runner = None
        self._log_buffer = []

    def add_log(self, type_: str, msg: str):
        now = datetime.now().strftime("%H:%M:%S")
        self._log_buffer.append({"type": type_, "msg": msg, "time": now})
        if len(self._log_buffer) > 100:
            self._log_buffer.pop(0)

    async def start(self):
        self._app.router.add_get('/',                self._handle_root)
        self._app.router.add_get('/health',          self._handle_health)
        self._app.router.add_get('/status',          self._handle_status)
        self._app.router.add_post('/command',        self._handle_command)
        self._app.router.add_get('/master/report',   self._handle_master_report)
        self._app.router.add_get('/master/stats',    self._handle_master_stats)
        self._app.router.add_get('/master/export',   self._handle_master_export)
        self._app.router.add_get('/master/snapshot', self._handle_master_snapshot)
        self._app.middlewares.append(self._cors_middleware)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '0.0.0.0', self.port)
        await site.start()
        logger.info(f"📱 API Server su porta {self.port}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    def _check_master(self, request) -> bool:
        token = request.headers.get("X-Master-Token") or request.rel_url.query.get("token")
        return token == MASTER_TOKEN

    @web.middleware
    async def _cors_middleware(self, request, handler):
        if request.method == 'OPTIONS':
            return web.Response(headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, X-Master-Token',
            })
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Master-Token'
        return response

    async def _handle_root(self, request):
        return web.json_response({"ok": True, "service": "ScalpBot", "status": "running"})

    async def _handle_health(self, request):
        return web.json_response({"ok": True})

    async def _handle_status(self, request):
        rm = self.bot.risk_manager
        om = self.bot.order_manager

        # Posizioni aperte con PnL live
        trades = []
        for key, trade in list(om.trades.items()):
            try:
                ticker = await self.bot.client.get_ticker(trade.pair, trade.market)
                current = float(ticker["price"]) if ticker else trade.entry_price
                if trade.side == "LONG":
                    pnl = (current - trade.entry_price) * trade.qty
                else:
                    pnl = (trade.entry_price - current) * trade.qty
                trades.append({
                    "symbol": trade.pair,
                    "side": trade.side,
                    "market": trade.market,
                    "pnl": round(pnl, 4),
                    "entry": trade.entry_price,
                    "current": current,
                })
            except Exception:
                continue

        # Status bot
        if not self.bot.running:
            status = "stopped"
        elif rm.is_daily_target_hit():
            status = "target_hit"
        elif rm._target_hit:
            status = "paused"
        else:
            status = "running"

        # Dati strategia in tempo reale
        strategies_data = {}
        for pair, strat in self.bot.strategies.items():
            price = strat.last_close or 0
            ema200 = strat.ema_trend.value if strat.ema_trend.value else None
            ema_fast = strat.ema_fast.value if strat.ema_fast.value else None
            ema_slow = strat.ema_slow.value if strat.ema_slow.value else None

            dist_pct = None
            if price and ema200:
                dist_pct = round((price - ema200) / ema200 * 100, 3)

            trend = "UP" if (price and ema200 and price > ema200) else "DOWN"

            missing = []
            if not strat._warmed_up:
                missing.append("EMA200 non pronta")
            elif ema200 and price:
                if trend == "DOWN" and not self.config.enable_futures:
                    diff = round(ema200 - price, 2)
                    missing.append(f"Prezzo deve salire +{diff} ({abs(dist_pct):.2f}%)")
                elif trend == "DOWN" and self.config.enable_futures:
                    missing.append("SHORT futures — attesa segnale OB")
                else:
                    if ema_fast and ema_slow and ema_fast <= ema_slow:
                        missing.append("EMA9 deve superare EMA21")
                    total_ob = strat.bid_volume + strat.ask_volume
                    if total_ob > 0:
                        buy_ratio = strat.bid_volume / total_ob
                        if buy_ratio < self.config.ob_imbalance_threshold:
                            missing.append(f"OB buy {buy_ratio:.0%} < {self.config.ob_imbalance_threshold:.0%}")

            strategies_data[pair] = {
                "price": round(price, 2) if price else None,
                "ema_fast": round(ema_fast, 2) if ema_fast else None,
                "ema_slow": round(ema_slow, 2) if ema_slow else None,
                "ema200": round(ema200, 2) if ema200 else None,
                "trend": trend,
                "dist_pct": dist_pct,
                "warmed_up": strat._warmed_up,
                "missing": missing,
                "ob_buy": round(strat.bid_volume / (strat.bid_volume + strat.ask_volume) * 100, 1) if (strat.bid_volume + strat.ask_volume) > 0 else 0,
            }

        # Cooldown attivi
        cooldowns = {}
        for pair, until in rm._cooldowns.items():
            remaining = int(until - time.time())
            if remaining > 0:
                cooldowns[pair] = remaining

        # Parametri attuali
        current_params = {
            "tp_pct": round(self.config.take_profit_pct * 100, 3​​​​​​​​​​​​​​​​
