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
    logger.info(f"✅ API Server su porta {port}")

    def shutdown(sig, frame):
        logger.warning("🛑 Stop...")
        asyncio.create_task(bot.stop())
        asyncio.create_task(api.stop())

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    bot_task = asyncio.create_task(bot.start())

    try:
        await bot_task
    except Exception as e:
        logger.error(f"Errore: {e}")
    finally:
        await api.stop()


if __name__ == "__main__":
    asyncio.run(main())
