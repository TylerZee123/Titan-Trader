"""
NewsSentimentFetcher
======================
Aggregates news from yfinance + runs basic sentiment scoring.
The AI signal layer (Claude) does the deeper analysis on top of this.
"""

import logging
import re
from typing import Dict, List
import yfinance as yf
from datetime import datetime, timedelta

logger = logging.getLogger("titan_trader")

# Positive and negative keywords for basic sentiment scoring
POSITIVE_WORDS = {
    "beat", "exceeded", "record", "growth", "raised", "upgrade", "outperform",
    "strong", "surge", "rally", "profit", "gain", "bullish", "positive",
    "innovation", "expansion", "partnership", "acquisition", "dividend",
    "buyback", "revenue", "earnings", "guidance", "raised guidance", "new high",
    "breakthrough", "launch", "awarded", "contract", "approval", "fda approved",
}

NEGATIVE_WORDS = {
    "miss", "missed", "below", "weak", "loss", "cut", "downgrade", "underperform",
    "decline", "drop", "fall", "bearish", "negative", "layoff", "lawsuit",
    "investigation", "fraud", "recall", "default", "debt", "bankruptcy",
    "warning", "lowered guidance", "concern", "risk", "volatile", "sell-off",
    "overvalued", "competition", "tariff", "regulation", "fine", "penalty",
}

HIGH_IMPACT_NEGATIVE = {
    "sec investigation", "fraud", "bankruptcy", "ceo resign", "ceo fired",
    "accounting irregularities", "restatement", "criminal", "indicted",
}


class NewsSentimentFetcher:

    def __init__(self, config: Dict):
        self.config = config
        self._cache: Dict = {}

    def get_sentiment(self, ticker: str) -> Dict:
        """
        Returns:
        - score: 0–1 (1 = very positive)
        - headlines: list of recent headlines
        - alerts: any high-impact negative signals
        """
        if ticker in self._cache:
            return self._cache[ticker]

        try:
            stock = yf.Ticker(ticker)
            news  = stock.news or []

            headlines = []
            for item in news[:15]:  # last 15 articles
                title = item.get("content", {}).get("title", "") or item.get("title", "")
                if title:
                    headlines.append(title)

            sentiment = self._score_headlines(headlines)
            alerts    = self._detect_alerts(headlines)

            result = {
                "ticker":    ticker,
                "score":     sentiment,
                "headlines": headlines[:10],
                "alerts":    alerts,
                "article_count": len(headlines),
            }

            self._cache[ticker] = result
            return result

        except Exception as e:
            logger.error(f"  {ticker} news error: {e}")
            return {"ticker": ticker, "score": 0.5, "headlines": [], "alerts": [], "article_count": 0}

    def _score_headlines(self, headlines: List[str]) -> float:
        """
        Basic keyword-based sentiment scoring.
        Returns 0–1.
        """
        if not headlines:
            return 0.5

        pos_hits = 0
        neg_hits = 0

        for headline in headlines:
            lower = headline.lower()
            for word in POSITIVE_WORDS:
                if word in lower:
                    pos_hits += 1
            for word in NEGATIVE_WORDS:
                if word in lower:
                    neg_hits += 1

        total = pos_hits + neg_hits
        if total == 0:
            return 0.5

        raw_score = pos_hits / total
        # Blend toward neutral (0.5) if few articles
        confidence = min(len(headlines) / 10, 1.0)
        return round(0.5 * (1 - confidence) + raw_score * confidence, 3)

    def _detect_alerts(self, headlines: List[str]) -> List[str]:
        """
        Detect high-impact negative events that should override scoring.
        These trigger immediate review regardless of other scores.
        """
        alerts = []
        combined = " ".join(headlines).lower()

        for phrase in HIGH_IMPACT_NEGATIVE:
            if phrase in combined:
                alerts.append(f"HIGH IMPACT: '{phrase}' detected in recent news")

        return alerts
