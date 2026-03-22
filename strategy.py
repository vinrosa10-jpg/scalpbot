"""
Scalping Strategy: Order Book Imbalance + EMA Momentum

Entry conditions (LONG):
  - EMA fast > EMA slow (uptrend)
  - Order book buy pressure > ob_imbalance_threshold
  - Recent trade flow confirms buying

Entry conditions (SHORT - futures only):
  - EMA fast < EMA slow (downtrend)
  - Order book sell pressure > ob_imbalance_threshold
  - Recent trade flow confirms selling

Exit: Take profit / Stop loss handled by OrderManager.
"""

from collections import deque
from typing import Literal, Optional
from loguru import logger
from config import Config


SignalType = Literal["LONG", "SHORT", "NONE"]


class EMA:
    def __init__(self, period: int):
        self.period = period
        self.k = 2 / (period + 1)
        self.value: Optional[float] = None
        self._count = 0

    def update(self, price: float) -> Optional[float]:
        if self.value is None:
            self._count += 1
            if not hasattr(self, "_sum"):
                self._sum = 0
            self._sum += price
            if self._count >= self.period:
                self.value = self._sum / self.period
        else:
            self.value = price * self.k + self.value * (1 - self.k)
        return self.value


class ScalpingStrategy:
    def __init__(self, pair: str, config: Config):
        self.pair = pair
        self.config = config

        self.ema_fast = EMA(config.ema_fast)
        self.ema_slow = EMA(config.ema_slow)

        # Order book state
        self.bid_volume = 0.0
        self.ask_volume = 0.0

        # Recent trade flow
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.trade_window = deque(maxlen=50)  # Last 50 trades

        # Current kline state
        self.last_close: Optional[float] = None
        self.kline_closed = False

        # Signal cooldown (avoid firing repeatedly on same condition)
        self._last_signal: SignalType = "NONE"
        self._signal_count = 0

    def update_kline(self, data: dict):
        """Update EMA from kline (candle) data."""
        close = float(data["k"]["c"])
        is_closed = data["k"]["x"]  # True when candle is complete

        self.last_close = close
        self.kline_closed = is_closed

        if is_closed:
            self.ema_fast.update(close)
            self.ema_slow.update(close)
            # Reset trade flow on new candle
            self.buy_volume = 0.0
            self.sell_volume = 0.0

    def update_orderbook(self, data: dict):
        """Update order book imbalance from top N levels."""
        depth = self.config.ob_depth_levels
        bids = data.get("bids", [])[:depth]
        asks = data.get("asks", [])[:depth]

        self.bid_volume = sum(float(b[1]) for b in bids)
        self.ask_volume = sum(float(a[1]) for a in asks)

    def update_trade(self, trade: dict):
        """Update buy/sell trade flow."""
        qty = float(trade["q"])
        is_buyer_maker = trade["m"]  # True = seller is aggressor = sell trade
        if is_buyer_maker:
            self.sell_volume += qty
        else:
            self.buy_volume += qty
        self.trade_window.append(trade)

    def get_signal(self) -> SignalType:
        """Compute current signal."""
        if self.ema_fast.value is None or self.ema_slow.value is None:
            return "NONE"

        total_ob = self.bid_volume + self.ask_volume
        if total_ob == 0:
            return "NONE"

        buy_ratio = self.bid_volume / total_ob
        sell_ratio = self.ask_volume / total_ob
        threshold = self.config.ob_imbalance_threshold

        total_flow = self.buy_volume + self.sell_volume
        flow_confirms_buy = (self.buy_volume / total_flow > 0.55) if total_flow > 0 else False
        flow_confirms_sell = (self.sell_volume / total_flow > 0.55) if total_flow > 0 else False

        ema_up = self.ema_fast.value > self.ema_slow.value
        ema_down = self.ema_fast.value < self.ema_slow.value

        # LONG signal
        if ema_up and buy_ratio >= threshold and flow_confirms_buy:
            if self._last_signal != "LONG":
                self._last_signal = "LONG"
                logger.debug(
                    f"{self.pair} LONG | EMA {self.ema_fast.value:.4f}>{self.ema_slow.value:.4f} "
                    f"| OB buy={buy_ratio:.2%} | flow buy={self.buy_volume:.2f}"
                )
                return "LONG"

        # SHORT signal
        elif ema_down and sell_ratio >= threshold and flow_confirms_sell:
            if self._last_signal != "SHORT":
                self._last_signal = "SHORT"
                logger.debug(
                    f"{self.pair} SHORT | EMA {self.ema_fast.value:.4f}<{self.ema_slow.value:.4f} "
                    f"| OB sell={sell_ratio:.2%} | flow sell={self.sell_volume:.2f}"
                )
                return "SHORT"

        else:
            self._last_signal = "NONE"

        return "NONE"
