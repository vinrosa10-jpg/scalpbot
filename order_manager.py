"""
Order Manager - Handles entry, exit, TP/SL for all open trades.
"""

import asyncio
import time
from typing import Dict, Optional
from dataclasses import dataclass, field
from loguru import logger
from config import Config
from risk_manager import RiskManager


@dataclass
class Trade:
    pair: str
    side: str           # LONG / SHORT
    market: str         # SPOT / FUTURES
    entry_price: float
    qty: float
    tp_price: float
    sl_price: float
    order_id: str
    opened_at: float = field(default_factory=time.time)
    status: str = "OPEN"


class OrderManager:
    def __init__(self, client, risk_manager: RiskManager, config: Config):
        self.client = client
        self.risk_manager = risk_manager
        self.config = config
        self.trades: Dict[str, Trade] = {}  # key = pair+market

    def _trade_key(self, pair: str, market: str) -> str:
        return f"{pair}_{market}"

    async def open_trade(self, pair: str, signal: str, market: str):
        key = self._trade_key(pair, market)
        if key in self.trades:
            return

        # Get current price
        ticker = await self.client.get_ticker(pair, market)
        if not ticker:
            return

        price = float(ticker["price"])
        side = "BUY" if signal == "LONG" else "SELL"

        # For SPOT, no shorting
        if market == "SPOT" and signal == "SHORT":
            return

        qty = self.risk_manager.calculate_position_size(price, market)
        tp, sl = self.risk_manager.calculate_tp_sl(price, signal)

        # Adjust limit price slightly for faster fill
        if side == "BUY":
            limit_price = price * (1 + self.config.limit_order_offset_pct)
        else:
            limit_price = price * (1 - self.config.limit_order_offset_pct)

        limit_price = round(limit_price, 8)

        logger.info(f"📥 Opening {signal} {pair} [{market}] | Price: {price} | TP: {tp} | SL: {sl}")

        try:
            order = await self.client.place_order(
                pair=pair,
                side=side,
                order_type=self.config.order_type,
                qty=qty,
                price=limit_price,
                market=market,
            )

            order_id = order.get("orderId", "unknown")
            trade = Trade(
                pair=pair,
                side=signal,
                market=market,
                entry_price=limit_price,
                qty=qty,
                tp_price=tp,
                sl_price=sl,
                order_id=str(order_id),
            )

            self.trades[key] = trade
            self.risk_manager.register_trade_open(pair)

        except Exception as e:
            logger.error(f"Order error {pair} [{market}]: {e}")

    async def monitor_open_orders(self):
        """Check TP/SL on all open trades."""
        for key, trade in list(self.trades.items()):
            try:
                ticker = await self.client.get_ticker(trade.pair, trade.market)
                if not ticker:
                    continue

                current_price = float(ticker["price"])
                elapsed = time.time() - trade.opened_at

                # Timeout: cancel unfilled limit orders
                if elapsed > self.config.order_timeout_sec and trade.status == "OPEN":
                    logger.warning(f"⏱️  Order timeout {trade.pair} [{trade.market}] — cancelling")
                    await self.client.cancel_order(trade.pair, trade.order_id, trade.market)
                    await self._close_trade(key, trade, current_price, reason="TIMEOUT")
                    continue

                # Check TP
                if trade.side == "LONG" and current_price >= trade.tp_price:
                    await self._close_trade(key, trade, trade.tp_price, reason="TP")
                elif trade.side == "SHORT" and current_price <= trade.tp_price:
                    await self._close_trade(key, trade, trade.tp_price, reason="TP")

                # Check SL
                elif trade.side == "LONG" and current_price <= trade.sl_price:
                    await self._close_trade(key, trade, trade.sl_price, reason="SL")
                elif trade.side == "SHORT" and current_price >= trade.sl_price:
                    await self._close_trade(key, trade, trade.sl_price, reason="SL")

            except Exception as e:
                logger.error(f"Monitor error {key}: {e}")

    async def _close_trade(self, key: str, trade: Trade, exit_price: float, reason: str):
        """Close a trade and register PnL."""
        if trade.side == "LONG":
            pnl = (exit_price - trade.entry_price) * trade.qty
        else:
            pnl = (trade.entry_price - exit_price) * trade.qty

        # Subtract fees (~0.1% per leg on spot, 0.04% on futures)
        fee_pct = 0.001 if trade.market == "SPOT" else 0.0004
        fees = trade.entry_price * trade.qty * fee_pct * 2
        net_pnl = pnl - fees

        logger.info(f"🔒 Close [{reason}] {trade.pair} [{trade.market}] | Net PnL: {net_pnl:+.4f} USDT")

        try:
            close_side = "SELL" if trade.side == "LONG" else "BUY"
            await self.client.place_order(
                pair=trade.pair,
                side=close_side,
                order_type="MARKET",
                qty=trade.qty,
                price=None,
                market=trade.market,
            )
        except Exception as e:
            logger.error(f"Close order error: {e}")

        self.trades.pop(key, None)
        self.risk_manager.register_trade_close(trade.pair, net_pnl)

    async def close_all_positions(self):
        """Emergency close all."""
        logger.warning("⚠️  Closing all positions...")
        for key, trade in list(self.trades.items()):
            ticker = await self.client.get_ticker(trade.pair, trade.market)
            price = float(ticker["price"]) if ticker else trade.entry_price
            await self._close_trade(key, trade, price, reason="EMERGENCY")
