"""
API Server - Espone lo stato del bot all'app iOS via HTTP.
Endpoint /master/* protetti da token per documentazione automatica.
"""

import asyncio
import os
from aiohttp import web
from loguru import logger
from datetime import datetime
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
                if trend == "DOWN":
                    diff = round(ema200 - price, 2)
                    missing.append(f"Prezzo deve salire +{diff} ({abs(dist_pct):.2f}%)")
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
        import time
        cooldowns = {}
        for pair, until in rm._cooldowns.items():
            remaining = int(until - time.time())
            if remaining > 0:
                cooldowns[pair] = remaining

        # Parametri attuali
        current_params = {
            "tp_pct": round(self.config.take_profit_pct * 100, 3),
            "sl_pct": round(self.config.stop_loss_pct * 100, 3),
            "timeout": self.config.order_timeout_sec,
            "spot_size": self.config.position_size_usdt,
            "futures_size": self.config.futures_position_size_usdt,
            "spot_enabled": self.config.enable_spot,
            "futures_enabled": self.config.enable_futures,
        }

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
            "log": self._log_buffer[-30:],
            "strategies": strategies_data,
            "cooldowns": cooldowns,
            "params": current_params,
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

            elif cmd == "set_market":
                market = body.get("market", "spot")
                enabled = body.get("enabled", True)
                if market == "spot":
                    self.config.enable_spot = enabled
                elif market == "futures":
                    self.config.enable_futures = enabled
                logger.info(f"📱 {market.upper()} {'attivato' if enabled else 'disattivato'}")
                return web.json_response({"ok": True})

            elif cmd == "set_params":
                tp      = body.get("tp")
                sl      = body.get("sl")
                timeout = body.get("timeout")
                size    = body.get("size")
                market  = body.get("market", "futures")

                if tp is not None:
                    self.config.take_profit_pct = float(tp)
                if sl is not None:
                    self.config.stop_loss_pct = float(sl)
                if timeout is not None:
                    self.config.order_timeout_sec = int(timeout)
                if size is not None:
                    if market == "futures":
                        self.config.futures_position_size_usdt = float(size)
                    else:
                        self.config.position_size_usdt = float(size)

                logger.info(
                    f"📱 Params → TP={self.config.take_profit_pct:.3%} "
                    f"SL={self.config.stop_loss_pct:.3%} "
                    f"timeout={self.config.order_timeout_sec}s "
                    f"size={size}$"
                )
                self.add_log("info", f"⚙️ Params: TP={self.config.take_profit_pct:.2%} SL={self.config.stop_loss_pct:.2%}")
                return web.json_response({"ok": True})

            return web.json_response({"ok": False, "msg": "Comando sconosciuto"})

        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e)}, status=500)

    # ── MASTER ENDPOINTS ─────────────────────────────────────────────

    async def _handle_master_report(self, request):
        if not self._check_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        target_date = request.rel_url.query.get("date", None)
        report = db.get_daily_report(target_date)
        return web.json_response({"ok": True, "report": report})

    async def _handle_master_stats(self, request):
        if not self._check_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        stats = db.get_overall_stats()
        rm = self.bot.risk_manager
        stats['current'] = {
            "capital": round(rm.daily_start_capital, 2),
            "daily_pnl": round(rm.daily_pnl, 4),
            "wins_today": getattr(rm, 'winning_trades', 0),
            "losses_today": getattr(rm, 'losing_trades', 0),
            "running": self.bot.running,
        }
        return web.json_response({"ok": True, "stats": stats})

    async def _handle_master_export(self, request):
        if not self._check_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        csv = db.export_csv()
        return web.Response(
            text=csv,
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="scalpbot_trades.csv"'}
        )

    async def _handle_master_snapshot(self, request):
        if not self._check_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        rm = self.bot.risk_manager
        db.save_snapshot(
            capital=rm.daily_start_capital,
            daily_pnl=rm.daily_pnl,
            wins=getattr(rm, 'winning_trades', 0),
            losses=getattr(rm, 'losing_trades', 0),
        )
        return web.json_response({"ok": True, "msg": "Snapshot salvato"})

