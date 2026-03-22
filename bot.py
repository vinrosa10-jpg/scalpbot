"""
ScalpingBot - Main orchestrator
Manages WebSocket feeds, strategy signals, and order execution.
"""

import asyncio
from typing import Dict, List
from loguru import logger
from config import Config
from exchange import BinanceClient
from strategy import ScalpingStrategy
from risk_manager import RiskManager
from order_manager import OrderManager
from data_feed import DataFeed
from pair_selector import PairSelector
from eod_manager import EndOfDayManager


class ScalpingBot:
    def __init__(self, config: Config):
        self.config = config
        self.running = False

        self.client = BinanceClient(config)
        self.risk_manager = RiskManager(config)
        self.order_manager = OrderManager(self.client, self.risk_manager, config)
        self.strategies: Dict[str, ScalpingStrategy] = {}
        self.data_feed = DataFeed(self.client, config)
        self.pair_selector = PairSelector(self.client, config)
        self.eod_manager = EndOfDayManager(self.client, config)
        self.risk_manager.set_eod_manager(self.eod_manager)

    async def start(self):
        self.running = True
        logger.info("✅ Bot initialized, connecting to Binance...")

        if self.config.testnet:
            logger.warning("⚠️  TESTNET MODE - No real money at risk")

        # Sincronizza clock con Binance — fix errore -1022
        await self.client.sync_clock()

        # Seleziona coppie automaticamente o da config
        if self.config.auto_select_pairs:
            logger.info("🔍 Analisi mercato per selezione coppie...")
            await self.pair_selector.start()
            pairs = await self.pair_selector.get_pairs()
        else:
            pairs = self.config.pairs
            logger.info(f"📋 Coppie manuali: {pairs}")

        # Registra strategia per ogni coppia
        for pair in pairs:
            self.strategies[pair] = ScalpingStrategy(pair, self.config)

        # Setup leverage for futures
        if self.config.enable_futures:
            await self.client.set_leverage_all(
                pairs,
                self.config.futures_leverage
            )

        # Start data streams
        await self.data_feed.start(
            pairs=pairs,
            on_kline=self._on_kline,
            on_orderbook=self._on_orderbook,
            on_trade=self._on_trade,
        )

        # Main loop - monitor open orders
        while self.running:
            await self.order_manager.monitor_open_orders()
            await asyncio.sleep(1)

    async def stop(self):
        logger.info("🛑 Stopping bot...")
        self.running = False
        await self.order_manager.close_all_positions()
        await self.data_feed.stop()
        await self.pair_selector.stop()
        logger.info("✅ Bot stopped cleanly.")

    async def _on_kline(self, pair: str, kline_data: dict):
        strategy = self.strategies.get(pair)
        if not strategy:
            return
        strategy.update_kline(kline_data)
        await self._evaluate_signal(pair)

    async def _on_orderbook(self, pair: str, orderbook: dict):
        strategy = self.strategies.get(pair)
        if not strategy:
            return
        strategy.update_orderbook(orderbook)
        await self._evaluate_signal(pair)

    async def _on_trade(self, pair: str, trade: dict):
        strategy = self.strategies.get(pair)
        if strategy:
            strategy.update_trade(trade)

    async def _evaluate_signal(self, pair: str):
        if not self.running:
            return

        if self.risk_manager.is_daily_target_hit():
            await self.eod_manager.run()
            return

        if self.risk_manager.is_daily_limit_hit():
            if self.running:
                logger.error("❌ Daily loss limit reached. Stopping bot.")
                await self.stop()
            return

        strategy = self.strategies[pair]
        signal = strategy.get_signal()

        if signal == "NONE":
            return

        if not self.risk_manager.can_open_trade(pair):
            return

        logger.info(f"📡 Signal: {signal} | {pair}")

        if self.config.enable_spot:
            await self.order_manager.open_trade(pair, signal, market="SPOT")

        if self.config.enable_futures:
            await self.order_manager.open_trade(pair, signal, market="FUTURES")
