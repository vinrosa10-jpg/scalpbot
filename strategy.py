# strategy.py -- ScalpBot v2
# Momentum + Breakout scalping on 1m candles, USDC pairs
# TP: 0.3% / SL: 0.15%

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
    def __init__(self, o: float, h: float, l: float, c: float, v: float = 0):
        self.open  = o
        self.high  = h
        self.low   = l
        self.close = c
        self.vol   = v
        self.body        = abs(c - o)
        self.upper_wick  = h - max(o, c)
        self.lower_wick  = min(o, c) - l
        self.total_range = h - l
        self.bullish = c > o
        self.bearish = c < o


class ScalpingStrategy:
    def __init__(self, pair: str, config: Config):
        self.pair   = pair
        self.config = config

        self.ema_trend = EMA(50)
        self.ema_fast  = EMA(config.ema_fast)
        self.ema_slow  = EMA(config.ema_slow)

        self.candles: deque      = deque(maxlen=100)
        self.bid_volume          = 0.0
        self.ask_volume          = 0.0
        self.trade_window: deque = deque(maxlen=200)

        self.last_close: Optional[float] = None
        self._warmed_up               = False
        self._last_signal: SignalType = "NONE"
        self._last_candle_time: int   = 0
        self._debug_logged_at: int    = 0

        self._vol_avg = 0.0
        self._atr     = 0.0

    async def warm_up_from_api(self, client):
        try:
            interval = self.config.kline_interval
            logger.info(f"? {self.pair} -- loading {interval} candles (scalping 1m)...")
            klines = await client.get_klines(self.pair, interval=interval, limit=100)
            if klines and len(klines) >= 20:
                for k in klines[:-1]:
                    c = Candle(
                        float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                        float(k[5])
                    )
                    self.candles.append(c)
                    self.ema_trend.update(c.close)
                    self.ema_fast.update(c.close)
                    self.ema_slow.update(c.close)
                self._update_vol_avg()
                self._update_atr()
                self.last_close = float(klines[-2][4])
                self._warmed_up = True
                logger.info(
                    f"? {self.pair} warmed up | "
                    f"EMA50={self.ema_trend.value:.4f} "
                    f"EMA9={self.ema_fast.value:.4f} "
                    f"ATR={self._atr:.5f} "
                    f"VolAvg={self._vol_avg:.1f}"
                )
            else:
                logger.warning(f"{self.pair} -- dati insufficienti per warm-up")
        except Exception as e:
            logger.warning(f"{self.pair} warm-up failed: {e}")

    def update_kline(self, data: dict):
        k = data["k"]
        o = float(k["o"]); h = float(k["h"])
        l = float(k["l"]); c = float(k["c"])
        v = float(k.get("v", 0))
        t = int(k["t"])
        is_closed = k.get("x", False)

        self.last_close = c

        if is_closed and t != self._last_candle_time:
            self._last_candle_time = t
            self.candles.append(Candle(o, h, l, c, v))
            self.ema_trend.update(c)
            self.ema_fast.update(c)
            self.ema_slow.update(c)
            self._update_vol_avg()
            self._update_atr()
            if not self._warmed_up and self.ema_trend.value:
                self._warmed_up = True
            self._last_signal     = "NONE"
            self._debug_logged_at = 0

    def update_orderbook(self, data: dict):
        depth = self.config.ob_depth_levels
        bids  = data.get("bids", [])[:depth]
        asks  = data.get("asks", [])[:depth]
        self.bid_volume = sum(float(b[1]) for b in bids)
        self.ask_volume = sum(float(a[1]) for a in asks)

    def update_trade(self, trade: dict):
        qty = float(trade["q"])
        is_buyer_maker = trade["m"]
        self.trade_window.append({
            "qty":  qty,
            "sell": is_buyer_maker,
            "buy":  not is_buyer_maker,
        })

    def _ob_ratio(self) -> tuple:
        total = self.bid_volume + self.ask_volume
        if total == 0:
            return 0.5, 0.5
        return self.bid_volume / total, self.ask_volume / total

    def _flow_ratio(self) -> tuple:
        buy  = sum(t["qty"] for t in self.trade_window if t["buy"])
        sell = sum(t["qty"] for t in self.trade_window if t["sell"])
        total = buy + sell
        if total == 0:
            return 0.5, 0.5
        return buy / total, sell / total

    def _update_vol_avg(self):
        if len(self.candles) < 5:
            return
        recent = list(self.candles)[-20:]
        vols   = [c.vol for c in recent if c.vol > 0]
        if vols:
            self._vol_avg = sum(vols) / len(vols)

    def _update_atr(self):
        if len(self.candles) < 5:
            return
        candles = list(self.candles)[-14:]
        trs = [c.total_range for c in candles if c.total_range > 0]
        if trs:
            self._atr = sum(trs) / len(trs)

    def _momentum_bull(self, candles: list) -> bool:
        if len(candles) < 2:
            return False
        last = candles[-1]
        if not last.bullish or last.total_range == 0:
            return False
        if last.body / last.total_range < 0.55:
            return False
        if self.ema_fast.value and last.close < self.ema_fast.value:
            return False
        if self._vol_avg > 0 and last.vol > 0 and last.vol < self._vol_avg * 1.05:
            return False
        return True

    def _momentum_bear(self, candles: list) -> bool:
        if len(candles) < 2:
            return False
        last = candles[-1]
        if not last.bearish or last.total_range == 0:
            return False
        if last.body / last.total_range < 0.55:
            return False
        if self.ema_fast.value and last.close > self.ema_fast.value:
            return False
        if self._vol_avg > 0 and last.vol > 0 and last.vol < self._vol_avg * 1.05:
            return False
        return True

    def _ema_cross_bull(self) -> bool:
        if not self.ema_fast.value or not self.ema_slow.value:
            return False
        return self.ema_fast.value > self.ema_slow.value

    def _ema_cross_bear(self) -> bool:
        if not self.ema_fast.value or not self.ema_slow.value:
            return False
        return self.ema_fast.value < self.ema_slow.value

    def _breakout_bull(self, candles: list) -> bool:
        if len(candles) < 6:
            return False
        last  = candles[-1]
        prev5 = candles[-6:-1]
        high5 = max(c.high for c in prev5)
        if last.close <= high5:
            return False
        if self._vol_avg > 0 and last.vol > 0 and last.vol < self._vol_avg * 1.15:
            return False
        return True

    def _breakout_bear(self, candles: list) -> bool:
        if len(candles) < 6:
            return False
        last  = candles[-1]
        prev5 = candles[-6:-1]
        low5  = min(c.low for c in prev5)
        if last.close >= low5:
            return False
        if self._vol_avg > 0 and last.vol > 0 and last.vol < self._vol_avg * 1.15:
            return False
        return True

    def get_signal(self) -> SignalType:
        if not self._warmed_up or self.ema_trend.value is None:
            return "NONE"
        if len(self.candles) < 10:
            return "NONE"

        price = self.last_close or 0
        ema50 = self.ema_trend.value

        trend_up   = price > ema50
        trend_down = price < ema50

        dist_pct = abs(price - ema50) / ema50 * 100
        if dist_pct < 0.03:
            return "NONE"

        candles = list(self.candles)

        buy_ratio, sell_ratio = self._ob_ratio()
        buy_flow,  sell_flow  = self._flow_ratio()

        ob_bull   = buy_ratio  >= 0.49
        ob_bear   = sell_ratio >= 0.49
        flow_bull = buy_flow   >= 0.46
        flow_bear = sell_flow  >= 0.46

        long_pattern = (
            self._momentum_bull(candles) or
            (self._ema_cross_bull() and candles[-1].bullish) or
            self._breakout_bull(candles)
        )
        short_pattern = (
            self._momentum_bear(candles) or
            (self._ema_cross_bear() and candles[-1].bearish) or
            self._breakout_bear(candles)
        )

        if trend_up and long_pattern and ob_bull and flow_bull and self._last_signal != "LONG":
            pattern = (
                "Momentum" if self._momentum_bull(candles) else
                "EMACross" if self._ema_cross_bull() else
                "Breakout"
            )
            self._last_signal = "LONG"
            logger.info(
                f"? {self.pair} LONG [{pattern}] | "
                f"P={price:.4f} EMA50={ema50:.4f} dist={dist_pct:.3f}% | "
                f"OB={buy_ratio:.0%} flow={buy_flow:.0%}"
            )
            return "LONG"

        if trend_down and short_pattern and ob_bear and flow_bear and self._last_signal != "SHORT":
            pattern = (
                "Momentum" if self._momentum_bear(candles) else
                "EMACross" if self._ema_cross_bear() else
                "Breakout"
            )
            self._last_signal = "SHORT"
            logger.info(
                f"? {self.pair} SHORT [{pattern}] | "
                f"P={price:.4f} EMA50={ema50:.4f} dist={dist_pct:.3f}% | "
                f"OB={sell_ratio:.0%} flow={sell_flow:.0%}"
            )
            return "SHORT"

        if self._debug_logged_at != self._last_candle_time and (long_pattern or short_pattern):
            self._debug_logged_at = self._last_candle_time
            direction = "LONG" if long_pattern else "SHORT"
            trend_ok  = trend_up if long_pattern else trend_down
            ratio     = buy_ratio if long_pattern else sell_ratio
            flow      = buy_flow  if long_pattern else sell_flow
            logger.debug(
                f"? {self.pair} {direction} bloccato | "
                f"trend={'OK' if trend_ok else 'NO dist='+str(round(dist_pct,3))+'%'} "
                f"ob={'OK' if (ob_bull if long_pattern else ob_bear) else 'NO '+str(round(ratio*100,1))+'%'} "
                f"flow={'OK' if (flow_bull if long_pattern else flow_bear) else 'NO '+str(round(flow*100,1))+'%'}"
            )

        return "NONE"
