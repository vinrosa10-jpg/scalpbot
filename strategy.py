"""
Scalping Strategy ottimizzata per timeframe 15m
OBI + EMA Momentum + EMA200 Trend Filter + Conferma volume
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


class ScalpingStrategy:
    def __init__(self, pair: str, config: Config):
        self.pair = pair
        self.config = config

        self.ema_fast = EMA(config.ema_fast)   # EMA 9
        self.ema_slow = EMA(config.ema_slow)   # EMA 21
        self.ema_trend = EMA(200)              # EMA 200

        self.bid_volume = 0.0
        self.ask_volume = 0.0
        self.trade_window = deque(maxlen=500)

        self.last_close: Optional[float] = None
        self.last_open: Optional[float] = None
        self.last_high: Optional[float] = None
        self.last_low: Optional[float] = None
        self.prev_close: Optional[float] = None

        self._last_signal: SignalType = "NONE"
        self._warmed_up = False
        self._signal_count = 0  # quante volte consecutivo stesso segnale
        self._last_raw_signal: SignalType = "NONE"

    async def warm_up_from_api(self, client):
        """Carica candele storiche per EMA200 accurata."""
        try:
            interval = self.config.kline_interval
            logger.info(f"📊 {self.pair} -- caricamento 200 candele storiche...")
            klines = await client.get_klines(self.pair, interval=interval, limit=220)
            if klines and len(klines) >= 200:
                closes = [float(k[4]) for k in klines[:-1]]
                self.ema_fast.warm_up(closes[-50:])
                self.ema_slow.warm_up(closes[-50:])
                self.ema_trend.warm_up(closes)
                self.last_close = closes[-1]
                self.prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
                self._warmed_up = True
                logger.info(
                    f"✅ {self.pair} EMA200={self.ema_trend.value:.2f} | "
                    f"EMA{self.config.ema_fast}={self.ema_fast.value:.2f} | "
                    f"EMA{self.config.ema_slow}={self.ema_slow.value:.2f}"
                )
            else:
                logger.warning(f"⚠️ {self.pair} -- dati insufficienti per warm-up")
        except Exception as e:
            logger.warning(f"⚠️ {self.pair} warm-up fallito: {e}")

    def update_kline(self, data: dict):
        k = data["k"]
        self.prev_close = self.last_close
        self.last_close = float(k["c"])
        self.last_open = float(k["o"])
        self.last_high = float(k["h"])
        self.last_low = float(k["l"])

        # Aggiorna EMA solo su candela chiusa
        if k.get("x", False):
            self.ema_fast.update(self.last_close)
            self.ema_slow.update(self.last_close)
            self.ema_trend.update(self.last_close)
            if not self._warmed_up and self.ema_trend.value is not None:
                self._warmed_up = True

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

    def _candle_bullish(self) -> bool:
        """Candela corrente bullish."""
        if self.last_close and self.last_open:
            return self.last_close > self.last_open
        return False

    def _candle_bearish(self) -> bool:
        """Candela corrente bearish."""
        if self.last_close and self.last_open:
            return self.last_close < self.last_open
        return False

    def get_signal(self) -> SignalType:
        if not self._warmed_up:
            return "NONE"
        if self.ema_fast.value is None or self.ema_slow.value is None:
            return "NONE"
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

        flow_confirms_buy = (buy_flow / total_flow > 0.55) if total_flow > 0 else False
        flow_confirms_sell = (sell_flow / total_flow > 0.55) if total_flow > 0 else False

        price = self.last_close or 0
        ema200 = self.ema_trend.value

        trend_up = price > ema200
        trend_down = price < ema200

        ema_up = self.ema_fast.value > self.ema_slow.value
        ema_down = self.ema_fast.value < self.ema_slow.value

        # Distanza minima EMA200 — evita zona laterale pericolosa
        dist_pct = abs(price - ema200) / ema200 * 100
        if dist_pct < 0.05:
            # Troppo vicino a EMA200 — zona di indecisione
            self._last_signal = "NONE"
            return "NONE"

        # LONG — trend UP + EMA cross + OB + candela bullish + flow
        if (trend_up and ema_up and
                buy_ratio >= threshold and
                flow_confirms_buy and
                self._candle_bullish()):

            raw = "LONG"
            if raw == self._last_raw_signal:
                self._signal_count += 1
            else:
                self._signal_count = 1
                self._last_raw_signal = raw

            # Richiede almeno 2 conferme consecutive per entrare
            if self._signal_count >= 2 and self._last_signal != "LONG":
                self._last_signal = "LONG"
                logger.info(
                    f"📈 {self.pair} LONG | "
                    f"EMA {self.ema_fast.value:.2f}>{self.ema_slow.value:.2f} | "
                    f"EMA200={ema200:.2f} | dist={dist_pct:.2f}% | "
                    f"OB={buy_ratio:.0%} | flow={buy_flow:.2f}/{total_flow:.2f}"
                )
                return "LONG"

        # SHORT — trend DOWN + EMA cross + OB + candela bearish + flow
        elif (trend_down and ema_down and
              sell_ratio >= threshold and
              flow_confirms_sell and
              self._candle_bearish()):

            raw = "SHORT"
            if raw == self._last_raw_signal:
                self._signal_count += 1
            else:
                self._signal_count = 1
                self._last_raw_signal = raw

            if self._signal_count >= 2 and self._last_signal != "SHORT":
                self._last_signal = "SHORT"
                logger.info(
                    f"📉 {self.pair} SHORT | "
                    f"EMA {self.ema_fast.value:.2f}<{self.ema_slow.value:.2f} | "
                    f"EMA200={ema200:.2f} | dist={dist_pct:.2f}% | "
                    f"OB={sell_ratio:.0%} | flow={sell_flow:.2f}/{total_flow:.2f}"
                )
                return "SHORT"

        else:
            self._last_signal = "NONE"
            self._signal_count = 0
            self._last_raw_signal = "NONE"

        return "NONE"
