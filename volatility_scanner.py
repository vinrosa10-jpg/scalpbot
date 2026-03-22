"""
Volatility Scanner - Monitora il mercato in tempo reale.

- Scansiona ogni 15 minuti tutte le coppie USDT
- Classifica per volatilità momento (ATR, price spike, volume surge)
- Rotazione automatica: abbandona coppie "piatte", entra su quelle esplosive
- Modalità aggressiva: cerca breakout e spike di volume
"""

import asyncio
import time
from typing import List, Dict, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class PairScore:
    symbol: str
    volume_usdt: float
    price_change_pct: float
    intraday_range_pct: float
    volume_surge: float        # Volume attuale vs media = momentum
    score: float
    last_updated: float = 0.0


BLACKLIST = {
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT",
    "DAIUSDT", "FRAXUSDT", "EURUSDT", "GBPUSDT",
    "SUSDT", "STETHUSDT"
}


class VolatilityScanner:
    def __init__(self, client, config):
        self.client = client
        self.config = config
        self.top_pairs: List[str] = []
        self.pair_scores: Dict[str, PairScore] = {}
        self._task: Optional[asyncio.Task] = None
        self._callbacks = []  # Chiamati quando la lista cambia

    def on_pairs_updated(self, callback):
        """Registra callback chiamato quando le coppie cambiano."""
        self._callbacks.append(callback)

    async def start(self):
        await self._scan()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def get_top_pairs(self) -> List[str]:
        if not self.top_pairs:
            await self._scan()
        return self.top_pairs

    async def _loop(self):
        while True:
            await asyncio.sleep(self.config.scan_interval_sec)
            old_pairs = set(self.top_pairs)
            await self._scan()
            new_pairs = set(self.top_pairs)

            added = new_pairs - old_pairs
            removed = old_pairs - new_pairs

            if added or removed:
                logger.info(f"🔄 Rotazione coppie | +{list(added)} -{list(removed)}")
                for cb in self._callbacks:
                    await cb(list(new_pairs), list(removed))

    async def _scan(self):
        try:
            tickers = await self._fetch_all_tickers()
            scored = self._score_all(tickers)
            top = scored[:self.config.max_pairs]
            self.top_pairs = [p.symbol for p in top]
            self.pair_scores = {p.symbol: p for p in scored}
            self._log_top(top)
        except Exception as e:
            logger.error(f"Scan error: {e}")

    async def _fetch_all_tickers(self) -> List[dict]:
        session = await self.client._get_session()
        url = self.client._spot_rest + "/api/v3/ticker/24hr"
        async with session.get(url) as r:
            return await r.json()

    def _score_all(self, tickers: List[dict]) -> List[PairScore]:
        results = []

        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if sym in BLACKLIST:
                continue
            if "UP" in sym or "DOWN" in sym or "BULL" in sym or "BEAR" in sym:
                continue

            try:
                volume = float(t.get("quoteVolume", 0))
                change_pct = abs(float(t.get("priceChangePercent", 0)))
                high = float(t.get("highPrice", 0))
                low = float(t.get("lowPrice", 0))
                last = float(t.get("lastPrice", 0))
                count = int(t.get("count", 0))  # numero di trade

                if volume < self.config.min_volume_usdt:
                    continue
                if last <= 0:
                    continue

                intraday_range = ((high - low) / last * 100) if last > 0 else 0

                # Volume surge: stima momentum (trade count / ora)
                volume_surge = count / 1440 if count > 0 else 0

                # SCORE AGGRESSIVO:
                # 50% volatilità intraday (range H-L)
                # 30% variazione % 24h
                # 20% volume
                results.append(PairScore(
                    symbol=sym,
                    volume_usdt=volume,
                    price_change_pct=change_pct,
                    intraday_range_pct=intraday_range,
                    volume_surge=volume_surge,
                    score=0.0,
                    last_updated=time.time(),
                ))
            except (ValueError, TypeError):
                continue

        if not results:
            return results

        max_range = max(p.intraday_range_pct for p in results) or 1
        max_change = max(p.price_change_pct for p in results) or 1
        max_vol = max(p.volume_usdt for p in results) or 1

        for p in results:
            p.score = (
                (p.intraday_range_pct / max_range) * 50 +
                (p.price_change_pct / max_change) * 30 +
                (p.volume_usdt / max_vol) * 20
            )

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _log_top(self, pairs: List[PairScore]):
        logger.info("━" * 65)
        logger.info(f"{'#':<3} {'Coppia':<12} {'Volume':>12} {'±24h':>7} {'Range':>7} {'Score':>6}")
        logger.info("━" * 65)
        for i, p in enumerate(pairs, 1):
            vol_str = f"${p.volume_usdt/1_000_000:.0f}M"
            logger.info(
                f"{i:<3} {p.symbol:<12} {vol_str:>12} "
                f"{p.price_change_pct:>6.1f}% "
                f"{p.intraday_range_pct:>6.1f}% "
                f"{p.score:>6.1f}"
            )
        logger.info("━" * 65)
