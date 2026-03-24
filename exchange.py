"""
Binance Exchange Client
Handles REST API calls and WebSocket streams for Spot and Futures.
Supports separate API keys/secrets for SPOT and FUTURES.
"""

import asyncio
import json
import hmac
import hashlib
import ssl
import time
from typing import Optional, List, Tuple
from urllib.parse import urlencode

import aiohttp
import websockets
from loguru import logger
from config import Config


# Fix SSL su Mac
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


class BinanceClient:
    SPOT_REST = "https://api.binance.com"
    FUTURES_REST = "https://fapi.binance.com"
    SPOT_WS = "wss://stream.binance.com:9443/stream"
    FUTURES_WS = "wss://fstream.binance.com/stream"

    SPOT_REST_TEST = "https://testnet.binance.vision"
    FUTURES_REST_TEST = "https://testnet.binancefuture.com"
    SPOT_WS_TEST = "wss://stream.testnet.binance.vision/stream"
    FUTURES_WS_TEST = "wss://stream.binancefuture.com/ws"  # test futures ws

    def __init__(self, config: Config):
        self.config = config
        self.testnet = config.testnet

        if self.testnet:
            self._spot_rest = self.SPOT_REST_TEST
            self._fut_rest = self.FUTURES_REST_TEST
            self._spot_ws = self.SPOT_WS_TEST
            self._fut_ws = self.FUTURES_WS_TEST
        else:
            self._spot_rest = self.SPOT_REST
            self._fut_rest = self.FUTURES_REST
            self._spot_ws = self.SPOT_WS
            self._fut_ws = self.FUTURES_WS

        self._session: Optional[aiohttp.ClientSession] = None
        self._clock_offset: int = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    def _normalize_market(self, market: str) -> str:
        market = market.upper()
        if market not in ("SPOT", "FUTURES"):
            raise ValueError(f"Unsupported market: {market}")
        return market

    def _get_credentials(self, market: str) -> Tuple[str, str]:
        """
        Restituisce la coppia (api_key, api_secret) in base al mercato.
        Fallback alle vecchie config.api_key / config.api_secret se i campi separati non esistono.
        """
        market = self._normalize_market(market)

        if market == "SPOT":
            api_key = getattr(self.config, "spot_api_key", None) or getattr(self.config, "api_key", None)
            api_secret = getattr(self.config, "spot_api_secret", None) or getattr(self.config, "api_secret", None)
        else:
            api_key = getattr(self.config, "futures_api_key", None) or getattr(self.config, "api_key", None)
            api_secret = getattr(self.config, "futures_api_secret", None) or getattr(self.config, "api_secret", None)

        if not api_key or not api_secret:
            raise ValueError(f"Missing API credentials for market {market}")

        return api_key, api_secret

    async def sync_clock(self):
        """Sincronizza il clock con Binance."""
        try:
            path = "/api/v3/time"
            data = await self._get("SPOT", path)
            server_time = data["serverTime"]
            local_time = int(time.time() * 1000)
            self._clock_offset = server_time - local_time
            logger.info(f"🕐 Clock sync: offset={self._clock_offset}ms")
        except Exception as e:
            logger.warning(f"Clock sync failed: {e}")

    def _get_binance_time(self) -> int:
        return int(time.time() * 1000) + self._clock_offset

    def _sign(self, market: str, params: dict) -> str:
        """Restituisce query string firmata pronta per l'invio."""
        _, api_secret = self._get_credentials(market)

        signed_params = dict(params)
        signed_params["timestamp"] = self._get_binance_time()
        signed_params["recvWindow"] = 20000

        query = urlencode(sorted(signed_params.items()))
        signature = hmac.new(
            api_secret.encode(),
            query.encode(),
            hashlib.sha256
        ).hexdigest()

        return query + "&signature=" + signature

    def _headers(self, market: str) -> dict:
        api_key, _ = self._get_credentials(market)
        return {"X-MBX-APIKEY": api_key}

    def _base_url(self, market: str) -> str:
        market = self._normalize_market(market)
        return self._spot_rest if market == "SPOT" else self._fut_rest

    async def _handle_response(self, response: aiohttp.ClientResponse):
        data = await response.json()

        # Binance error standard
        if isinstance(data, dict) and "code" in data and response.status >= 400:
            raise Exception(f"Binance error [{response.status}]: {data}")

        # Alcuni endpoint possono restituire code anche con HTTP 200
        if isinstance(data, dict) and "code" in data and isinstance(data["code"], int) and data["code"] < 0:
            raise Exception(f"Binance error: {data}")

        return data

    async def _get(self, market: str, path: str, params: dict = None, signed: bool = False):
        session = await self._get_session()
        params = params or {}
        url = self._base_url(market) + path

        if signed:
            signed_query = self._sign(market, params)
            url = url + "?" + signed_query
            async with session.get(url, headers=self._headers(market)) as r:
                return await self._handle_response(r)
        else:
            # per endpoint pubblici gli header non servono
            async with session.get(url, params=params) as r:
                return await self._handle_response(r)

    async def _post(self, market: str, path: str, params: dict):
        session = await self._get_session()
        signed_query = self._sign(market, params)
        url = self._base_url(market) + path
        headers = {
            **self._headers(market),
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with session.post(url, data=signed_query, headers=headers) as r:
            return await self._handle_response(r)

    async def _delete(self, market: str, path: str, params: dict):
        session = await self._get_session()
        signed_query = self._sign(market, params)
        url = self._base_url(market) + path + "?" + signed_query

        async with session.delete(url, headers=self._headers(market)) as r:
            return await self._handle_response(r)

    async def get_ticker(self, pair: str, market: str) -> Optional[dict]:
        try:
            market = self._normalize_market(market)
            path = "/api/v3/ticker/price" if market == "SPOT" else "/fapi/v1/ticker/price"
            return await self._get(market, path, {"symbol": pair})
        except Exception as e:
            logger.error(f"get_ticker error [{market} {pair}]: {e}")
            return None

    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float],
        market: str
    ) -> dict:
        market = self._normalize_market(market)
        path = "/api/v3/order" if market == "SPOT" else "/fapi/v1/order"

        params = {
            "symbol": pair,
            "side": side,
            "type": order_type,
            "quantity": round(qty, 6),
        }

        if order_type == "LIMIT" and price is not None:
            params["price"] = round(price, 8)
            params["timeInForce"] = "GTC"

        logger.debug(f"Placing order: {params} [{market}]")
        return await self._post(market, path, params)

    async def cancel_order(self, pair: str, order_id: str, market: str):
        market = self._normalize_market(market)
        path = "/api/v3/order" if market == "SPOT" else "/fapi/v1/order"

        try:
            await self._delete(market, path, {"symbol": pair, "orderId": order_id})
        except Exception as e:
            logger.error(f"Cancel order error [{market} {pair} #{order_id}]: {e}")

    async def set_leverage_all(self, pairs: List[str], leverage: int):
        for pair in pairs:
            try:
                await self._post("FUTURES", "/fapi/v1/leverage", {
                    "symbol": pair,
                    "leverage": leverage
                })
                logger.info(f"⚡ Leverage set {leverage}x for {pair}")
            except Exception as e:
                logger.warning(f"Leverage error {pair}: {e}")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class DataFeed:
    """Manages WebSocket streams for klines, order book, and trades."""

    def __init__(self, client: BinanceClient, config: Config):
        self.client = client
        self.config = config
        self._tasks = []
        self._running = False

    async def start(self, pairs, on_kline, on_orderbook, on_trade):
        self._running = True
        interval = self.config.kline_interval

        for market in (["SPOT"] if self.client.testnet else ["SPOT", "FUTURES"]):
            ws_base = self.client._spot_ws if market == "SPOT" else self.client._fut_ws

            streams = []
            for pair in pairs:
                p = pair.lower()
                streams.append(f"{p}@kline_{interval}")
                streams.append(f"{p}@depth10@100ms")
                streams.append(f"{p}@trade")

            if self.client.testnet and market == "FUTURES":
                # futures testnet ws spesso usa /ws/<stream> più che /stream?streams=
                # qui manteniamo compatibilità minima usando stream singolo concatenato se serve
                logger.warning("FUTURES testnet websocket may require endpoint-specific adjustments.")

            url = ws_base + "?streams=" + "/".join(streams) if "/stream" in ws_base else ws_base

            task = asyncio.create_task(
                self._listen(url, market, on_kline, on_orderbook, on_trade)
            )
            self._tasks.append(task)

        logger.info(f"📡 WebSocket streams started for {len(pairs)} pairs")

    async def _listen(self, url, market, on_kline, on_orderbook, on_trade):
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ssl=SSL_CONTEXT) as ws:
                    logger.info(f"🔗 Connected: {market} stream")

                    async for raw in ws:
                        if not self._running:
                            break

                        msg = json.loads(raw)
                        data = msg.get("data", msg)
                        stream = msg.get("stream", "")

                        if "@kline" in stream:
                            pair = data["s"]
                            await on_kline(pair, data)

                        elif "@depth" in stream:
                            pair = stream.split("@")[0].upper()
                            await on_orderbook(pair, data)

                        elif "@trade" in stream:
                            pair = data["s"]
                            await on_trade(pair, data)

            except Exception as e:
                if self._running:
                    logger.warning(f"WebSocket disconnected [{market}]: {e} — reconnecting in 3s")
                    await asyncio.sleep(3)

    async def stop(self):
        self._running = False

        for task in self._tasks:
            task.cancel()

        self._tasks.clear()
