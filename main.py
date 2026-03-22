#!/usr/bin/env python3
import asyncio
import signal
import sys
import os
from loguru import logger
from config import Config
from bot import ScalpingBot
from api_server import APIServer

def setup_logging():
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="INFO")
    os.makedirs("logs", exist_ok=True)

async def main():
    setup_logging()
    config = Config.load()
    port = int(os.environ.get("PORT", 10000))

    logger.info("🚀 Avvio Binance Scalping Bot")
    logger.info(f"🎯 Target: {config.daily_profit_target_pct*100:.0f}%")
    logger.info(f"🌐 Porta: {port}")

    bot = ScalpingBot(config)
    api = APIServer(bot, config)
    api.port = port

    await api.start()
    logger.info(f"✅ API Server attivo su 0.0.0.0:{port}")

    stop_event = asyncio.Event()

    async def shutdown():
        logger.warning("🛑 Shutdown in corso...")
        stop_event.set()
        await bot.stop()
        await api.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    bot_task = asyncio.create_task(bot.start())

    def on_bot_error(task):
        if not task.cancelled() and task.exception():
            logger.error(f"💥 Bot crashato: {task.exception()}")

    bot_task.add_done_callback(on_bot_error)

    await stop_event.wait()

if __name__ == "__main__":
    asyncio.run(main())
