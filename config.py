# config.py -- ScalpBot
# Persistent state via SQLite. App controls everything at runtime.

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv
load_dotenv()


def _load_state() -> dict:
    try:
        import database as db
        return db.load_all_state()
    except Exception:
        return {}


def save_state(config: "Config"):
    try:
        import database as db
        db.save_all_state({
            "enable_spot":                str(config.enable_spot).lower(),
            "enable_futures":             str(config.enable_futures).lower(),
            "take_profit_pct":            str(config.take_profit_pct),
            "stop_loss_pct":              str(config.stop_loss_pct),
            "futures_position_size_usdt": str(config.futures_position_size_usdt),
            "position_size_usdt":         str(config.position_size_usdt),
            "spot_take_profit_pct":       str(config.spot_take_profit_pct),
            "spot_stop_loss_pct":         str(config.spot_stop_loss_pct),
            "max_open_trades":            str(config.max_open_trades),
            "max_daily_loss_usdt":        str(config.max_daily_loss_usdt),
        })
    except Exception:
        pass


@dataclass
class Config:
    # API Keys
    api_key: str = ""
    api_secret: str = ""
    futures_api_key: str = ""
    futures_api_secret: str = ""

    # Markets
    enable_spot: bool = False
    enable_futures: bool = False

    # Pairs
    auto_select_pairs: bool = False
    max_pairs: int = 6
    pairs: List[str] = field(default_factory=lambda: [
        "ETHUSDC", "BNBUSDC", "XRPUSDC",
        "SOLUSDC", "ADAUSDC", "DOGEUSDC"
    ])

    # Strategy
    ema_fast: int = 9
    ema_slow: int = 21
    ob_imbalance_threshold: float = 0.52
    ob_depth_levels: int = 10
    kline_interval: str = "1m"

    # Futures params
    futures_position_size_usdt: float = 25.0
    futures_leverage: int = 3
    take_profit_pct: float = 0.003
    stop_loss_pct: float = 0.0015

    # Spot params
    position_size_usdt: float = 100.0
    spot_take_profit_pct: float = 0.003
    spot_stop_loss_pct: float = 0.0015

    # Risk
    max_open_trades: int = 6
    max_daily_loss_usdt: float = 15.0
    daily_profit_target_pct: float = 99.0

    # Misc
    order_type: str = "MARKET"
    limit_order_offset_pct: float = 0.0001
    starting_capital_usdt: float = 100.0
    testnet: bool = True

    # EOD
    eod_usdt_pct: float = 0.80
    eod_usdc_pct: float = 0.10
    eod_bnb_pct: float = 0.10

    @classmethod
    def load(cls) -> "Config":
        env_spot    = os.getenv("ENABLE_SPOT",    "false").lower() == "true"
        env_futures = os.getenv("ENABLE_FUTURES", "false").lower() == "true"

        cfg = cls(
            api_key=os.getenv("BINANCE_SPOT_API_KEY", ""),
            api_secret=os.getenv("BINANCE_SPOT_API_SECRET", ""),
            futures_api_key=os.getenv("BINANCE_FUTURES_API_KEY", ""),
            futures_api_secret=os.getenv("BINANCE_FUTURES_API_SECRET", ""),
            enable_spot=env_spot,
            enable_futures=env_futures,
            testnet=os.getenv("TESTNET", "true").lower() == "true",
            ob_imbalance_threshold=float(os.getenv("OB_IMBALANCE_THRESHOLD", "0.52")),
            ema_fast=int(os.getenv("EMA_FAST", "9")),
            ema_slow=int(os.getenv("EMA_SLOW", "21")),
            kline_interval=os.getenv("KLINE_INTERVAL", "1m"),
            futures_position_size_usdt=float(os.getenv("FUTURES_POSITION_SIZE_USDT", "25")),
            futures_leverage=int(os.getenv("FUTURES_LEVERAGE", "3")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.003")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.0015")),
            position_size_usdt=float(os.getenv("POSITION_SIZE_USDT", "100")),
            spot_take_profit_pct=float(os.getenv("SPOT_TAKE_PROFIT_PCT", "0.003")),
            spot_stop_loss_pct=float(os.getenv("SPOT_STOP_LOSS_PCT", "0.0015")),
            max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "6")),
            max_daily_loss_usdt=float(os.getenv("MAX_DAILY_LOSS_USDT", "15")),
            daily_profit_target_pct=float(os.getenv("DAILY_PROFIT_TARGET_PCT", "99.0")),
            starting_capital_usdt=float(os.getenv("STARTING_CAPITAL_USDT", "100")),
            auto_select_pairs=os.getenv("AUTO_SELECT_PAIRS", "false").lower() == "true",
            pairs=os.getenv("PAIRS", "ETHUSDC,BNBUSDC,XRPUSDC,SOLUSDC,ADAUSDC,DOGEUSDC").split(","),
            order_type=os.getenv("ORDER_TYPE", "MARKET"),
        )

        # Override con stato DB -- DB ha priorita' sulle env var
        state = _load_state()
        if state:
            if "enable_spot" in state:
                db_spot = state["enable_spot"] == "true"
                cfg.enable_spot = db_spot if (not env_spot or db_spot) else env_spot
            if "enable_futures" in state:
                db_futures = state["enable_futures"] == "true"
                cfg.enable_futures = db_futures if (not env_futures or db_futures) else env_futures
            if "take_profit_pct" in state:
                cfg.take_profit_pct = float(state["take_profit_pct"])
            if "stop_loss_pct" in state:
                cfg.stop_loss_pct = float(state["stop_loss_pct"])
            if "futures_position_size_usdt" in state:
                cfg.futures_position_size_usdt = float(state["futures_position_size_usdt"])
            if "position_size_usdt" in state:
                cfg.position_size_usdt = float(state["position_size_usdt"])
            if "spot_take_profit_pct" in state:
                cfg.spot_take_profit_pct = float(state["spot_take_profit_pct"])
            if "spot_stop_loss_pct" in state:
                cfg.spot_stop_loss_pct = float(state["spot_stop_loss_pct"])
            if "max_open_trades" in state:
                cfg.max_open_trades = int(state["max_open_trades"])
            if "max_daily_loss_usdt" in state:
                cfg.max_daily_loss_usdt = float(state["max_daily_loss_usdt"])

            from loguru import logger
            logger.info(
                f"State loaded | "
                f"Spot={'ON' if cfg.enable_spot else 'OFF'} "
                f"Futures={'ON' if cfg.enable_futures else 'OFF'} "
                f"TP={cfg.take_profit_pct:.3%} SL={cfg.stop_loss_pct:.3%} "
                f"Size={cfg.position_size_usdt} MaxTrades={cfg.max_open_trades}"
            )
        else:
            from loguru import logger
            logger.info(
                f"No saved state -- using env vars | "
                f"Spot={'ON' if cfg.enable_spot else 'OFF'} "
                f"Futures={'ON' if cfg.enable_futures else 'OFF'} "
                f"Size={cfg.position_size_usdt}"
            )

        return cfg
