"""
MarketDataFetcher — Price history, VIX, sector ETF data
==========================================================
Uses yfinance (no API key needed) for historical OHLCV data.
Also fetches real-time quotes via Alpaca.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import yfinance as yf
import pandas as pd

logger = logging.getLogger("titan_trader")

# Sector ETF mapping — used to score macro sector momentum
SECTOR_ETFS = {
    "Technology":            "XLK",
    "Healthcare":            "XLV",
    "Financials":            "XLF",
    "Consumer Discretionary":"XLY",
    "Consumer Staples":      "XLP",
    "Industrials":           "XLI",
    "Energy":                "XLE",
    "Utilities":             "XLU",
    "Real Estate":           "XLRE",
    "Materials":             "XLB",
    "Communication Services":"XLC",
}

# Ticker → sector mapping for known universe
TICKER_SECTOR = {
    "AAPL": "Technology",   "MSFT": "Technology",   "GOOGL": "Technology",
    "META": "Technology",   "AMZN": "Consumer Discretionary",
    "NVDA": "Technology",   "TSLA": "Consumer Discretionary",
    "AMD":  "Technology",   "PLTR": "Technology",   "SNOW": "Technology",
    "NET":  "Technology",   "CRWD": "Technology",
    "BRK.B":"Financials",   "JPM":  "Financials",   "V": "Financials",
    "MA":   "Financials",
    "UNH":  "Healthcare",   "JNJ":  "Healthcare",   "LLY": "Healthcare",
    "PG":   "Consumer Staples","KO": "Consumer Staples","COST": "Consumer Staples",
    "CAT":  "Industrials",  "DE":   "Industrials",  "UPS": "Industrials",
    "ABBV": "Healthcare",   "T":    "Communication Services",
    "O":    "Real Estate",  "MAIN": "Financials",
    "GLD":  "Materials",    "XOM":  "Energy",       "CVX": "Energy",
}


class MarketDataFetcher:

    def __init__(self, config: Dict):
        self.config = config
        self._sector_cache: Dict = {}

    def get_price_history(self, ticker: str, days: int = 365) -> Optional[Dict]:
        """
        Fetch OHLCV history for a ticker.
        Returns dict with DataFrame + derived stats.
        """
        try:
            end   = datetime.today()
            start = end - timedelta(days=days + 50)  # extra buffer for indicators

            df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)

            if df.empty or len(df) < 50:
                logger.warning(f"  {ticker}: insufficient price history")
                return None

            # Flatten MultiIndex columns (yfinance 1.x returns MultiIndex for single tickers)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            # Deduplicate columns in case flattening creates duplicates
            df = df.loc[:, ~df.columns.duplicated()]
            # Squeeze any remaining Series columns (yfinance 1.2.0 compat)
            for col in list(df.columns):
                df[col] = df[col].squeeze()

            return {
                "ticker":        ticker,
                "df":            df,
                "current_price": float(df["Close"].iloc[-1]),
                "price_52w_high":float(df["Close"].max()),
                "price_52w_low": float(df["Close"].min()),
                "avg_volume_30d":float(df["Volume"].tail(30).mean()),
                "price_change_1d":  float((df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100),
                "price_change_1m":  float((df["Close"].iloc[-1] / df["Close"].iloc[-22] - 1) * 100),
                "price_change_3m":  float((df["Close"].iloc[-1] / df["Close"].iloc[-66] - 1) * 100),
                "price_change_1y":  float((df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100),
            }
        except Exception as e:
            logger.error(f"  {ticker} price history error: {e}")
            return None

    def get_market_context(self) -> Dict:
        """
        Fetch macro market context:
        - VIX (fear gauge) — high VIX = reduce risk
        - SPY trend (bull/bear market regime)
        - Sector rotation — which sectors are leading
        """
        try:
            # SPY and VIX
            spy = yf.download("SPY", period="6mo", progress=False, auto_adjust=True)
            vix = yf.download("^VIX", period="1mo", progress=False, auto_adjust=True)

            if isinstance(spy.columns, pd.MultiIndex):
                spy.columns = spy.columns.get_level_values(0)
            if isinstance(vix.columns, pd.MultiIndex):
                vix.columns = vix.columns.get_level_values(0)
            spy = spy.loc[:, ~spy.columns.duplicated()]
            vix = vix.loc[:, ~vix.columns.duplicated()]

            spy_close = spy["Close"].squeeze()
            vix_close = vix["Close"].squeeze()

            spy_current = float(spy_close.iloc[-1])
            spy_ma50    = float(spy_close.tail(50).mean())
            spy_ma200   = float(spy_close.tail(200).mean()) if len(spy) >= 200 else spy_ma50

            vix_current = float(vix_close.iloc[-1]) if not vix.empty else 20.0

            # Market regime
            if spy_current > spy_ma50 > spy_ma200:
                regime = "BULL"           # price above both MAs
            elif spy_current < spy_ma50 < spy_ma200:
                regime = "BEAR"           # price below both MAs
            else:
                regime = "TRANSITION"     # mixed signals

            # Risk-off signal
            if vix_current > 30:
                risk_env = "HIGH_FEAR"    # reduce position sizes
            elif vix_current > 20:
                risk_env = "ELEVATED"     # normal caution
            else:
                risk_env = "LOW_FEAR"     # normal/greedy market

            # Sector scores
            sector_scores = self._score_sectors()

            return {
                "regime":        regime,
                "risk_env":      risk_env,
                "vix":           round(vix_current, 2),
                "spy_price":     round(spy_current, 2),
                "spy_vs_ma50":   round((spy_current / spy_ma50 - 1) * 100, 2),
                "spy_vs_ma200":  round((spy_current / spy_ma200 - 1) * 100, 2),
                "sector_scores": sector_scores,
                "leading_sectors": sorted(sector_scores, key=sector_scores.get, reverse=True)[:3],
                "lagging_sectors": sorted(sector_scores, key=sector_scores.get)[:3],
            }

        except Exception as e:
            logger.error(f"Market context error: {e}")
            return {
                "regime": "UNKNOWN", "risk_env": "ELEVATED",
                "vix": 20.0, "sector_scores": {}
            }

    def _score_sectors(self) -> Dict[str, float]:
        """
        Score each sector ETF by 1-month momentum.
        Normalized to 0–1 scale.
        """
        scores = {}
        for sector, etf in SECTOR_ETFS.items():
            try:
                df = yf.download(etf, period="3mo", progress=False, auto_adjust=True)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.loc[:, ~df.columns.duplicated()]
                if not df.empty and len(df) >= 22:
                    close = df["Close"].squeeze()
                    momentum_1m = (close.iloc[-1] / close.iloc[-22] - 1)
                    momentum_3m = (close.iloc[-1] / close.iloc[0] - 1)
                    scores[sector] = round(float(momentum_1m * 0.6 + momentum_3m * 0.4), 4)
            except Exception:
                scores[sector] = 0.0

        # Normalize to 0–1
        if scores:
            min_s = min(scores.values())
            max_s = max(scores.values())
            rng   = max_s - min_s if max_s != min_s else 1
            scores = {k: round((v - min_s) / rng, 3) for k, v in scores.items()}

        return scores

    def get_sector_score(self, ticker: str, market_context: Dict) -> float:
        """Return 0–1 sector score for a given ticker."""
        sector = TICKER_SECTOR.get(ticker, "Technology")
        sector_scores = market_context.get("sector_scores", {})
        return sector_scores.get(sector, 0.5)
