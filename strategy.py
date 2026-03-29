"""
Price Action Strategy
Patterns: Pin Bar + Engulfing + Strong Candle
Filter: EMA200 trend + OB + Flow
Updates: Only on closed candles (no intracandle noise)
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

    def warm_up(self, prices: list):
        for p in prices:
            self.update(p)


class Candle:
    def __init__(self, o: float, h: float, l: float, c: float):
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.body = abs(c - o)
        self.upper_wick = h - max(o, c)
        self.lower_wick = min(o, c) - l
        self.total_range = h - l
        self.bullish = c > o
        self.bearish = c < o


class ScalpingStrategy:
    def __init__(self, pair: str, config: Config):
        self.pair = pair
        self.config = config

        self.ema_trend = EMA(200)
        self.ema_fast = EMA(config.ema_fast)
        self.ema_slow = EMA(config.ema_slow)

        self.candles: deque = deque(maxlen=50)
        self.bid_volume = 0.0
        self.ask_volume = 0.0
        self.trade_window: deque = deque(maxlen=500)

        self.last_close: Optional[float] = None
        self._warmed_up = False
        self._last_signal: SignalType = "NONE"
        self._last_candle_time: int = 0

        # Debug: track why signals are blocked (logged max 1 volta per candela)
        self._debug_logged_at: int = 0

    async def warm_up_from_api(self, client):
        """Load historical candles for accurate EMA200."""
        try:
            interval = self.config.kline_interval
            logger.info(f"📊 {self.pair} — loading {interval} candles...")
            klines = await client.get_klines(self.pair, interval=interval, limit=220)
            if klines and len(klines) >= 50:
                for k in klines[:-1]:
                    c = Candle(float(k[1]), float(k[2]), float(k[3]), float(k[4]))
                    self.candles.append(c)
                    self.ema_trend.update(c.close)
                    self.ema_fast.update(c.close)
                    self.ema_slow.update(c.close)
                self.last_close = float(klines[-2][4])
                self._warmed_up = True
                logger.info(
                    f"✅ {self.pair} EMA200={self.ema_trend.value:.2f} | "
                    f"Candles: {len(self.candles)} | "
                    f"Interval: {interval}"
                )
            else:
                logger.warning(f"⚠️ {self.pair} — insufficient data for warm-up")
        except Exception as e:
            logger.warning(f"⚠️ {self.pair} warm-up failed: {e}")

    def update_kline(self, data: dict):
        k = data["k"]
        o, h, l, c = float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"])
        t = int(k["t"])
        is_closed = k.get("x", False)

        self.last_close = c

        # Only update EMA and candle buffer on CLOSED candles
        if is_closed and t != self._last_candle_time:
            self._last_candle_time = t
            self.candles.append(Candle(o, h, l, c))
            self.ema_trend.update(c)
            self.ema_fast.update(c)
            self.ema_slow.update(c)
            if not self._warmed_up and self.ema_trend.value:
                self._warmed_up = True
            self._last_signal = "NONE"  # Reset on each new candle
            self._debug_logged_at = 0   # Permetti nuovo debug log per questa candela

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

    def _ob_ratio(self) -> tuple:
        total = self.bid_volume + self.ask_volume
        if total == 0:
            return 0.5, 0.5
        return self.bid_volume / total, self.ask_volume / total

    def _flow_ratio(self) -> tuple:
        buy = sum(t["qty"] for t in self.trade_window if t["buy"])
        sell = sum(t["qty"] for t in self.trade_window if t["sell"])
        total = buy + sell
        if total == 0:
            return 0.5, 0.5
        return buy / total, sell / total

    # ── PRICE ACTION PATTERNS ─────────────────────────────────

    def _pin_bar_bull(self, c: Candle) -> bool:
        """Bullish pin bar — long lower shadow, small body at top."""
        if c.total_range == 0:
            return False
        return (
            c.lower_wick >= c.total_range * 0.60 and
            c.body <= c.total_range * 0.30 and
            c.upper_wick <= c.total_range * 0.20
        )

    def _pin_bar_bear(self, c: Candle) -> bool:
        """Bearish pin bar — long upper shadow, small body at bottom."""
        if c.total_range == 0:
            return False
        return (
            c.upper_wick >= c.total_range * 0.60 and
            c.body <= c.total_range * 0.30 and
            c.lower_wick <= c.total_range * 0.20
        )

    def _engulfing_bull(self, prev: Candle, curr: Candle) -> bool:
        """Bullish engulfing — green candle engulfs previous red."""
        return (
            prev.bearish and curr.bullish and
            curr.open <= prev.close and
            curr.close >= prev.open and
            curr.body >= prev.body * 0.8
        )

    def _engulfing_bear(self, prev: Candle, curr: Candle) -> bool:
        """Bearish engulfing — red candle engulfs previous green."""
        return (
            prev.bullish and curr.bearish and
            curr.open >= prev.close and
            curr.close <= prev.open and
            curr.body >= prev.body * 0.8
        )

    def _strong_bull(self, c: Candle) -> bool:
        """Strong bullish candle — body > 60% of range."""
        if c.total_range == 0:
            return False
        return c.bullish and c.body >= c.total_range * 0.60

    def _strong_bear(self, c: Candle) -> bool:
        """Strong bearish candle — body > 60% of range."""
        if c.total_range == 0:
            return False
        return c.bearish and c.body >= c.total_range * 0.60

    # ── SIGNAL ────────────────────────────────────────────────

    def get_signal(self) -> SignalType:
        # Guards
        if not self._warmed_up:
            return "NONE"
        if self.ema_trend.value is None:
            return "NONE"
        if len(self.candles) < 20:
            return "NONE"

        price = self.last_close or 0
        ema200 = self.ema_trend.value

        # Trend filter
        trend_up = price > ema200
        trend_down = price < ema200

        # Distance from EMA200 — avoid choppy zones
        dist_pct = abs(price - ema200) / ema200 * 100
        if dist_pct < 0.15:
            return "NONE"

        candles = list(self.candles)
        last = candles[-1]
        prev = candles[-2]

        # FIX: soglie abbassate — 0.65/0.55 era troppo restrittivo su 15m
        # OB e flow misurati su tick real-time: soglie alte = zero segnali
        buy_ratio, sell_ratio = self._ob_ratio()
        buy_flow, sell_flow = self._flow_ratio()

        ob_threshold   = 0.52   # era 0.65
        flow_threshold = 0.50   # era 0.55

        ob_bull   = buy_ratio  >= ob_threshold
        ob_bear   = sell_ratio >= ob_threshold
        flow_bull = buy_flow   >= flow_threshold
        flow_bear = sell_flow  >= flow_threshold

        # Patterns
        long_pattern  = self._pin_bar_bull(last) or self._engulfing_bull(prev, last) or self._strong_bull(last)
        short_pattern = self._pin_bar_bear(last) or self._engulfing_bear(prev, last) or self._strong_bear(last)

        # Debug log — una volta per candela quando c'è un pattern ma qualcosa blocca
        should_debug = (self._debug_logged_at != self._last_candle_time)

        # LONG
        if trend_up and long_pattern and ob_bull and flow_bull and self._last_signal != "LONG":
            pattern = ("PinBar"      if self._pin_bar_bull(last) else
                       "Engulfing"   if self._engulfing_bull(prev, last) else "StrongCandle")
            self._last_signal = "LONG"
            logger.info(
                f"📈 {self.pair} LONG [{pattern}] | "
                f"EMA200={ema200:.2f} dist={dist_pct:.2f}% | "
                f"OB={buy_ratio:.0%} flow={buy_flow:.0%}"
            )
            return "LONG"

        # SHORT
        if trend_down and short_pattern and ob_bear and flow_bear and self._last_signal != "SHORT":
            pattern = ("PinBar"      if self._pin_bar_bear(last) else
                       "Engulfing"   if self._engulfing_bear(prev, last) else "StrongCandle")
            self._last_signal = "SHORT"
            logger.info(
                f"📉 {self.pair} SHORT [{pattern}] | "
                f"EMA200={ema200:.2f} dist={dist_pct:.2f}% | "
                f"OB={sell_ratio:.0%} flow={sell_flow:.0%}"
            )
            return "SHORT"

        # Debug: logga perché il pattern non si è convertito in segnale
        if should_debug and (long_pattern or short_pattern):
            self._debug_logged_at = self._last_candle_time
            direction = "LONG" if long_pattern else "SHORT"
            ratio     = buy_ratio  if long_pattern else sell_ratio
            flow      = buy_flow   if long_pattern else sell_flow
            trend_ok  = trend_up   if long_pattern else trend_down
            logger.debug(
                f"🔍 {self.pair} pattern {direction} trovato ma bloccato | "
                f"trend={'✅' if trend_ok else '❌'} "
                f"ob={'✅' if (ob_bull if long_pattern else ob_bear) else f'❌({ratio:.0%})'} "
                f"flow={'✅' if (flow_bull if long_pattern else flow_bear) else f'❌({flow:.0%})'} "
                f"dup={'✅' if self._last_signal != direction else '❌(già inviato)'}"
            )

        return "NONE"
