"""
Auto Compounder - Reinveste automaticamente i profitti.

Logica:
- Tiene traccia del capitale totale (base + profitti)
- Dopo ogni trade vincente, aumenta la position size proporzionalmente
- Hard cap: non supera mai il max_position_pct del capitale totale
- Protezione drawdown: se perde X% del picco, riduce la size
"""

from loguru import logger
from config import Config


class AutoCompounder:
    def __init__(self, config: Config):
        self.config = config

        # Capitale iniziale stimato
        self.starting_capital = config.starting_capital_usdt
        self.current_capital = config.starting_capital_usdt
        self.peak_capital = config.starting_capital_usdt

        # Stats
        self.total_pnl = 0.0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_trades = 0

    def register_pnl(self, pnl_usdt: float):
        """Registra un trade chiuso e aggiorna il capitale."""
        self.total_pnl += pnl_usdt
        self.current_capital += pnl_usdt
        self.total_trades += 1

        if pnl_usdt > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        # Aggiorna picco
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

        self._log_status()

    def get_position_size(self) -> float:
        """
        Calcola la position size ottimale basata sul capitale attuale.
        Usa Kelly Criterion semplificato per modalità aggressiva.
        """
        # Drawdown protection: se siamo sotto il X% del picco, riduci size
        drawdown = (self.peak_capital - self.current_capital) / self.peak_capital
        if drawdown >= self.config.max_drawdown_pct:
            reduced = self.config.position_size_usdt * 0.25
            logger.warning(
                f"⚠️  Drawdown {drawdown:.1%} — size ridotta a {reduced:.2f} USDT"
            )
            return reduced

        # Compounding: position size = % fissa del capitale attuale
        position = self.current_capital * self.config.position_pct_of_capital

        # Clamp tra min e max
        position = max(position, self.config.position_size_usdt)
        position = min(position, self.config.max_position_usdt)

        return round(position, 2)

    def get_daily_pnl_pct(self) -> float:
        return (self.total_pnl / self.starting_capital * 100) if self.starting_capital > 0 else 0

    def get_win_rate(self) -> float:
        if self.total_trades == 0:
            return 0
        return self.winning_trades / self.total_trades * 100

    def _log_status(self):
        roi = (self.current_capital - self.starting_capital) / self.starting_capital * 100
        wr = self.get_win_rate()
        logger.info(
            f"💰 Capitale: {self.current_capital:.2f} USDT | "
            f"ROI: {roi:+.2f}% | "
            f"Win rate: {wr:.0f}% ({self.winning_trades}W/{self.losing_trades}L) | "
            f"Next size: {self.get_position_size():.2f} USDT"
        )
