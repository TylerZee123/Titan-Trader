"""
EarningsCalendar — Earnings Awareness Module
=============================================
Fetches upcoming earnings dates for all universe stocks.
Flags positions that have earnings within the next 3 days.

Bot behavior:
  - DEFAULT: Hold through earnings if score >= 75 (UBER/HIGH confidence only)
  - MODERATE/LOW conviction: Exit day before earnings, re-enter after
  - Always flags earnings in the daily SMS so Tyler can override

This prevents being blindsided by a 20% overnight gap-down.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import yfinance as yf

logger = logging.getLogger("titan_trader")


class EarningsCalendar:

    def __init__(self):
        self._cache: Dict = {}

    def get_upcoming_earnings(self, tickers: List[str], days_ahead: int = 5) -> Dict[str, Dict]:
        """
        Returns dict of ticker -> earnings info for stocks
        with earnings in the next `days_ahead` days.
        """
        upcoming = {}
        cutoff   = datetime.now() + timedelta(days=days_ahead)

        for ticker in tickers:
            info = self._get_earnings_date(ticker)
            if info and info.get("earnings_date"):
                ed = info["earnings_date"]
                if datetime.now() <= ed <= cutoff:
                    days_until = (ed - datetime.now()).days
                    upcoming[ticker] = {
                        "earnings_date": ed.strftime("%Y-%m-%d"),
                        "days_until":    days_until,
                        "eps_estimate":  info.get("eps_estimate"),
                        "revenue_estimate": info.get("revenue_estimate"),
                        "surprise_history": info.get("surprise_history", []),
                    }
                    logger.info(f"  {ticker}: earnings in {days_until} days ({ed.strftime('%b %d')})")

        return upcoming

    def should_avoid_entry(self, ticker: str, score: float) -> bool:
        """
        Returns True if we should NOT enter a new position
        because earnings are too close.

        Rules:
        - Score < 75: avoid entering within 2 days of earnings
        - Score >= 75 (UBER): can enter, but position size is halved
        """
        info = self._get_earnings_date(ticker)
        if not info or not info.get("earnings_date"):
            return False

        days_until = (info["earnings_date"] - datetime.now()).days
        if days_until < 0:
            return False  # earnings already passed

        if days_until <= 1:
            return True   # always avoid day-of and day-before
        if days_until <= 2 and score < 75:
            return True   # avoid 2 days out unless uber confident

        return False

    def get_earnings_size_modifier(self, ticker: str, score: float) -> float:
        """
        Return position size modifier based on earnings proximity.
        1.0 = normal size, 0.5 = half size (earnings risk), 0.0 = skip
        """
        info = self._get_earnings_date(ticker)
        if not info or not info.get("earnings_date"):
            return 1.0

        days_until = (info["earnings_date"] - datetime.now()).days
        if days_until < 0:
            return 1.0

        if days_until == 0:
            return 0.0   # never enter day of earnings
        if days_until == 1:
            return 0.0 if score < 75 else 0.5
        if days_until <= 3:
            return 0.5 if score >= 75 else 0.25

        return 1.0

    def _get_earnings_date(self, ticker: str) -> Optional[Dict]:
        if ticker in self._cache:
            return self._cache[ticker]

        try:
            stock   = yf.Ticker(ticker)
            cal     = stock.calendar

            result = {}
            if cal is not None and not cal.empty:
                # Calendar is a DataFrame with dates as columns
                if "Earnings Date" in cal.index:
                    ed_val = cal.loc["Earnings Date"].iloc[0]
                    if hasattr(ed_val, "to_pydatetime"):
                        result["earnings_date"] = ed_val.to_pydatetime().replace(tzinfo=None)
                    elif isinstance(ed_val, datetime):
                        result["earnings_date"] = ed_val
                if "EPS Estimate" in cal.index:
                    result["eps_estimate"] = cal.loc["EPS Estimate"].iloc[0]
                if "Revenue Estimate" in cal.index:
                    result["revenue_estimate"] = cal.loc["Revenue Estimate"].iloc[0]

            # Historical earnings surprises
            try:
                hist = stock.earnings_history
                if hist is not None and not hist.empty:
                    surprises = []
                    for _, row in hist.tail(4).iterrows():
                        exp = row.get("epsEstimate", 0)
                        act = row.get("epsActual", 0)
                        if exp and act:
                            surprises.append(round((act - exp) / abs(exp) * 100, 1))
                    result["surprise_history"] = surprises
            except Exception:
                result["surprise_history"] = []

            self._cache[ticker] = result
            return result

        except Exception as e:
            logger.debug(f"  {ticker} earnings calendar error: {e}")
            self._cache[ticker] = {}
            return {}

    def format_earnings_warning(self, upcoming: Dict[str, Dict]) -> str:
        """Format earnings warnings for SMS/email."""
        if not upcoming:
            return ""
        lines = ["⚡ EARNINGS THIS WEEK:"]
        for ticker, info in sorted(upcoming.items(), key=lambda x: x[1]["days_until"]):
            lines.append(
                f"  {ticker}: {info['earnings_date']} "
                f"({info['days_until']}d) "
                f"{'— historically beats' if self._historically_beats(info) else ''}"
            )
        return "\n".join(lines)

    def _historically_beats(self, info: Dict) -> bool:
        surprises = info.get("surprise_history", [])
        if not surprises:
            return False
        positive = sum(1 for s in surprises if s > 0)
        return positive >= len(surprises) * 0.75  # beats 75%+ of the time
