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
    enable_futures: bool = True

    # Pair selector - auto sceglie le migliori coppie
    auto_select_pairs: bool = True         # True = selezione automatica
    max_pairs: int = 8                     # Quante coppie tradare contemporaneamente
    min_volume_usdt: float = 50_000_000    # Volume minimo 24h ($50M)
    min_volatility_pct: float = 1.0        # Variazione minima 24h (1%)

    # Fallback pairs (usate se auto_select fallisce o auto_select=False)
    pairs: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT",
        "SOLUSDT", "XRPUSDT"
    ])

    # Strategy params
    ema_fast: int = 9
    ema_slow: int = 21
    ob_imbalance_threshold: float = 0.65   # >65% one side = signal
    ob_depth_levels: int = 10              # Order book levels to read

    # Risk management
    position_size_usdt: float = 20.0       # Per trade in USDT
    max_open_trades: int = 4               # Simultaneously
    take_profit_pct: float = 0.003         # 0.3%
    stop_loss_pct: float = 0.0015          # 0.15%
    max_daily_loss_usdt: float = 50.0      # Bot stops if daily loss > this
    daily_profit_target_pct: float = 0.20  # 🎯 Stop alle +20% giornalieri
    max_drawdown_pct: float = 0.15         # Riduce size se drawdown > 15%
    futures_leverage: int = 5              # Leverage on futures only

    # Execution
    order_type: str = "LIMIT"             # LIMIT or MARKET
    limit_order_offset_pct: float = 0.0001 # 0.01% better than ask/bid
    order_timeout_sec: int = 10            # Cancel unfilled limit orders after N sec

    # Timeframe for EMA
    kline_interval: str = "1m"

    # Auto compounding
    starting_capital_usdt: float = 100.0  # Capitale iniziale
    position_pct_of_capital: float = 0.15 # 15% del capitale per trade
    max_position_usdt: float = 500.0       # Cap massimo per singolo trade

    # Fine giornata — allocazione automatica del capitale
    eod_usdt_pct: float = 0.80            # 80% rimane in USDT
    eod_usdc_pct: float = 0.10            # 10% convertito in USDC
    eod_bnb_pct: float = 0.10             # 10% convertito in BNB (commissioni -25%)

    # Testnet mode (HIGHLY RECOMMENDED to test first!)
    testnet: bool = True

    @classmethod
    def load(cls) -> "Config":
        return cls(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
            enable_spot=os.getenv("ENABLE_SPOT", "true").lower() == "true",
            enable_futures=os.getenv("ENABLE_FUTURES", "true").lower() == "true",
            testnet=os.getenv("TESTNET", "true").lower() == "true",
            position_size_usdt=float(os.getenv("POSITION_SIZE_USDT", "20")),
            max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "4")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.003")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.0015")),
            max_daily_loss_usdt=float(os.getenv("MAX_DAILY_LOSS_USDT", "50")),
            futures_leverage=int(os.getenv("FUTURES_LEVERAGE", "5")),
            pairs=os.getenv("PAIRS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT").split(","),
        )
