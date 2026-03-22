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

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self._today:
            logger.info(f"📅 Nuovo giorno — PnL ieri: {self.daily_pnl:+.2f} USDT — Bot riattivato ✅")
            self.daily_pnl = 0.0
            self._today = today
            self._target_hit = False
            # Aggiorna capitale di partenza per il nuovo giorno (compounding)
            if hasattr(self, '_eod_manager'):
                self._eod_manager.reset_for_new_day()

    def set_eod_manager(self, eod_manager):
        self._eod_manager = eod_manager

    def can_open_trade(self, pair: str) -> bool:
        self._reset_daily_if_needed()

        if len(self.open_pairs) >= self.config.max_open_trades:
            logger.debug(f"Max open trades reached ({self.config.max_open_trades})")
            return False

        if pair in self.open_pairs:
            logger.debug(f"{pair} already has an open trade")
            return False

        if self.is_daily_limit_hit():
            return False

        return True

    def is_daily_limit_hit(self) -> bool:
        self._reset_daily_if_needed()
        if self.daily_pnl <= -abs(self.config.max_daily_loss_usdt):
            logger.warning(f"⛔ Limite perdita giornaliera: {self.daily_pnl:.2f} USDT — Bot in pausa fino a domani")
            return True
        return False

    def is_daily_target_hit(self) -> bool:
        """Controlla se abbiamo raggiunto il target giornaliero (es. +20%)."""
        self._reset_daily_if_needed()
        if self._target_hit:
            return True
        target_usdt = self.daily_start_capital * self.config.daily_profit_target_pct
        if self.daily_pnl >= target_usdt:
            self._target_hit = True
            pct = self.daily_pnl / self.daily_start_capital * 100
            logger.success(
                f"🎯 TARGET GIORNALIERO RAGGIUNTO! "
                f"+{self.daily_pnl:.2f} USDT ({pct:.1f}%) — "
                f"Bot in pausa fino a domani 🌙"
            )
            return True
        return False

    def set_daily_start_capital(self, capital: float):
        """Aggiorna il capitale di partenza giornaliero (per compounding)."""
        self.daily_start_capital = capital
        logger.info(f"📊 Capitale giornaliero: {capital:.2f} USDT | Target: +{capital * self.config.daily_profit_target_pct:.2f} USDT ({self.config.daily_profit_target_pct*100:.0f}%)")

    def register_trade_open(self, pair: str):
        self.open_pairs.add(pair)
        logger.debug(f"📌 Trade opened: {pair} | Open: {self.open_pairs}")

    def register_trade_close(self, pair: str, pnl_usdt: float):
        self.open_pairs.discard(pair)
        self.daily_pnl += pnl_usdt
        emoji = "✅" if pnl_usdt >= 0 else "❌"
        target_usdt = self.daily_start_capital * self.config.daily_profit_target_pct
        progress_pct = (self.daily_pnl / target_usdt * 100) if target_usdt > 0 else 0
        logger.info(
            f"{emoji} Chiuso: {pair} | PnL: {pnl_usdt:+.4f} USDT | "
            f"Oggi: {self.daily_pnl:+.4f} USDT | "
            f"Verso target: {progress_pct:.0f}%"
        )

    def calculate_position_size(self, price: float, market: str) -> float:
        """Returns quantity to buy/sell based on USDT position size."""
        usdt = self.config.position_size_usdt
        if market == "FUTURES":
            usdt *= self.config.futures_leverage
        qty = usdt / price
        return qty

    def calculate_tp_sl(self, entry_price: float, side: str):
        """Returns (take_profit_price, stop_loss_price)."""
        tp_pct = self.config.take_profit_pct
        sl_pct = self.config.stop_loss_pct

        if side == "LONG":
            tp = entry_price * (1 + tp_pct)
            sl = entry_price * (1 - sl_pct)
        else:  # SHORT
            tp = entry_price * (1 - tp_pct)
            sl = entry_price * (1 + sl_pct)

        return round(tp, 8), round(sl, 8)
