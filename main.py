#!/usr/bin/env python3
"""
Binance Scalping Bot - Main Entry Point
"""

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
    logger.add(sys.stdout,
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
               level="INFO")
    os.makedirs("logs", exist_ok=True)
    logger.add("logs/bot_{time:YYYY-MM-DD}.log",
               rotation="1 day", retention="7 days", level="DEBUG")


async def main():
    setup_logging()
    config = Config.load()

    logger.info("🚀 Avvio Binance Scalping Bot")
    logger.info(f"🎯 Target giornaliero: {config.daily_profit_target_pct*100:.0f}%")

    bot = ScalpingBot(config)
    api = APIServer(bot, config)

    # Avvia API server PRIMA del bot (Render richiede risposta immediata sulla porta)
    await api.start()
    logger.info(f"📱 API Server avviato sulla porta {api.port}")
    logger.info(f"📱 Testa: https://scalpbot-o2k5.onrender.com/status")

    def shutdown(sig, frame):
        logger.warning("🛑 Stop...")
        asyncio.create_task(bot.stop())
        asyncio.create_task(api.stop())

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Avvia bot in background, API server rimane in ascolto
    bot_task = asyncio.create_task(bot.start())

    try:
        await bot_task
    except Exception as e:
        logger.error(f"Errore: {e}")
    finally:
        await api.stop()


if __name__ == "__main__":
    asyncio.run(main())
