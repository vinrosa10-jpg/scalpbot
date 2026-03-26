"""
Strategy - Price Action Method
Pattern: Pin Bar + Engulfing + EMA200 trend filter
Timeframe: 15m
"""

from collections import deque
from typing import Literal, Optional, List
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
    def __init__(self, o, h, l, c):
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

        # Storico candele chiuse
        self.candles: deque = deque(maxlen=50)
        self.current_candle: Optional[Candle] = None

        self.bid_volume = 0.0
        self.ask_volume = 0.0
        self.trade_window = deque(maxlen=500)

        self.last_close: Optional[float] = None
        self._warmed_up = False
        self._last_signal: SignalType = "NONE"
        self._last_candle_time: int = 0

    async def warm_up_from_api(self, client):
        try:
            interval = self.config.kline_interval
            logger.info(f"📊 {self.pair} -- caricamento 200 candele storiche...")
            klines = await client.get_klines(self.pair, interval=interval, limit=220)
            if klines and len(klines) >= 50:
                for k in klines[:-1]:
                    c = Candle(
                        float(k[1]), float(k[2]),
                        float(k[3]), float(k[4])
                    )
                    self.candles.append(c)
                    self.ema_trend.update(c.close)
                    self.ema_fast.update(c.close)
                    self.ema_slow.update(c.close)

                self.last_close = float(klines[-2][4])
                self._warmed_up = True
                logger.info(
                    f"✅ {self.pair} EMA200={self.ema_trend.value:.2f} | "
                    f"Candele caricate: {len(self.candles)}"
                )
            else:
                logger.warning(f"⚠️ {self.pair} -- dati insufficienti")
        except Exception as e:
            logger.warning(f"⚠️ {self.pair} warm-up fallito: {e}")

    def update_kline(self, data: dict):
        k = data["k"]
        o = float(k["o"])
        h = float(k["h"])
        l = float(k["l"])
        c = float(k["c"])
        t = int(k["t"])
        is_closed = k.get("x", False)

        self.last_close = c
        self.current_candle = Candle(o, h, l, c)

        # Aggiorna solo su candela CHIUSA
        if is_closed and t != self._last_candle_time:
            self._last_candle_time = t
            self.candles.append(Candle(o, h, l, c))
            self.ema_trend.update(c)
            self.ema_fast.update(c)
            self.ema_slow.update(c)
            if not self._warmed_up and self.ema_trend.value:
                self._warmed_up = True
            # Reset segnale su nuova candela
            self._last_signal = "NONE"

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

    def _ob_ratio(self):
        total = self.bid_volume + self.ask_volume
        if total == 0:
            return 0.5, 0.5
        return self.bid_volume / total, self.ask_volume / total

    def _flow(self):
        buy = sum(t["qty"] for t in self.trade_window if t["buy"])
        sell = sum(t["qty"] for t in self.trade_window if t["sell"])
        total = buy + sell
        return (buy/total if total > 0 else 0.5,
                sell/total if total > 0 else 0.5)

    # ── PRICE ACTION PATTERNS ────────────────────────────────

    def _is_pin_bar_bullish(self, c: Candle) -> bool:
        """
        Pin Bar rialzista — lunga shadow in basso, piccolo body in alto.
        Segnale di inversione al rialzo.
        """
        if c.total_range == 0:
            return False
        return (
            c.lower_wick >= c.total_range * 0.6 and  # shadow >= 60% range
            c.body <= c.total_range * 0.3 and         # body piccolo
            c.upper_wick <= c.total_range * 0.2        # poca shadow sopra
        )

    def _is_pin_bar_bearish(self, c: Candle) -> bool:
        """
        Pin Bar ribassista — lunga shadow in alto, piccolo body in basso.
        Segnale di inversione al ribasso.
        """
        if c.total_range == 0:
            return False
        return (
            c.upper_wick >= c.total_range * 0.6 and
            c.body <= c.total_range * 0.3 and
            c.lower_wick <= c.total_range * 0.2
        )

    def _is_bullish_engulfing(self, prev: Candle, curr: Candle) -> bool:
        """
        Engulfing rialzista — candela verde che ingloba la rossa precedente.
        Forte segnale di inversione/continuazione al rialzo.
        """
        return (
            prev.bearish and
            curr.bullish and
            curr.open <= prev.close and
            curr.close >= prev.open and
            curr.body > prev.body * 0.8
        )

    def _is_bearish_engulfing(self, prev: Candle, curr: Candle) -> bool:
        """
        Engulfing ribassista — candela rossa che ingloba la verde precedente.
        """
        return (
            prev.bullish and
            curr.bearish and
            curr.open >= prev.close and
            curr.close <= prev.open and
            curr.body > prev.body * 0.8
        )

    def _is_strong_bullish_candle(self, c: Candle) -> bool:
        """Candela bullish forte — body > 60% del range."""
        if c.total_range == 0:
            return False
        return c.bullish and c.body >= c.total_range * 0.6

    def _is_strong_bearish_candle(self, c: Candle) -> bool:
        """Candela bearish forte — body > 60% del range."""
        if c.total_range == 0:
            return False
        return c.bearish and c.body >= c.total_range * 0.6

    def _get_support(self, lookback: int = 10) -> Optional[float]:
        """Supporto dinamico — minimo delle ultime N candele."""
        if len(self.candles) < lookback:
            return None
        recent = list(self.candles)[-lookback:]
        return min(c.low for c in recent)

    def _get_resistance(self, lookback: int = 10) -> Optional[float]:
        """Resistenza dinamica — massimo delle ultime N candele."""
        if len(self.candles) < lookback:
            return None
        recent = list(self.candles)[-lookback:]
        return max(c.high for c in recent)

    # ── MAIN SIGNAL ─────────────────────────────────────────

    def get_signal(self) -> SignalType:
        if not self._warmed_up:
            return "NONE"
        if self.ema_trend.value is None:
            return "NONE"
        if len(self.candles) < 3:
            return "NONE"

        price = self.last_close or 0
        ema200 = self.ema_trend.value

        # Trend principale
        trend_up = price > ema200
        trend_down = price < ema200

        # Distanza minima da EMA200 — evita zone laterali
        dist_pct = abs(price - ema200) / ema200 * 100
        if dist_pct < 0.1:
            return "NONE"

        # Candele recenti
        candles = list(self.candles)
        last = candles[-1]    # ultima candela chiusa
        prev = candles[-2]    # penultima

        # Order book e flow
        buy_ratio, sell_ratio = self._ob_ratio()
        buy_flow, sell_flow = self._flow()
        ob_threshold = self.config.ob_imbalance_threshold

        # Pattern rilevati
        pin_bull = self._is_pin_bar_bullish(last)
        pin_bear = self._is_pin_bar_bearish(last)
        eng_bull = self._is_bullish_engulfing(prev, last)
        eng_bear = self._is_bearish_engulfing(prev, last)
        strong_bull = self._is_strong_bullish_candle(last)
        strong_bear = self._is_strong_bearish_candle(last)

        # Conferma OB
        ob_bull = buy_ratio >= ob_threshold
        ob_bear = sell_ratio >= ob_threshold

        # Conferma flow
        flow_bull = buy_flow >= 0.55
        flow_bear = sell_flow >= 0.55

        # ── LONG ────────────────────────────────────────────
        # Condizioni: trend UP + pattern PA + conferma OB/flow
        long_pattern = pin_bull or eng_bull or strong_bull
        long_confirm = ob_bull and flow_bull

        if (trend_up and
                long_pattern and
                long_confirm and
                self._last_signal != "LONG"):

            pattern_name = ("PinBar" if pin_bull else
                           "Engulfing" if eng_bull else "StrongCandle")
            self._last_signal = "LONG"
            logger.info(
                f"📈 {self.pair} LONG [{pattern_name}] | "
                f"EMA200={ema200:.2f} dist={dist_pct:.2f}% | "
                f"OB={buy_ratio:.0%} flow={buy_flow:.0%}"
            )
            return "LONG"

        # ── SHORT ───────────────────────────────────────────
        short_pattern = pin_bear or eng_bear or strong_bear
        short_confirm = ob_bear and flow_bear

        if (trend_down and
                short_pattern and
                short_confirm and
                self._last_signal != "SHORT"):

            pattern_name = ("PinBar" if pin_bear else
                           "Engulfing" if eng_bear else "StrongCandle")
            self._last_signal = "SHORT"
            logger.info(
                f"📉 {self.pair} SHORT [{pattern_name}] | "
                f"EMA200={ema200:.2f} dist={dist_pct:.2f}% | "
                f"OB={sell_ratio:.0%} flow={sell_flow:.0%}"
            )
            return "SHORT"

        return "NONE"
