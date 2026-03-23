"""
API Server - Espone lo stato del bot all'app iOS via HTTP.
"""

import asyncio
import os
from aiohttp import web
from loguru import logger
from datetime import datetime


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
        if len(self._log_buffer) > 50:
            self._log_buffer.pop(0)

    async def start(self):
        self._app.router.add_get('/',         self._handle_root)
        self._app.router.add_get('/status',   self._handle_status)
        self._app.router.add_post('/command', self._handle_command)
        self._app.router.add_get('/health',   self._handle_health)
        self._app.middlewares.append(self._cors_middleware)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '0.0.0.0', self.port)
        await site.start()
        logger.info(f"📱 API Server su porta {self.port}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _cors_middleware(self, request, handler):
        if request.method == 'OPTIONS':
            return web.Response(headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            })
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    async def _handle_root(self, request):
        return web.json_response({"ok": True, "service": "ScalpBot", "status": "running"})

    async def _handle_health(self, request):
        return web.json_response({"ok": True})

    async def _handle_status(self, request):
        rm = self.bot.risk_manager
        om = self.bot.order_manager

        trades = []
        for key, trade in list(om.trades.items()):  # fix: list() evita RuntimeError
            try:
                ticker = await self.bot.client.get_ticker(trade.pair, trade.market)
                current = float(ticker["price"]) if ticker else trade.entry_price
                pnl = (current - trade.entry_price) * trade.qty if trade.side == "LONG" else (trade.entry_price - current) * trade.qty
                trades.append({
                    "symbol": trade.pair,
                    "side": trade.side,
                    "market": trade.market,
                    "pnl": round(pnl, 4),
                    "entry": trade.entry_price,
                })
            except Exception:
                continue

        if not self.bot.running:
            status = "stopped"
        elif rm.is_daily_target_hit():
            status = "target_hit"
        elif rm._target_hit:
            status = "paused"
        else:
            status = "running"

        return web.json_response({
            "status": status,
            "capital": round(rm.daily_start_capital, 2),
            "daily_pnl": round(rm.daily_pnl, 4),
            "wins": getattr(rm, 'winning_trades', 0),
            "losses": getattr(rm, 'losing_trades', 0),
            "trades": trades,
            "active_pairs": list(self.bot.strategies.keys()),
            "target_pct": self.config.daily_profit_target_pct * 100,
            "risk_mode": "high" if self.config.daily_profit_target_pct >= 0.15 else "low",
            "log": self._log_buffer[-20:],
        })

    async def _handle_command(self, request):
        try:
            body = await request.json()
            cmd = body.get("command", "")
            logger.info(f"📱 Comando: {cmd}")
            self.add_log("info", f"📱 App: {cmd}")

            if cmd == "stop":
                asyncio.create_task(self.bot.stop())
                return web.json_response({"ok": True})
            elif cmd == "start":
                if not self.bot.running:
                    asyncio.create_task(self.bot.start())
                return web.json_response({"ok": True})
            elif cmd == "pause":
                self.bot.risk_manager._target_hit = True
                return web.json_response({"ok": True})
            elif cmd == "restart":
                asyncio.create_task(self.bot.stop())
                await asyncio.sleep(2)
                asyncio.create_task(self.bot.start())
                return web.json_response({"ok": True})
            elif cmd == "emergency_stop":
                await self.bot.order_manager.close_all_positions()
                return web.json_response({"ok": True})
            elif cmd == "set_risk":
                level = body.get("value", "high")
                target = body.get("target", 20) / 100
                self.config.daily_profit_target_pct = target
                self.bot.risk_manager._target_hit = False
                self.bot.risk_manager.daily_pnl = 0
                return web.json_response({"ok": True})

            return web.json_response({"ok": False, "msg": "Comando sconosciuto"})
        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e)}, status=500)



