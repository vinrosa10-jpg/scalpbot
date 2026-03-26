"""
Risk Manager -- Capital protection with hard rules.
Cooldowns tuned for 15m timeframe.
"""

import time
from datetime import date
from typing import Set, Dict
from loguru import logger
from config import Config


class RiskManager:
    def __init__(self, config: Config):
        self.config = config
        self.open_pairs: Set[str] = set()
        self.daily_pnl: float = 0.0
        self.daily_start_capital: float = config.starting_capital_usdt
        self._today: date = date.today()
        self._target_hit: bool = False
        self.winning_trades: int = 0
        self.losing_trades: int = 0
        self._cooldowns: Dict[str, float] = {}
        self._consecutive_losses: Dict[str, int] = {}

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self._today:
            logger.info(f"📅 New day | Yesterday PnL: {self.daily_pnl:+.2f} USDT")
            self.daily_pnl = 0.0
            self._today = today
            self._target_hit = False
            self.winning_trades = 0
            self.losing_trades = 0
            self._cooldowns = {}
            self._consecutive_losses = {}
            if hasattr(self, '_eod_manager'):
                self._eod_manager.reset_for_new_day()

    def set_eod_manager(self, eod_manager):
        self._eod_manager = eod_manager

    def can_open_trade(self, pair: str) -> bool:
        self._reset_daily_if_needed()

        if len(self.open_pairs) >= self.config.max_open_trades:
            return False
        if pair in self.open_pairs:
            return False
        if self.is_daily_limit_hit():
            return False

        cooldown_until = self._cooldowns.get(pair, 0)
        if time.time() < cooldown_until:
            remaining = int(cooldown_until - time.time())
            logger.debug(f"⏳ {pair} cooldown -- {remaining}s left")
            return False

        if self._consecutive_losses.get(pair, 0) >= 3:
            logger.warning(f"🛑 {pair} blocked -- 3 consecutive losses")
            self._cooldowns[pair] = time.time() + 1800
            self._consecutive_losses[pair] = 0
            return False

        return True

    def is_daily_limit_hit(self) -> bool:
        self._reset_daily_if_needed()
        if self.daily_pnl <= -abs(self.config.max_daily_loss_usdt):
            logger.warning(f"⛔ Daily loss limit hit: {self.daily_pnl:.2f} USDT")
            return True
        return False

    def is_daily_target_hit(self) -> bool:
        self._reset_daily_if_needed()
        if self._target_hit:
            return True
        target = self.daily_start_capital * self.config.daily_profit_target_pct
        if self.daily_pnl >= target:
            self._target_hit = True
            pct = self.daily_pnl / self.daily_start_capital * 100
            logger.success(f"🎯 TARGET HIT! +{self.daily_pnl:.2f} USDT ({pct:.1f}%)")
            return True
        return False

    def set_daily_start_capital(self, capital: float):
        self.daily_start_capital = capital

    def register_trade_open(self, pair: str):
        self.open_pairs.add(pair)

    def register_trade_close(self, pair: str, pnl_usdt: float, reason: str = ""):
        self.open_pairs.discard(pair)
        self.daily_pnl += pnl_usdt

        if pnl_usdt >= 0:
            self.winning_trades += 1
            self._consecutive_losses[pair] = 0
        else:
            self.losing_trades += 1
            losses = self._consecutive_losses.get(pair, 0) + 1
            self._consecutive_losses[pair] = losses
            # Cooldown tuned for 15m -- wait at least 1 candle
            cooldown = 900 if reason == "SL" else 600
            self._cooldowns[pair] = time.time() + cooldown
            logger.info(f"⏳ {pair} cooldown: {cooldown//60}min")

        emoji = "✅" if pnl_usdt >= 0 else "❌"
        logger.info(
            f"{emoji} {pair} closed | PnL: {pnl_usdt:+.4f} USDT | "
            f"Today: {self.daily_pnl:+.4f} | "
            f"W/L: {self.winning_trades}/{self.losing_trades}"
        )

    def calculate_position_size(self, price: float, market: str) -> float:
        if market == "FUTURES":
            usdt = self.config.futures_position_size_usdt * self.config.futures_leverage
        else:
            usdt = self.config.position_size_usdt
        return usdt / price

    def calculate_tp_sl(self, entry_price: float, side: str) -> tuple:
        tp_pct = self.config.take_profit_pct
        sl_pct = self.config.stop_loss_pct
        if side == "LONG":
            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)
        else:
            tp = entry_price * (1 - tp_pct)
            sl = entry_price * (1 + sl_pct)
        return round(tp, 8), round(sl, 8)
