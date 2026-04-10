# api_server.py -- ScalpBot
# Master endpoints protected by token.

import asyncio
import os
import time
import sqlite3
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
        self.port = int(os.environ.get("PORT", 10000))
        self._app = web.Application()
        self._runner = None
        self._log_buffer = []

    def add_log(self, type_: str, msg: str):
        now = datetime.now().strftime("%H:%M:%S")
        self._log_buffer.append({"type": type_, "msg": msg, "time": now})
        if len(self._log_buffer) > 100:
            self._log_buffer.pop(0)

    async def start(self):
        r = self._app.router
        r.add_get('/',                self._root)
        r.add_get('/health',          self._health)
        r.add_get('/status',          self._status)
        r.add_post('/command',        self._command)
        r.add_get('/master/report',   self._master_report)
        r.add_get('/master/stats',    self._master_stats)
        r.add_get('/master/export',   self._master_export)
        r.add_get('/master/snapshot', self._master_snapshot)
        self._app.middlewares.append(self._cors)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        await web.TCPSite(self._runner, '0.0.0.0', self.port).start()
        logger.info(f"API Server on port {self.port}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    def _is_master(self, request) -> bool:
        token = request.headers.get("X-Master-Token") or request.rel_url.query.get("token")
        return token == MASTER_TOKEN

    @web.middleware
    async def _cors(self, request, handler):
        if request.method == 'OPTIONS':
            return web.Response(headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, X-Master-Token',
            })
        resp = await handler(request)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Master-Token'
        return resp

    async def _root(self, _):
        return web.json_response({"ok": True, "service": "ScalpBot Pro", "version": "2.1"})

    async def _health(self, _):
        return web.json_response({"ok": True})

    async def _status(self, request):
        rm = self.bot.risk_manager
        om = self.bot.order_manager

        # Open trades
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
                    "symbol":   trade.pair,
                    "side":     trade.side,
                    "market":   trade.market,
                    "pnl":      round(pnl, 4),
                    "entry":    trade.entry_price,
                    "current":  current,
                    "tp_price": trade.tp_price,
                    "sl_price": trade.sl_price,
                    "pattern":  trade.pattern,
                    "elapsed":  round(time.time() - trade.opened_at),
                })
            except Exception:
                continue

        # Bot status
        if not self.bot.running:
            status = "stopped"
        elif rm.is_daily_target_hit():
            status = "target_hit"
        elif rm._target_hit:
            status = "paused"
        else:
            status = "running"

        # Strategy data per pair
        strategies = {}
        for pair, strat in self.bot.strategies.items():
            price  = strat.last_close or 0
            ema50  = strat.ema_trend.value
            dist_pct = round((price - ema50) / ema50 * 100, 3) if (price and ema50) else None
            trend  = "UP" if (price and ema50 and price > ema50) else "DOWN"

            missing = []
            if not strat._warmed_up:
                missing.append("Warming up...")
            elif len(strat.candles) < 10:
                missing.append(f"Need {10 - len(strat.candles)} more candles")
            elif ema50 and price:
                if dist_pct is not None and abs(dist_pct) < 0.03:
                    missing.append("Too close to EMA50 (choppy)")
                elif trend == "UP":
                    missing.append("Waiting for LONG pattern")
                elif trend == "DOWN" and self.config.enable_futures:
                    missing.append("Waiting for SHORT pattern")
                else:
                    missing.append("Trend DOWN - spot only LONG blocked")

            strategies[pair] = {
                "price":     round(price, 4) if price else None,
                "ema200":    round(ema50, 4) if ema50 else None,
                "ema_fast":  round(strat.ema_fast.value, 4) if strat.ema_fast.value else None,
                "ema_slow":  round(strat.ema_slow.value, 4) if strat.ema_slow.value else None,
                "trend":     trend,
                "dist_pct":  dist_pct,
                "warmed_up": strat._warmed_up,
                "candles":   len(strat.candles),
                "missing":   missing,
                "ob_buy":    round(strat.bid_volume / (strat.bid_volume + strat.ask_volume) * 100, 1)
                             if (strat.bid_volume + strat.ask_volume) > 0 else 0,
            }

        # Active cooldowns
        cooldowns = {
            pair: int(until - time.time())
            for pair, until in rm._cooldowns.items()
            if time.time() < until
        }

        # Current params -- include max_trades and max_loss for app settings
        params = {
            "tp_pct":          round(self.config.take_profit_pct * 100, 3),
            "sl_pct":          round(self.config.stop_loss_pct * 100, 3),
            "futures_size":    self.config.futures_position_size_usdt,
            "spot_size":       self.config.position_size_usdt,
            "spot_tp_pct":     round(self.config.spot_take_profit_pct * 100, 3),
            "spot_sl_pct":     round(self.config.spot_stop_loss_pct * 100, 3),
            "spot_enabled":    self.config.enable_spot,
            "futures_enabled": self.config.enable_futures,
            "interval":        self.config.kline_interval,
            "max_trades":      self.config.max_open_trades,
            "max_loss":        self.config.max_daily_loss_usdt,
        }

        return web.json_response({
            "status":       status,
            "capital":      round(rm.daily_start_capital, 2),
            "daily_pnl":    round(rm.daily_pnl, 4),
            "wins":         rm.winning_trades,
            "losses":       rm.losing_trades,
            "trades":       trades,
            "active_pairs": list(self.bot.strategies.keys()),
            "target_pct":   self.config.daily_profit_target_pct * 100,
            "log":          self._log_buffer[-30:],
            "strategies":   strategies,
            "cooldowns":    cooldowns,
            "params":       params,
        })

    async def _command(self, request):
        try:
            body = await request.json()
            cmd  = body.get("command", "")
            logger.info(f"CMD: {cmd}")
            self.add_log("info", f"CMD: {cmd}")

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

            elif cmd == "set_market":
                market  = body.get("market", "spot")
                enabled = body.get("enabled", True)
                if market == "spot":
                    self.config.enable_spot = enabled
                    if enabled:
                        self.config.enable_futures = False
                        self.config.take_profit_pct = self.config.spot_take_profit_pct
                        self.config.stop_loss_pct   = self.config.spot_stop_loss_pct
                elif market == "futures":
                    self.config.enable_futures = enabled
                    if enabled:
                        self.config.enable_spot     = False
                        self.config.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT", "0.003"))
                        self.config.stop_loss_pct   = float(os.getenv("STOP_LOSS_PCT",   "0.0015"))
                save_state(self.config)
                status = "ON" if enabled else "OFF"
                self.add_log("info", f"{market.upper()} {status}")
                logger.info(f"CMD set_market: {market.upper()} {status} | spot={self.config.enable_spot} fut={self.config.enable_futures}")
                return web.json_response({"ok": True})

            elif cmd == "set_params":
                tp     = body.get("tp")
                sl     = body.get("sl")
                size   = body.get("size")
                market = body.get("market", "futures")

                if tp is not None:
                    self.config.take_profit_pct = float(tp)
                    if market == "spot":
                        self.config.spot_take_profit_pct = float(tp)
                if sl is not None:
                    self.config.stop_loss_pct = float(sl)
                    if market == "spot":
                        self.config.spot_stop_loss_pct = float(sl)
                if size is not None:
                    if market == "futures":
                        self.config.futures_position_size_usdt = float(size)
                    else:
                        self.config.position_size_usdt = float(size)

                save_state(self.config)
                self.add_log("info", f"[{market.upper()}] TP={self.config.take_profit_pct:.3%} SL={self.config.stop_loss_pct:.3%} size={size}")
                logger.info(f"CMD set_params [{market}] TP={self.config.take_profit_pct:.3%} SL={self.config.stop_loss_pct:.3%} size={size}")
                return web.json_response({"ok": True})

            elif cmd == "set_risk":
                # Supporta target, max_trades e max_loss tutti opzionali
                target     = body.get("target")
                max_trades = body.get("max_trades")
                max_loss   = body.get("max_loss")

                if target is not None:
                    self.config.daily_profit_target_pct = float(target) / 100
                    self.bot.risk_manager._target_hit   = False

                if max_trades is not None:
                    self.config.max_open_trades = int(max_trades)
                    # Aggiorna anche il risk manager a runtime
                    if hasattr(self.bot.risk_manager, 'max_open_trades'):
                        self.bot.risk_manager.max_open_trades = int(max_trades)

                if max_loss is not None:
                    self.config.max_daily_loss_usdt = float(max_loss)
                    if hasattr(self.bot.risk_manager, 'max_daily_loss_usdt'):
                        self.bot.risk_manager.max_daily_loss_usdt = float(max_loss)

                save_state(self.config)
                logger.info(f"CMD set_risk: max_trades={self.config.max_open_trades} max_loss={self.config.max_daily_loss_usdt}")
                return web.json_response({"ok": True})

            elif cmd == "reset_db":
                # Resetta lo stato del DB -- i valori torneranno alle env var al prossimo restart
                try:
                    db_path = os.environ.get("DB_PATH", "/data/scalpbot.db")
                    conn = sqlite3.connect(db_path)
                    conn.execute("DELETE FROM bot_state")
                    conn.commit()
                    conn.close()
                    logger.info("DB state reset via app command")
                    self.add_log("warn", "DB resettato - riavvia il bot")
                    return web.json_response({"ok": True, "msg": "DB resettato"})
                except Exception as e:
                    logger.error(f"reset_db error: {e}")
                    return web.json_response({"ok": False, "msg": str(e)}, status=500)

            return web.json_response({"ok": False, "msg": "Unknown command"})

        except Exception as e:
            logger.error(f"Command error: {e}")
            return web.json_response({"ok": False, "msg": str(e)}, status=500)

    # ?? MASTER ????????????????????????????????????????????????

    async def _master_report(self, request):
        if not self._is_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        report = db.get_daily_report(request.rel_url.query.get("date"))
        return web.json_response({"ok": True, "report": report})

    async def _master_stats(self, request):
        if not self._is_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        stats = db.get_overall_stats()
        rm = self.bot.risk_manager
        stats['current'] = {
            "capital":      round(rm.daily_start_capital, 2),
            "daily_pnl":    round(rm.daily_pnl, 4),
            "wins_today":   rm.winning_trades,
            "losses_today": rm.losing_trades,
            "running":      self.bot.running,
        }
        return web.json_response({"ok": True, "stats": stats})

    async def _master_export(self, request):
        if not self._is_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        return web.Response(
            text=db.export_csv(),
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="scalpbot_trades.csv"'}
        )

    async def _master_snapshot(self, request):
        if not self._is_master(request):
            return web.json_response({"ok": False, "msg": "Unauthorized"}, status=401)
        rm = self.bot.risk_manager
        db.save_snapshot(
            capital=rm.daily_start_capital,
            daily_pnl=rm.daily_pnl,
            wins=rm.winning_trades,
            losses=rm.losing_trades,
        )
        return web.json_response({"ok": True, "msg": "Snapshot saved"})
