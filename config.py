"""
Bot Configuration - con persistenza su disco per parametri dinamici
"""
import os
import json
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv
load_dotenv()

STATE_FILE = "/app/bot_state.json"


def _load_state() -> dict:
    """Carica stato dinamico salvato su disco."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(config: "Config"):
    """Salva parametri dinamici su disco."""
    try:
        state = {
            "enable_spot": config.enable_spot,
            "enable_futures": config.enable_futures,
            "take_profit_pct": config.take_profit_pct,
            "stop_loss_pct": config.stop_loss_pct,
            "order_timeout_sec": config.order_timeout_sec,
            "position_size_usdt": config.position_size_usdt,
            "futures_position_size_usdt": config.futures_position_size_usdt,
            "spot_take_profit_pct": config.spot_take_profit_pct,
            "spot_stop_loss_pct": config.spot_stop_loss_pct,
            "spot_order_timeout_sec": config.spot_order_timeout_sec,
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        pass


@dataclass
class Config:
    api_key: str = ""
    api_secret: str = ""
    futures_api_key: str = ""
    futures_api_secret: str = ""

    enable_spot: bool = False
    enable_futures: bool = True

    auto_select_pairs: bool = False
    max_pairs: int = 3
    min_volume_usdt: float = 50_000_000
    min_volatility_pct: float = 1.0
    pairs: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "BNBUSDT"])

    ema_fast: int = 9
    ema_slow: int = 21
    ob_imbalance_threshold: float = 0.62
    ob_depth_levels: int = 10

    # Futures params
    futures_position_size_usdt: float = 50.0
    take_profit_pct: float = 0.002
    stop_loss_pct: float = 0.001
    order_timeout_sec: int = 180
    futures_leverage: int = 3

    # Spot params separati
    position_size_usdt: float = 25.0
    spot_take_profit_pct: float = 0.006
    spot_stop_loss_pct: float = 0.003
    spot_order_timeout_sec: int = 120

    max_open_trades: int = 3
    max_daily_loss_usdt: float = 10.0
    daily_profit_target_pct: float = 99.0
    max_drawdown_pct: float = 0.15

    order_type: str = "MARKET"
    limit_order_offset_pct: float = 0.0001
    kline_interval: str = "1m"
    starting_capital_usdt: float = 100.0
    position_pct_of_capital: float = 0.12
    max_position_usdt: float = 500.0

    eod_usdt_pct: float = 0.80
    eod_usdc_pct: float = 0.10
    eod_bnb_pct: float = 0.10
    testnet: bool = True

    @classmethod
    def load(cls) -> "Config":
        # 1 — Carica da variabili ambiente (default Render)
        cfg = cls(
            api_key=os.getenv("BINANCE_SPOT_API_KEY", ""),
            api_secret=os.getenv("BINANCE_SPOT_API_SECRET", ""),
            futures_api_key=os.getenv("BINANCE_FUTURES_API_KEY", ""),
            futures_api_secret=os.getenv("BINANCE_FUTURES_API_SECRET", ""),
            enable_spot=os.getenv("ENABLE_SPOT", "false").lower() == "true",
            enable_futures=os.getenv("ENABLE_FUTURES", "true").lower() == "true",
            testnet=os.getenv("TESTNET", "true").lower() == "true",
            ob_imbalance_threshold=float(os.getenv("OB_IMBALANCE_THRESHOLD", "0.62")),
            ema_fast=int(os.getenv("EMA_FAST", "9")),
            ema_slow=int(os.getenv("EMA_SLOW", "21")),
            # Futures
            futures_position_size_usdt=float(os.getenv("FUTURES_POSITION_SIZE_USDT", "50")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.002")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.001")),
            order_timeout_sec=int(os.getenv("ORDER_TIMEOUT_SEC", "180")),
            futures_leverage=int(os.getenv("FUTURES_LEVERAGE", "3")),
            # Spot
            position_size_usdt=float(os.getenv("POSITION_SIZE_USDT", "25")),
            spot_take_profit_pct=float(os.getenv("SPOT_TAKE_PROFIT_PCT", "0.006")),
            spot_stop_loss_pct=float(os.getenv("SPOT_STOP_LOSS_PCT", "0.003")),
            spot_order_timeout_sec=int(os.getenv("SPOT_ORDER_TIMEOUT_SEC", "120")),
            # Generali
            max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "3")),
            max_daily_loss_usdt=float(os.getenv("MAX_DAILY_LOSS_USDT", "10")),
            daily_profit_target_pct=float(os.getenv("DAILY_PROFIT_TARGET_PCT", "99.0")),
            starting_capital_usdt=float(os.getenv("STARTING_CAPITAL_USDT", "100")),
            auto_select_pairs=os.getenv("AUTO_SELECT_PAIRS", "false").lower() == "true",
            max_pairs=int(os.getenv("MAX_PAIRS", "3")),
            pairs=os.getenv("PAIRS", "BTCUSDT,ETHUSDT,BNBUSDT").split(","),
            order_type=os.getenv("ORDER_TYPE", "MARKET"),
            kline_interval=os.getenv("KLINE_INTERVAL", "1m"),
        )

        # 2 — Sovrascrive con stato salvato su disco (priorità app)
        state = _load_state()
        if state:
            if "enable_spot" in state:
                cfg.enable_spot = state["enable_spot"]
            if "enable_futures" in state:
                cfg.enable_futures = state["enable_futures"]
            if "take_profit_pct" in state:
                cfg.take_profit_pct = state["take_profit_pct"]
            if "stop_loss_pct" in state:
                cfg.stop_loss_pct = state["stop_loss_pct"]
            if "order_timeout_sec" in state:
                cfg.order_timeout_sec = state["order_timeout_sec"]
            if "futures_position_size_usdt" in state:
                cfg.futures_position_size_usdt = state["futures_position_size_usdt"]
            if "position_size_usdt" in state:
                cfg.position_size_usdt = state["position_size_usdt"]
            if "spot_take_profit_pct" in state:
                cfg.spot_take_profit_pct = state["spot_take_profit_pct"]
            if "spot_stop_loss_pct" in state:
                cfg.spot_stop_loss_pct = state["spot_stop_loss_pct"]
            if "spot_order_timeout_sec" in state:
                cfg.spot_order_timeout_sec = state["spot_order_timeout_sec"]

        return cfg
