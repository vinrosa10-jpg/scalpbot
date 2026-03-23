"""
Bot Configuration
Edit .env file or set environment variables before running.
"""
import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv
load_dotenv()

@dataclass
class Config:
    # API Keys
    api_key: str = ""
    api_secret: str = ""

    # Markets
    enable_spot: bool = True
    enable_futures: bool = False

    # Pair selector
    auto_select_pairs: bool = True
    max_pairs: int = 3
    min_volume_usdt: float = 50_000_000
    min_volatility_pct: float = 1.0

    pairs: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT"
    ])

    # Strategy params
    ema_fast: int = 9
    ema_slow: int = 21
    ob_imbalance_threshold: float = 0.62
    ob_depth_levels: int = 10

    # Risk management
    position_size_usdt: float = 25.0
    max_open_trades: int = 3
    take_profit_pct: float = 0.008
    stop_loss_pct: float = 0.003
    max_daily_loss_usdt: float = 20.0
    daily_profit_target_pct: float = 0.20
    max_drawdown_pct: float = 0.15
    futures_leverage: int = 5

    # Execution
    order_type: str = "MARKET"
    limit_order_offset_pct: float = 0.0001
    order_timeout_sec: int = 60

    # Timeframe
    kline_interval: str = "1m"

    # Capital management
    starting_capital_usdt: float = 100.0
    position_pct_of_capital: float = 0.12
    max_position_usdt: float = 500.0

    # Fine giornata
    eod_usdt_pct: float = 0.80
    eod_usdc_pct: float = 0.10
    eod_bnb_pct: float = 0.10

    # Testnet
    testnet: bool = True

    @classmethod
    def load(cls) -> "Config":
        return cls(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
            enable_spot=os.getenv("ENABLE_SPOT", "true").lower() == "true",
            enable_futures=os.getenv("ENABLE_FUTURES", "false").lower() == "true",
            testnet=os.getenv("TESTNET", "true").lower() == "true",

            # Strategy
            ob_imbalance_threshold=float(os.getenv("OB_IMBALANCE_THRESHOLD", "0.62")),
            ema_fast=int(os.getenv("EMA_FAST", "9")),
            ema_slow=int(os.getenv("EMA_SLOW", "21")),

            # Risk
            position_size_usdt=float(os.getenv("POSITION_SIZE_USDT", "25")),
            max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "3")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.008")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.003")),
            max_daily_loss_usdt=float(os.getenv("MAX_DAILY_LOSS_USDT", "20")),
            daily_profit_target_pct=float(os.getenv("DAILY_PROFIT_TARGET_PCT", "0.20")),
            futures_leverage=int(os.getenv("FUTURES_LEVERAGE", "5")),

            # Capital
            starting_capital_usdt=float(os.getenv("STARTING_CAPITAL_USDT", "100")),
            position_pct_of_capital=float(os.getenv("POSITION_PCT_OF_CAPITAL", "0.12")),

            # Pairs
            auto_select_pairs=os.getenv("AUTO_SELECT_PAIRS", "true").lower() == "true",
            max_pairs=int(os.getenv("MAX_PAIRS", "3")),
            pairs=os.getenv("PAIRS", "BTCUSDT,ETHUSDT,BNBUSDT").split(","),

            # Execution
            order_type=os.getenv("ORDER_TYPE", "MARKET"),
            order_timeout_sec=int(os.getenv("ORDER_TIMEOUT_SEC", "60")),
            kline_interval=os.getenv("KLINE_INTERVAL", "1m"),
        )
