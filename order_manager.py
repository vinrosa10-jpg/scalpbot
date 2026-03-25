"""
Order Manager - Handles entry, exit, TP/SL for all open trades.
Saves all trades to SQLite database for automatic documentation.
"""

import asyncio
import math
import time
from typing import Dict, Optional
from dataclasses import dataclass, field
from loguru import logger
from config import Config
from risk_manager import RiskManager
import database as db


@dataclass
class Trade:
    pair: str
    side: str
    market: str
    entry_price: float
    qty: float
    tp_price: float
    sl_price: float
    order_id: str
    opened_at: float = field(default_factory=time.time)
    status: str = "OPEN"


LOT_STEP = {
    "BTCUSDT":   0.00001,
    "ETHUSDT":   0.0001,
    "BNBUSDT":   0.001,
    "SOLUSDT":   0.01,
    "XRPUSDT":   0.1,
    "DOGEUSDT":  1.0,
    "ADAUSDT":   1.0,
    "BTCUSDT_F": 0.001,
    "ETHUSDT_F": 0.001,
    "BNBUSDT_F": 0.01,
    "SOLUSDT_F": 0.1,
    "XRPUSDT_F": 1.0,
}
DEFAULT_STEP = 0.001


def round_lot(qty: float, pair: str, market: str = "SPOT") -> float:
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
        self.api_server = None  # Impostato da bot.py

    def _log(self, type_: str, msg: str):
        """Log su console e app mobile."""
        if type_ == 'win':
            logger.success(msg)
        elif type_ == 'loss':
            logger.warning(msg)
        else:
            logger.info(msg)
        if self.api_server:
            self.api_server.add_log(type_, msg)

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

        if market == "SPOT" and signal == "SHORT":
            return

        qty = self.risk_manager.calculate_position_size(price, market)
        qty = round_lot(qty, pair, market)

        if qty <= 0:
            logger.warning(f"⚠️ Qty troppo piccola per {pair} [{market}]: {qty}")
            return

        tp, sl = self.risk_manager.calculate_tp_sl(price, signal)

        if side == "BUY":
            limit_price = price * (1 + self.config.limit_order_offset_pct)
        else:
            limit_price = price * (1 - self.config.limit_order_offset_pct)

        limit_price = round(limit_price, 2)

        self._log('info', f'📥 {signal} {pair} | Entry: {limit_price} | TP: {round(tp,2)} | SL: {round(sl,2)}')

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
            self._log('info', f'✅ Aperto {pair} [{market}] ID: {order_id}')

        except Exception as e:
            self._log('warn', f'⚠️ Errore ordine {pair}: {e}')

    async def monitor_open_orders(self):
        for key, trade in list(self.trades.items()):
            try:
                ticker = await self.client.get_ticker(trade.pair, trade.market)
                if not ticker:
                    continue

                current_price = float(ticker["price"])
                elapsed = time.time() - trade.opened_at

                if elapsed > self.config.order_timeout_sec and trade.status == "OPEN":
                    logger.warning(f"⏱️ Order timeout {trade.pair} [{trade.market}]")
                    await self.client.cancel_order(trade.pair, trade.order_id, trade.market)
                    await self._close_trade(key, trade, current_price, reason="TIMEOUT")
                    continue

                if trade.side == "LONG" and current_price >= trade.tp_price:
                    await self._close_trade(key, trade, trade.tp_price, reason="TP")
                elif trade.side == "SHORT" and current_price <= trade.tp_price:
                    await self._close_trade(key, trade, trade.tp_price, reason="TP")
                elif trade.side == "LONG" and current_price <= trade.sl_price:
                    await self._close_trade(key, trade, trade.sl_price, reason="SL")
                elif trade.side == "SHORT" and current_price >= trade.sl_price:
                    await self._close_trade(key, trade, trade.sl_price, reason="SL")

            except Exception as e:
                logger.error(f"Monitor error {key}: {e}")

    async def _close_trade(self, key: str, trade: Trade, exit_price: float, reason: str):
        if trade.side == "LONG":
            pnl = (exit_price - trade.entry_price) * trade.qty
        else:
            pnl = (trade.entry_price - exit_price) * trade.qty

        fee_pct = 0.001 if trade.market == "SPOT" else 0.0004
        fees = trade.entry_price * trade.qty * fee_pct * 2
        net_pnl = pnl - fees
        duration = time.time() - trade.opened_at

        # Log su console
        logger.info(f"🔒 Close [{reason}] {trade.pair} [{trade.market}] | Net PnL: {net_pnl:+.4f} USDT")

        # Chiudi ordine su exchange
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

        # Salva nel database
        try:
            db.save_trade(
                pair=trade.pair,
                side=trade.side,
                market=trade.market,
                entry_price=trade.entry_price,
                exit_price=exit_price,
                qty=trade.qty,
                pnl=net_pnl,
                reason=reason,
                duration_sec=round(duration, 1)
            )
            rm = self.risk_manager
            db.save_equity(
                equity=rm.daily_start_capital + rm.daily_pnl,
                pnl_cumulative=rm.daily_pnl
            )
        except Exception as e:
            logger.error(f"DB save error: {e}")

        # Log su app mobile con dettaglio completo
        if self.api_server:
            emoji = '✅' if net_pnl > 0 else '❌'
            type_ = 'win' if net_pnl > 0 else 'loss'
            reason_emoji = {'TP': '🎯', 'SL': '🛑', 'TIMEOUT': '⏱️', 'EMERGENCY': '🚨'}.get(reason, '🔒')
            self.api_server.add_log(
                type_,
                f'{emoji} {trade.pair} {trade.side} {reason_emoji}[{reason}] {net_pnl:+.4f}$'
            )

        self.trades.pop(key, None)
        self.risk_manager.register_trade_close(trade.pair, net_pnl)

    async def close_all_positions(self):
        logger.warning("⚠️  Closing all positions...")
        for key, trade in list(self.trades.items()):
            ticker = await self.client.get_ticker(trade.pair, trade.market)
            price = float(ticker["price"]) if ticker else trade.entry_price
            await self._close_trade(key, trade, price, reason="EMERGENCY")
