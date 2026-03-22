#!/usr/bin/env python3
import asyncio
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

    # API server parte PRIMA e rimane sempre su
    await api.start()
    logger.info(f"✅ API Server attivo su 0.0.0.0:{port}")

    loop = asyncio.get_running_loop()

    # Shutdown gestito correttamente dentro asyncio
    async def shutdown():
        logger.warning("🛑 Shutdown in corso...")
        await bot.stop()
        await api.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    # Bot gira in background — se crasha, l'API rimane viva
    bot_task = asyncio.create_task(bot.start())

    def on_bot_error(task):
        if task.exception():
            logger.error(f"💥 Bot crashato: {task.exception()}")
            logger.info("🔄 API server rimane attivo per debug")

    bot_task.add_done_callback(on_bot_error)

    # Tieni vivo il processo finché l'API server gira
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        await shutdown()

if __name__ == "__main__":
    import signal
    asyncio.run(main())
