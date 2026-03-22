"""
Pair Selector - Trova automaticamente le migliori coppie per scalping.

Criteri di selezione:
1. Volume 24h alto (liquidità)
2. Volatilità sufficiente (price change %)
3. Solo coppie USDT
4. Esclude stablecoin e coppie illiquide

Aggiorna la lista ogni 30 minuti.
"""

import asyncio
from typing import List, Dict
from loguru import logger


# Coppie da escludere sempre (stablecoin, troppo stabili)
BLACKLIST = {
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDPUSDT",
    "DAIUSDT", "FRAXUSDT", "EURUSDT", "GBPUSDT",
    "SUSDT", "STETHUSDT"
}


class PairSelector:
    def __init__(self, client, config):
        self.client = client
        self.config = config
        self.selected_pairs: List[str] = []
        self._update_task = None

    async def start(self):
        """Avvia il selettore e aggiorna periodicamente."""
        await self._update_pairs()
        self._update_task = asyncio.create_task(self._periodic_update())

    async def stop(self):
        if self._update_task:
            self._update_task.cancel()

    async def get_pairs(self) -> List[str]:
        """Ritorna le coppie selezionate correnti."""
        if not self.selected_pairs:
            await self._update_pairs()
        return self.selected_pairs

    async def _periodic_update(self):
        """Aggiorna la lista ogni 30 minuti."""
        while True:
            await asyncio.sleep(30 * 60)
            logger.info("🔄 Aggiornamento coppie in corso...")
            await self._update_pairs()

    async def _update_pairs(self):
        """Analizza il mercato e seleziona le migliori coppie."""
        try:
            # Fetch tutti i ticker 24h da Spot
            spot_tickers = await self._fetch_tickers("SPOT")
            futures_tickers = await self._fetch_tickers("FUTURES") if self.config.enable_futures else []

            # Combina e deduplica
            all_tickers = self._merge_tickers(spot_tickers, futures_tickers)

            # Filtra e punteggia
            scored = self._score_pairs(all_tickers)

            # Prendi le top N
            top_n = self.config.max_pairs
            top_pairs = [p["symbol"] for p in scored[:top_n]]

            if top_pairs:
                self.selected_pairs = top_pairs
                logger.info(f"✅ Coppie selezionate ({len(top_pairs)}): {', '.join(top_pairs)}")
                self._log_details(scored[:top_n])
            else:
                logger.warning("⚠️  Nessuna coppia trovata, uso quelle di fallback")
                self.selected_pairs = self.config.pairs  # fallback

        except Exception as e:
            logger.error(f"Errore selezione coppie: {e}")
            if not self.selected_pairs:
                self.selected_pairs = self.config.pairs  # fallback

    async def _fetch_tickers(self, market: str) -> List[dict]:
        """Fetch tutti i ticker 24h."""
        try:
            path = "/api/v3/ticker/24hr" if market == "SPOT" else "/fapi/v1/ticker/24hr"
            session = await self.client._get_session()
            base = self.client._spot_rest if market == "SPOT" else self.client._fut_rest
            async with session.get(base + path) as r:
                data = await r.json()
                for t in data:
                    t["_market"] = market
                return data
        except Exception as e:
            logger.error(f"Fetch ticker error [{market}]: {e}")
            return []

    def _merge_tickers(self, spot: List[dict], futures: List[dict]) -> Dict[str, dict]:
        """Unisce spot e futures, preferisce futures se disponibile."""
        merged = {}
        for t in spot:
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                merged[sym] = t
        for t in futures:
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                # Se già presente da spot, merge i dati
                if sym in merged:
                    merged[sym]["_futures_volume"] = float(t.get("quoteVolume", 0))
                else:
                    merged[sym] = t
        return merged

    def _score_pairs(self, tickers: Dict[str, dict]) -> List[dict]:
        """
        Punteggia ogni coppia su 3 criteri:
        - Volume 24h in USDT (peso 40%)
        - Volatilità = |price_change_pct| (peso 40%)  
        - Spread stimato (peso 20%) — inversamente proporzionale
        """
        scored = []

        for symbol, t in tickers.items():
            # Escludi blacklist
            if symbol in BLACKLIST:
                continue

            # Escludi leveraged token (UP/DOWN)
            if "UP" in symbol or "DOWN" in symbol or "BULL" in symbol or "BEAR" in symbol:
                continue

            try:
                volume_usdt = float(t.get("quoteVolume", 0))
                price_change_pct = abs(float(t.get("priceChangePercent", 0)))
                last_price = float(t.get("lastPrice", 0))
                high = float(t.get("highPrice", 0))
                low = float(t.get("lowPrice", 0))

                # Filtri minimi
                if volume_usdt < self.config.min_volume_usdt:
                    continue
                if price_change_pct < self.config.min_volatility_pct:
                    continue
                if last_price <= 0:
                    continue

                # Volatilità intraday (high-low / last)
                intraday_range = ((high - low) / last_price * 100) if last_price > 0 else 0

                # Score normalizzato (useremo ranking relativo)
                scored.append({
                    "symbol": symbol,
                    "volume_usdt": volume_usdt,
                    "price_change_pct": price_change_pct,
                    "intraday_range": intraday_range,
                    "last_price": last_price,
                    "_market": t.get("_market", "SPOT"),
                })

            except (ValueError, TypeError):
                continue

        if not scored:
            return scored

        # Normalizza e calcola score finale
        max_vol = max(p["volume_usdt"] for p in scored) or 1
        max_vol_change = max(p["price_change_pct"] for p in scored) or 1
        max_range = max(p["intraday_range"] for p in scored) or 1

        for p in scored:
            vol_score = (p["volume_usdt"] / max_vol) * 40
            change_score = (p["price_change_pct"] / max_vol_change) * 40
            range_score = (p["intraday_range"] / max_range) * 20
            p["score"] = vol_score + change_score + range_score

        # Ordina per score decrescente
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def _log_details(self, pairs: List[dict]):
        logger.info("─" * 60)
        logger.info(f"{'Coppia':<12} {'Volume 24h':>15} {'Variaz%':>8} {'Range%':>8} {'Score':>6}")
        logger.info("─" * 60)
        for p in pairs:
            vol_str = f"${p['volume_usdt']/1_000_000:.0f}M"
            logger.info(
                f"{p['symbol']:<12} {vol_str:>15} "
                f"{p['price_change_pct']:>7.2f}% "
                f"{p['intraday_range']:>7.2f}% "
                f"{p['score']:>6.1f}"
            )
        logger.info("─" * 60)
