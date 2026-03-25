#!/usr/bin/env python3
import asyncio
import signal
import sys
import os
import time
from loguru import logger
from config import Config
from bot import ScalpingBot
from api_server import APIServer
import database as db

def setup_logging():
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="INFO")
    os.makedirs("logs", exist_ok=True)

async def snapshot_loop(bot, interval=3600):
    """Salva snapshot del PnL ogni ora."""
    while True:
        await asyncio.sleep(interval)
        try:
            rm = bot.risk_manager
            db.save_snapshot(
                capital=rm.daily_start_capital,
                daily_pnl=rm.daily_pnl,
                wins=getattr(rm, 'winning_trades', 0),
                losses=getattr(rm, 'losing_trades', 0),
            )
            logger.info(f"📸 Snapshot salvato | PnL: {rm.daily_pnl:+.4f} USDT")
        except Exception as e:
            logger.error(f"Snapshot error: {e}")

async def main():
    setup_logging()

    # Inizializza database
    db.init_db()

    config = Config.load()
    port = int(os.environ.get("PORT", 10000))

    logger.info("🚀 Avvio Binance Scalping Bot")
    logger.info(f"🎯 Target: disabilitato — bot perpetuo")
    logger.info(f"🌐 Porta: {port}")
    logger.info(f"📈 Spot: {'✅' if config.enable_spot else '❌'} | Futures: {'✅' if config.enable_futures else '❌'}")

    bot = ScalpingBot(config)
    api = APIServer(bot, config)
    api.port = port

    await api.start()
    logger.info(f"✅ API Server attivo su 0.0.0.0:{port}")

    stop_event = asyncio.Event()
    start_time = time.time()

    async def shutdown():
        logger.warning("🛑 Shutdown in corso...")
        stop_event.set()
        await bot.stop()
        await api.stop()

    def handle_sigterm():
        uptime = time.time() - start_time
        if uptime < 180:
            logger.warning(f"⚠️ SIGTERM ignorato (uptime {uptime:.1f}s — deploy rolling)")
            return
        logger.warning("🛑 SIGTERM ricevuto — shutdown...")
        asyncio.create_task(shutdown())

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT,  lambda: asyncio.create_task(shutdown()))
    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)

    bot_task = asyncio.create_task(bot.start())
    snapshot_task = asyncio.create_task(snapshot_loop(bot))

    def on_bot_error(task):
        if not task.cancelled() and task.exception():
            logger.error(f"💥 Bot crashato: {task.exception()}")
            logger.info("🔄 API server rimane attivo")

    bot_task.add_done_callback(on_bot_error)

    await stop_event.wait()
    snapshot_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
