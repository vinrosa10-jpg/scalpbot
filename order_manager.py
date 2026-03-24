"""
Order Manager - Handles entry, exit, TP/SL for all open trades.
"""

import asyncio
import math
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


# Step size per coppia — LOT_SIZE filter di Binance
LOT_STEP = {
    # SPOT
    "BTCUSDT":    0.00001,
    "ETHUSDT":    0.0001,
    "BNBUSDT":    0.001,
    "SOLUSDT":    0.01,
    "XRPUSDT":    0.1,
    "DOGEUSDT":   1.0,
    "ADAUSDT":    1.0,
    # FUTURES — step size diverso
    "BTCUSDT_F":  0.001,
    "ETHUSDT_F":  0.001,
    "BNBUSDT_F":  0.01,
    "SOLUSDT_F":  0.1,
    "XRPUSDT_F":  1.0,
}
DEFAULT_STEP = 0.001


def round_lot(qty: float, pair: str, market: str = "SPOT") -> float:
    """Arrotonda qty al step size corretto per coppia e mercato."""
    key = pair + ("_F" if market == "FUTURES" else "")
    step = LOT_STEP.get(key, LOT_STEP.get(pair, DEFAULT_STEP))
    qty = math.floor(qty / step) * step
    precision = max(0, int(round(-math.log10(step))))
    return round(qty, precision)


class OrderManager:
    def __init__(self, client, risk_manager: RiskManager, config: Config):
        self.client = client
        self.risk_manager = risk_manager
        self.config = config
        self.trades: Dict[str, Trade] = {}

    def _trade_key(self, pair: str, market: str) -> str:
        return f"{pair}_{market}"

    async def open_trade(self, pair: str, signal: str, market: str):
        key = self._trade_key(pair, market)
        if key in self.trades:
            return

        ticker = await self.client.get_ticker(pair, market)
        if not ticker:
            return

        price = float(ticker["price"])
        side = "BUY" if signal == "LONG" else "SELL"

        # SPOT non supporta SHORT
        if market == "SPOT" and signal == "SHORT":
            return

        qty = self.risk_manager.calculate_position_size(price, market)
        qty = round_lot(qty, pair, market)

        if qty <= 0:
            logger.warning(f"⚠️ Qty troppo piccola per {pair} [{market}]: {qty} — saltato")
            return

        tp, sl = self.risk_manager.calculate_tp_sl(price, signal)

        if side == "BUY":
            limit_price = price * (1 + self.config.limit_order_offset_pct)
        else:
            limit_price = price * (1 - self.config.limit_order_offset_pct)

        limit_price = round(limit_price, 2)

        logger.info(f"📥 Opening {signal} {pair} [{market}] | Price: {price} | Qty: {qty} | TP: {tp} | SL: {sl}")

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
            logger.info(f"✅ Ordine aperto {pair} [{market}] | ID: {order_id}")

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

                # Timeout
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
