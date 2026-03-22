"""
End of Day Capital Manager
Quando il bot raggiunge il target giornaliero, converte automaticamente
il capitale nell'allocazione ottimale:
  - 80% USDT  (capitale operativo)
  - 10% USDC  (diversificazione stablecoin)
  - 10% BNB   (per commissioni ridotte 0.075%)
"""

import asyncio
from loguru import logger
from config import Config


class EndOfDayManager:
    def __init__(self, client, config: Config):
        self.client = client
        self.config = config
        self._done_today = False

    def reset_for_new_day(self):
        self._done_today = False
        logger.info("🌅 Nuovo giorno — EndOfDayManager resettato")

    async def run(self):
        """
        Chiamato quando il target giornaliero viene raggiunto.
        Esegue la conversione e logga il riepilogo.
        """
        if self._done_today:
            return
        self._done_today = True

        logger.info("💼 Avvio conversione capitale fine giornata...")

        try:
            balances = await self._get_balances()
            total_usdt = self._estimate_total_usdt(balances)

            if total_usdt <= 0:
                logger.warning("⚠️  Saldo USDT non rilevato, skip conversione")
                return

            await self._convert_to_optimal(total_usdt, balances)
            await self._log_final_summary(total_usdt)

        except Exception as e:
            logger.error(f"EndOfDay error: {e}")

    async def _get_balances(self) -> dict:
        """Recupera i saldi dal conto Spot."""
        try:
            data = await self.client._get("SPOT", "/api/v3/account", signed=True)
            balances = {}
            for b in data.get("balances", []):
                asset = b["asset"]
                free = float(b["free"])
                if free > 0:
                    balances[asset] = free
            return balances
        except Exception as e:
            logger.error(f"Get balances error: {e}")
            return {}

    def _estimate_total_usdt(self, balances: dict) -> float:
        """Stima il totale in USDT (approssimativo senza prezzi live)."""
        usdt = balances.get("USDT", 0)
        usdc = balances.get("USDC", 0)
        busd = balances.get("BUSD", 0)
        # USDC e BUSD sono ~1:1 con USDT
        return usdt + usdc + busd

    async def _convert_to_optimal(self, total_usdt: float, balances: dict):
        """
        Alloca il capitale secondo la strategia ottimale:
        80% USDT | 10% USDC | 10% BNB
        """
        target_usdc = round(total_usdt * self.config.eod_usdc_pct, 2)
        target_bnb_usdt = round(total_usdt * self.config.eod_bnb_pct, 2)

        current_usdc = balances.get("USDC", 0)
        current_bnb_usdt = balances.get("BNB", 0) * await self._get_bnb_price()

        logger.info(f"💰 Totale stimato: {total_usdt:.2f} USDT")
        logger.info(f"🎯 Target: 80% USDT | 10% USDC ({target_usdc:.2f}) | 10% BNB (~{target_bnb_usdt:.2f} USDT)")

        # Compra USDC se ne abbiamo meno del target
        usdc_needed = target_usdc - current_usdc
        if usdc_needed > 5:  # soglia minima $5
            await self._buy_with_usdt("USDCUSDT", usdc_needed)

        # Compra BNB se ne abbiamo meno del target
        bnb_needed_usdt = target_bnb_usdt - current_bnb_usdt
        if bnb_needed_usdt > 5:
            await self._buy_with_usdt("BNBUSDT", bnb_needed_usdt)

    async def _get_bnb_price(self) -> float:
        """Recupera il prezzo BNB/USDT."""
        try:
            ticker = await self.client.get_ticker("BNBUSDT", "SPOT")
            return float(ticker["price"]) if ticker else 300.0
        except Exception:
            return 300.0  # fallback

    async def _buy_with_usdt(self, pair: str, usdt_amount: float):
        """Compra una valuta usando USDT (ordine market)."""
        try:
            ticker = await self.client.get_ticker(pair, "SPOT")
            if not ticker:
                return
            price = float(ticker["price"])
            qty = usdt_amount / price

            logger.info(f"🔄 Acquisto {pair}: ~{usdt_amount:.2f} USDT → {qty:.4f} unità")

            await self.client.place_order(
                pair=pair,
                side="BUY",
                order_type="MARKET",
                qty=round(qty, 4),
                price=None,
                market="SPOT",
            )
            logger.success(f"✅ Convertiti {usdt_amount:.2f} USDT → {pair}")

        except Exception as e:
            logger.error(f"Conversione {pair} fallita: {e}")

    async def _log_final_summary(self, total_usdt: float):
        """Stampa il riepilogo finale della giornata."""
        await asyncio.sleep(3)  # Attendi che gli ordini si completino
        balances = await self._get_balances()

        usdt = balances.get("USDT", 0)
        usdc = balances.get("USDC", 0)
        bnb = balances.get("BNB", 0)
        bnb_price = await self._get_bnb_price()
        bnb_usdt = bnb * bnb_price

        logger.info("=" * 55)
        logger.info("🌙  RIEPILOGO FINE GIORNATA")
        logger.info("=" * 55)
        logger.info(f"  💵 USDT  : {usdt:>10.2f} USDT")
        logger.info(f"  💵 USDC  : {usdc:>10.2f} USDC")
        logger.info(f"  🟡 BNB   : {bnb:>10.4f} BNB (~{bnb_usdt:.2f} USDT)")
        logger.info(f"  📊 Totale: {usdt + usdc + bnb_usdt:>10.2f} USDT")
        logger.info("=" * 55)
        logger.info("😴 Bot in pausa — riprende domani automaticamente")
        logger.info("=" * 55)
