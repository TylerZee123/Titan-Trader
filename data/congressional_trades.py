"""
CongressionalTradesScanner — Free Alpha From Public Filings
=============================================================
Members of Congress must disclose stock trades within 45 days
under the STOCK Act. Their trades are statistically predictive.

Data source: https://efts.house.gov/LATEST/search-index?q=&dateRange=custom
             https://senate.gov/legislative/resources/STOCK_Act_Disclosures.htm
             https://housestockwatcher.com/api (free JSON API)
             https://senatestockwatcher.com/api (free JSON API)

Signals we look for:
  - Multiple congress members buying the same stock = very bullish
  - Committee chairs buying in their committee's sector = high edge
    (Armed Services chair buying defense, etc.)
  - Cluster buying within 2 weeks = coordinated signal
"""

import logging
import json
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List

logger = logging.getLogger("titan_trader")

HOUSE_API  = "https://housestockwatcher.com/api"
SENATE_API = "https://senatestockwatcher.com/api"

# Committee chairs get extra weight — they see things first
HIGH_VALUE_MEMBERS = {
    "Nancy Pelosi",       # historically exceptional returns
    "Paul Pelosi",
    "Dan Crenshaw",
    "Michael McCaul",     # Foreign Affairs — geopolitical edge
    "Michael Turner",     # Armed Services — defense edge
    "Patrick McHenry",    # Financial Services — finance edge
}


class CongressionalTradesScanner:

    def __init__(self):
        self._cache: Dict = {}
        self._last_fetch: Dict = {}

    def get_recent_trades(self, tickers: List[str], days_back: int = 30) -> Dict[str, Dict]:
        """
        Fetch recent congressional trades for universe tickers.
        Returns dict: ticker -> {buy_count, sell_count, notable_buyers, signal}
        """
        all_trades = self._fetch_all_recent(days_back)
        result     = {}

        for ticker in tickers:
            matching = [t for t in all_trades if t.get("ticker", "").upper() == ticker.upper()]
            if not matching:
                continue

            buys        = [t for t in matching if "purchase" in t.get("type", "").lower() or "buy" in t.get("type","").lower()]
            sells       = [t for t in matching if "sale" in t.get("type", "").lower() or "sell" in t.get("type","").lower()]
            notable     = [t["member"] for t in buys if t.get("member","") in HIGH_VALUE_MEMBERS]
            buy_count   = len(buys)
            sell_count  = len(sells)

            # Signal logic
            if buy_count >= 3 and not sells:
                signal = "STRONG_BULLISH"
            elif buy_count >= 2 and not notable:
                signal = "BULLISH"
            elif sell_count >= 3 and not buys:
                signal = "BEARISH"
            elif notable:
                signal = "NOTABLE_BUY"
            else:
                signal = "NEUTRAL"

            score_boost = 0
            if signal == "STRONG_BULLISH": score_boost = 8
            elif signal == "NOTABLE_BUY":  score_boost = 6
            elif signal == "BULLISH":      score_boost = 4
            elif signal == "BEARISH":      score_boost = -6

            result[ticker] = {
                "buy_count":      buy_count,
                "sell_count":     sell_count,
                "notable_buyers": notable,
                "signal":         signal,
                "score_boost":    score_boost,
                "recent_trades":  [
                    {
                        "member": t.get("member", ""),
                        "type":   t.get("type", ""),
                        "amount": t.get("amount", ""),
                        "date":   t.get("transaction_date", ""),
                    }
                    for t in (buys + sells)[:5]
                ],
            }

            if signal not in ("NEUTRAL",):
                logger.info(f"  Congress signal {ticker}: {signal} ({buy_count}B/{sell_count}S) notable={notable}")

        return result

    def _fetch_all_recent(self, days_back: int) -> List[Dict]:
        """Fetch from both House and Senate APIs."""
        all_trades = []

        for source, url in [("house", HOUSE_API), ("senate", SENATE_API)]:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 TitanTrader/1.0"}
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())

                # Handle different API response shapes
                trades = data if isinstance(data, list) else data.get("data", [])

                cutoff = datetime.now() - timedelta(days=days_back)
                for t in trades:
                    # Normalize date field
                    date_str = t.get("transaction_date") or t.get("date") or ""
                    try:
                        trade_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                        if trade_date >= cutoff:
                            t["_source"] = source
                            all_trades.append(t)
                    except Exception:
                        pass

            except Exception as e:
                logger.debug(f"Congress {source} API error: {e}")

        logger.info(f"  Congressional trades fetched: {len(all_trades)} in last {days_back} days")
        return all_trades
