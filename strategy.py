"""
Scalping Strategy: Order Book Imbalance + EMA Momentum + EMA200 Trend Filter

Entry conditions (LONG):
  - Prezzo > EMA200 (trend rialzista di lungo periodo)
  - EMA fast > EMA slow (momentum rialzista)
  - Order book buy pressure > ob_imbalance_threshold
  - Recent trade flow confirms buying

Entry conditions (SHORT - futures only):
  - Prezzo < EMA200 (trend ribassista di lungo periodo)
  - EMA fast < EMA slow (momentum ribassista)
  - Order book sell pressure > ob_imbalance_threshold
  - Recent trade flow confirms selling
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
        self._sum = 0.0

    def update(self, price: float) -> Optional[float]:
        if self.value is None:
            self._count += 1
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

        self.ema_fast = EMA(config.ema_fast)   # EMA 9
        self.ema_slow = EMA(config.ema_slow)   # EMA 21
        self.ema_trend = EMA(200)              # EMA 200 — filtro trend principale

        self.bid_volume = 0.0
        self.ask_volume = 0.0

        self.trade_window = deque(maxlen=200)

        self.last_close: Optional[float] = None
        self._last_signal: SignalType = "NONE"

    def update_kline(self, data: dict):
        close = float(data["k"]["c"])
        self.last_close = close
        self.ema_fast.update(close)
        self.ema_slow.update(close)
        self.ema_trend.update(close)

    def update_orderbook(self, data: dict):
        depth = self.config.ob_depth_levels
        bids = data.get("bids", [])[:depth]
        asks = data.get("asks", [])[:depth]
        self.bid_volume = sum(float(b[1]) for b in bids)
        self.ask_volume = sum(float(a[1]) for a in asks)

    def update_trade(self, trade: dict):
        qty = float(trade["q"])
        is_buyer_maker = trade["m"]
        self.trade_window.append({
            "qty": qty,
            "sell": is_buyer_maker,
            "buy": not is_buyer_maker,
        })

    def _flow(self):
        buy = sum(t["qty"] for t in self.trade_window if t["buy"])
        sell = sum(t["qty"] for t in self.trade_window if t["sell"])
        return buy, sell

    def get_signal(self) -> SignalType:
        # EMA fast e slow devono essere pronte
        if self.ema_fast.value is None or self.ema_slow.value is None:
            return "NONE"

        # EMA200 deve essere pronta
        if self.ema_trend.value is None:
            return "NONE"

        total_ob = self.bid_volume + self.ask_volume
        if total_ob == 0:
            return "NONE"

        buy_ratio = self.bid_volume / total_ob
        sell_ratio = self.ask_volume / total_ob
        threshold = self.config.ob_imbalance_threshold

        buy_flow, sell_flow = self._flow()
        total_flow = buy_flow + sell_flow

        flow_confirms_buy  = (buy_flow  / total_flow > 0.52) if total_flow > 0 else False
        flow_confirms_sell = (sell_flow / total_flow > 0.52) if total_flow > 0 else False

        ema_up   = self.ema_fast.value > self.ema_slow.value
        ema_down = self.ema_fast.value < self.ema_slow.value

        # Filtro trend EMA200
        price = self.last_close or 0
        trend_up   = price > self.ema_trend.value   # Mercato rialzista
        trend_down = price < self.ema_trend.value   # Mercato ribassista

        # LONG — solo se il mercato è in uptrend (prezzo > EMA200)
        if trend_up and ema_up and buy_ratio >= threshold and flow_confirms_buy:
            if self._last_signal != "LONG":
                self._last_signal = "LONG"
                logger.info(
                    f"📈 {self.pair} LONG | EMA {self.ema_fast.value:.2f}>{self.ema_slow.value:.2f} "
                    f"| EMA200={self.ema_trend.value:.2f} | OB={buy_ratio:.0%} "
                    f"| flow={buy_flow:.2f}/{total_flow:.2f}"
                )
                return "LONG"

        # SHORT — solo se il mercato è in downtrend (prezzo < EMA200)
        elif trend_down and ema_down and sell_ratio >= threshold and flow_confirms_sell:
            if self._last_signal != "SHORT":
                self._last_signal = "SHORT"
                logger.info(
                    f"📉 {self.pair} SHORT | EMA {self.ema_fast.value:.2f}<{self.ema_slow.value:.2f} "
                    f"| EMA200={self.ema_trend.value:.2f} | OB={sell_ratio:.0%} "
                    f"| flow={sell_flow:.2f}/{total_flow:.2f}"
                )
                return "SHORT"

        else:
            self._last_signal = "NONE"

        return "NONE"
