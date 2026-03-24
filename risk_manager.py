"""
Risk Manager - Protects capital with hard rules.
"""

from datetime import date
from typing import Set
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

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self._today:
            logger.info(f"📅 Nuovo giorno — PnL ieri: {self.daily_pnl:+.2f} USDT — Bot riattivato ✅")
            self.daily_pnl = 0.0
            self._today = today
            self._target_hit = False
            self.winning_trades = 0
            self.losing_trades = 0
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

        return True

    def is_daily_limit_hit(self) -> bool:
        self._reset_daily_if_needed()
        if self.daily_pnl <= -abs(self.config.max_daily_loss_usdt):
            logger.warning(f"⛔ Limite perdita giornaliera: {self.daily_pnl:.2f} USDT")
            return True
        return False

    def is_daily_target_hit(self) -> bool:
        self._reset_daily_if_needed()
        if self._target_hit:
            return True
        target_usdt = self.daily_start_capital * self.config.daily_profit_target_pct
        if self.daily_pnl >= target_usdt:
            self._target_hit = True
            pct = self.daily_pnl / self.daily_start_capital * 100
            logger.success(
                f"🎯 TARGET RAGGIUNTO! "
                f"+{self.daily_pnl:.2f} USDT ({pct:.1f}%)"
            )
            return True
        return False

    def set_daily_start_capital(self, capital: float):
        self.daily_start_capital = capital
        logger.info(f"📊 Capitale: {capital:.2f} USDT | Target: +{capital * self.config.daily_profit_target_pct:.2f} USDT")

    def register_trade_open(self, pair: str):
        self.open_pairs.add(pair)

    def register_trade_close(self, pair: str, pnl_usdt: float):
        self.open_pairs.discard(pair)
        self.daily_pnl += pnl_usdt
        if pnl_usdt >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        emoji = "✅" if pnl_usdt >= 0 else "❌"
        target_usdt = self.daily_start_capital * self.config.daily_profit_target_pct
        progress_pct = (self.daily_pnl / target_usdt * 100) if target_usdt > 0 else 0
        logger.info(
            f"{emoji} Chiuso: {pair} | PnL: {pnl_usdt:+.4f} USDT | "
            f"Oggi: {self.daily_pnl:+.4f} USDT | "
            f"W/L: {self.winning_trades}/{self.losing_trades} | "
            f"Verso target: {progress_pct:.0f}%"
        )

    def calculate_position_size(self, price: float, market: str) -> float:
        """Calcola quantità in base al mercato."""
        if market == "FUTURES":
            usdt = getattr(self.config, 'futures_position_size_usdt', 100.0)
            usdt *= self.config.futures_leverage
        else:
            usdt = self.config.position_size_usdt
        qty = usdt / price
        return qty

    def calculate_tp_sl(self, entry_price: float, side: str):
        tp_pct = self.config.take_profit_pct
        sl_pct = self.config.stop_loss_pct

        if side == "LONG":
            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)
        else:
            tp = entry_price * (1 - tp_pct)
            sl = entry_price * (1 + sl_pct)

        return round(tp, 8), round(sl, 8)
