import os
import asyncio
import signal
from aiohttp import web
from loguru import logger

from config import Config
from exchange import BinanceClient, DataFeed


class BotApp:
    def __init__(self):
        self.config = Config.load()
        self.client = BinanceClient(self.config)
        self.feed = DataFeed(self.client, self.config)
        self._stop_event = asyncio.Event()
        self._runner = None

    async def on_kline(self, pair, data):
        pass

    async def on_orderbook(self, pair, data):
        pass

    async def on_trade(self, pair, data):
        pass

    async def start_bot(self):
        logger.info("🚀 Starting bot...")
        await self.client.sync_clock()

        pairs = self.config.pairs
        await self.client.set_leverage_all(pairs, self.config.futures_leverage)
        await self.feed.start(pairs, self.on_kline, self.on_orderbook, self.on_trade)

    async def stop_bot(self):
        logger.warning("🛑 Stopping bot...")
        try:
            await self.feed.stop()
        except Exception as e:
            logger.warning(f"Feed stop warning: {e}")

        try:
            await self.client.close()
        except Exception as e:
            logger.warning(f"Client close warning: {e}")

        logger.info("✅ Bot stopped cleanly.")

    async def health(self, request):
        return web.Response(text="OK")

    async def start_http_server(self):
        app = web.Application()
        app.router.add_get("/", self.health)
        app.router.add_get("/health", self.health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        port = int(os.getenv("PORT", "10000"))
        site = web.TCPSite(self._runner, "0.0.0.0", port)
        await site.start()

        logger.info(f"🌐 Health server listening on 0.0.0.0:{port}")

    async def stop_http_server(self):
        if self._runner:
            await self._runner.cleanup()

    def _handle_signal(self):
        logger.warning("🛑 SIGTERM ricevuto — shutdown...")
        self._stop_event.set()

    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._handle_signal)
            except NotImplementedError:
                pass

        await self.start_http_server()
        await self.start_bot()

        await self._stop_event.wait()

        logger.warning("🛑 Shutdown in corso...")
        await self.stop_bot()
        await self.stop_http_server()


async def main():
    app = BotApp()
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
