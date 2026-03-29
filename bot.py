"""
ScalpingBot — Main orchestrator
Price Action strategy on closed candles only.
"""

import asyncio
from typing import Dict
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
        self.api_server = None

        self.client = BinanceClient(config)
        self.risk_manager = RiskManager(config)
        self.order_manager = OrderManager(self.client, self.risk_manager, config)
        self.strategies: Dict[str, ScalpingStrategy] = {}
        self.data_feed = DataFeed(self.client, config)
        self.pair_selector = PairSelector(self.client, config)
        self.eod_manager = EndOfDayManager(self.client, config)
        self.risk_manager.set_eod_manager(self.eod_manager)

    def set_api_server(self, api_server):
        self.api_server = api_server
        self.order_manager.api_server = api_server

    def _log(self, type_: str, msg: str):
        if type_ == 'win':
            logger.success(msg)
        elif type_ == 'loss':
            logger.warning(msg)
        else:
            logger.info(msg)
        if self.api_server:
            self.api_server.add_log(type_, msg)

    async def start(self):
        self.running = True
        self._log('info', '🤖 ScalpBot started')

        if self.config.testnet:
            self._log('warn', '⚠️ TESTNET MODE')

        # Log stato mercati all'avvio per debug immediato
        self._log('info',
            f"🔧 Markets: SPOT={'ON' if self.config.enable_spot else 'OFF'} | "
            f"FUTURES={'ON' if self.config.enable_futures else 'OFF'}"
        )

        if not self.config.enable_spot and not self.config.enable_futures:
            self._log('warn', '⚠️ Attenzione: sia SPOT che FUTURES sono disabilitati — nessun trade verrà aperto!')

        await self.client.sync_clock()

        # Select pairs
        if self.config.auto_select_pairs:
            self._log('info', '🔍 Auto-selecting pairs...')
            await self.pair_selector.start()
            pairs = await self.pair_selector.get_pairs()
        else:
            pairs = self.config.pairs
            self._log('info', f'📋 Pairs: {", ".join(pairs)}')

        # Init strategies
        for pair in pairs:
            self.strategies[pair] = ScalpingStrategy(pair, self.config)

        # Setup futures leverage
        if self.config.enable_futures:
            await self.client.set_leverage_all(pairs, self.config.futures_leverage)

        # Warm-up all strategies
        self._log('info', f'📊 Warming up {self.config.kline_interval} candles...')
        await asyncio.gather(*[
            strat.warm_up_from_api(self.client)
            for strat in self.strategies.values()
        ])

        # Verify warm-up
        failed = [p for p, s in self.strategies.items() if not s._warmed_up]
        if failed:
            logger.warning(f"⚠️ Warm-up failed for: {failed} — retrying...")
            await asyncio.gather(*[
                self.strategies[p].warm_up_from_api(self.client)
                for p in failed
            ])
            still_failed = [p for p in failed if not self.strategies[p]._warmed_up]
            if still_failed:
                logger.warning(f"⚠️ Still failed: {still_failed} — signals blocked until data arrives")

        ready = [p for p, s in self.strategies.items() if s._warmed_up]
        self._log('info', f'✅ Ready: {", ".join(ready)} | Interval: {self.config.kline_interval}')

        # Start data streams
        await self.data_feed.start(
            pairs=pairs,
            on_kline=self._on_kline,
            on_orderbook=self._on_orderbook,
            on_trade=self._on_trade,
        )

        # Main loop
        while self.running:
            await self.order_manager.monitor_open_orders()
            await asyncio.sleep(1)

    async def stop(self):
        self._log('info', '🛑 Stopping...')
        self.running = False
        await self.order_manager.close_all_positions()
        await self.data_feed.stop()
        await self.pair_selector.stop()
        self._log('info', '✅ Stopped cleanly.')

    async def _on_kline(self, pair: str, data: dict):
        strat = self.strategies.get(pair)
        if strat:
            strat.update_kline(data)
            await self._evaluate(pair)

    async def _on_orderbook(self, pair: str, data: dict):
        strat = self.strategies.get(pair)
        if strat:
            strat.update_orderbook(data)
            await self._evaluate(pair)

    async def _on_trade(self, pair: str, data: dict):
        strat = self.strategies.get(pair)
        if strat:
            strat.update_trade(data)

    async def _evaluate(self, pair: str):
        if not self.running:
            return

        if self.risk_manager.is_daily_target_hit():
            await self.eod_manager.run()
            return

        if self.risk_manager.is_daily_limit_hit():
            if self.running:
                self._log('loss', '❌ Daily loss limit hit. Stopping.')
                await self.stop()
            return

        strat = self.strategies[pair]

        # Block signals until warm-up complete
        if not strat._warmed_up:
            return

        # Need at least 20 candles for reliable PA signals
        if len(strat.candles) < 20:
            return

        signal = strat.get_signal()
        if signal == "NONE":
            return

        # FIX: spot Binance non supporta SHORT (no margin trading su testnet)
        # Blocca SHORT se siamo solo in spot mode
        if signal == "SHORT" and self.config.enable_spot and not self.config.enable_futures:
            logger.debug(f"🚫 {pair}: SHORT ignorato in modalità SPOT-only")
            return

        if not self.risk_manager.can_open_trade(pair):
            self._log('info', f'🚫 {pair}: can_open_trade=False (max trades raggiunto o cooldown attivo)')
            return

        self._log('info', f'📡 Signal: {signal} | {pair}')

        if self.config.enable_spot:
            await self.order_manager.open_trade(pair, signal, market="SPOT")

        if self.config.enable_futures:
            await self.order_manager.open_trade(pair, signal, market="FUTURES")
