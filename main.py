#!/usr/bin/env python3
"""
Binance Scalping Bot - Main Entry Point
Avvia il bot + API server per controllo da iPhone
"""

import asyncio
import signal
import sys
from loguru import logger
from config import Config
from bot import ScalpingBot
from api_server import APIServer


def setup_logging():
    logger.remove()
    logger.add(sys.stdout,
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
               level="INFO")
    import os
    os.makedirs("logs", exist_ok=True)
    logger.add("logs/bot_{time:YYYY-MM-DD}.log",
               rotation="1 day", retention="7 days", level="DEBUG")


async def main():
    setup_logging()
    config = Config.load()

    logger.info("🚀 Avvio Binance Scalping Bot")
    logger.info(f"🎯 Target giornaliero: {config.daily_profit_target_pct*100:.0f}%")
    logger.info(f"🛡️  Stop loss giornaliero: {config.max_daily_loss_usdt} USDT")

    bot = ScalpingBot(config)
    api = APIServer(bot, config)

    await api.start()

    try:
        import socket
        local_ip = socket.gethostbyname(socket.gethostname())
        logger.info(f"📱 App iPhone → apri: http://{local_ip}:8080")
        logger.info(f"📱 Inserisci IP nell'app: {local_ip}")
    except Exception:
        logger.info("📱 Trova il tuo IP con: ipconfig getifaddr en0")

    def shutdown(sig, frame):
        logger.warning("🛑 Stop...")
        asyncio.create_task(bot.stop())
        asyncio.create_task(api.stop())

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        await bot.start()
    except Exception as e:
        logger.error(f"Errore: {e}")
        raise
    finally:
        await api.stop()


if __name__ == "__main__":
    asyncio.run(main())
