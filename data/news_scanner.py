"""
NewsScanner — Pre-Market & Post-Market Intelligence Engine
============================================================
Runs TWICE daily:
  - 8:00 AM ET  → Pre-market scan (before open)
  - 5:00 PM ET  → Post-market scan (after close)

Pulls news from multiple sources via yfinance + RSS feeds,
runs deep sentiment analysis with Claude, and flags:
  - Earnings surprises
  - CEO/executive changes
  - Macro events (Fed, CPI, jobs)
  - Sector-wide news
  - Individual stock catalysts or risks
  - Pre-market movers with unusual volume

Results are saved to Supabase and influence next trading session.
"""

import logging
import json
import re
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import yfinance as yf
import anthropic

logger = logging.getLogger("titan_trader")

# RSS feeds for broad market news (no API key needed)
NEWS_RSS_FEEDS = {
    "reuters_markets":   "https://feeds.reuters.com/reuters/businessNews",
    "yahoo_finance":     "https://finance.yahoo.com/rss/topstories",
    "seeking_alpha":     "https://seekingalpha.com/market_currents.xml",
    "marketwatch":       "https://feeds.marketwatch.com/marketwatch/topstories/",
    "cnbc_markets":      "https://www.cnbc.com/id/20910258/device/rss/rss.html",
}

# Economic events that require special treatment
HIGH_IMPACT_MACRO = [
    "federal reserve", "fed rate", "fomc", "interest rate",
    "cpi", "inflation", "jobs report", "nonfarm payroll",
    "gdp", "recession", "treasury", "yield curve",
    "earnings season", "s&p 500", "nasdaq", "dow jones",
    "tariff", "trade war", "geopolitical", "war", "sanctions",
]

SENTIMENT_SYSTEM_PROMPT = """You are an elite financial analyst specializing in news sentiment analysis for algorithmic trading.

Analyze the provided news and return ONLY a JSON object with this structure:
{
  "overall_market_sentiment": float,     // 0.0=bearish to 1.0=bullish
  "sentiment_label": string,             // "VERY_BEARISH"|"BEARISH"|"NEUTRAL"|"BULLISH"|"VERY_BULLISH"
  "macro_risk_level": string,            // "LOW"|"MODERATE"|"HIGH"|"EXTREME"
  "key_themes": [string],                // top 3-5 market themes right now
  "sector_impacts": {                    // sector: impact score -1.0 to 1.0
    "Technology": float,
    "Healthcare": float,
    "Financials": float,
    "Energy": float,
    "Consumer": float
  },
  "stock_mentions": [                    // stocks explicitly mentioned
    {"ticker": string, "sentiment": float, "reason": string}
  ],
  "macro_events": [string],              // any scheduled events today/tomorrow
  "trading_bias": string,                // "RISK_ON"|"RISK_OFF"|"NEUTRAL"
  "pre_market_movers": [string],         // notable pre-market movers if mentioned
  "summary": string                      // 2-sentence market narrative
}

Be precise and data-driven. No markdown, no preamble. Only valid JSON."""

STOCK_SENTIMENT_PROMPT = """You are an elite financial analyst. Analyze the news headlines for {ticker} ({company}) and return ONLY JSON:
{
  "ticker": "{ticker}",
  "sentiment_score": float,        // 0.0=very negative to 1.0=very positive
  "sentiment_label": string,       // "VERY_NEGATIVE"|"NEGATIVE"|"NEUTRAL"|"POSITIVE"|"VERY_POSITIVE"
  "confidence": float,             // 0.0-1.0 confidence in assessment
  "key_drivers": [string],         // top 2-3 sentiment drivers
  "risks_detected": [string],      // specific risks in news
  "catalysts_detected": [string],  // specific catalysts in news
  "earnings_related": bool,        // is this earnings news?
  "management_news": bool,         // CEO/executive news?
  "requires_immediate_review": bool, // should trader review this immediately?
  "action_bias": string            // "STRONG_BUY"|"BUY"|"HOLD"|"SELL"|"STRONG_SELL"|"NEUTRAL"
}
No markdown. Only valid JSON."""


class NewsScanner:
    """
    Dual-session news intelligence engine.
    Pre-market: sets the day's thesis and risk level.
    Post-market: evaluates what happened and prepares for tomorrow.
    """

    def __init__(self, config: Dict):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config["anthropic_api_key"])
        self.session_type = self._detect_session()

    def _detect_session(self) -> str:
        """Determine if we're running pre-market or post-market."""
        hour = datetime.now().hour
        if 5 <= hour < 9:
            return "PRE_MARKET"
        elif hour >= 16:
            return "POST_MARKET"
        else:
            return "INTRADAY"

    def run_full_scan(self, universe: List[str]) -> Dict:
        """
        Main entry point for news scanning.
        Returns comprehensive intelligence report.
        """
        logger.info(f"\n{'='*50}")
        logger.info(f"NEWS SCAN: {self.session_type} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"{'='*50}")

        # 1. Broad market news
        logger.info("Fetching broad market headlines...")
        market_headlines = self._fetch_market_headlines()

        # 2. Individual stock news
        logger.info(f"Fetching news for {len(universe)} stocks...")
        stock_news = {}
        for ticker in universe:
            news = self._fetch_stock_news(ticker)
            if news:
                stock_news[ticker] = news
            time.sleep(0.1)  # Be gentle with APIs

        # 3. AI-powered market sentiment
        logger.info("Running Claude sentiment analysis on market news...")
        market_sentiment = self._analyze_market_sentiment(market_headlines)

        # 4. Per-stock sentiment
        logger.info("Running per-stock sentiment analysis...")
        stock_sentiments = {}
        for ticker in universe:
            if ticker in stock_news and stock_news[ticker]:
                sentiment = self._analyze_stock_sentiment(
                    ticker=ticker,
                    headlines=stock_news[ticker],
                )
                stock_sentiments[ticker] = sentiment
                if sentiment.get("requires_immediate_review"):
                    logger.warning(f"  ⚠️  {ticker}: IMMEDIATE REVIEW — {sentiment.get('key_drivers', [])}")

        # 5. Compile intelligence report
        report = {
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "session":          self.session_type,
            "market_sentiment": market_sentiment,
            "stock_sentiments": stock_sentiments,
            "headline_count":   len(market_headlines),
            "stocks_analyzed":  len(stock_sentiments),
            "immediate_reviews": [
                t for t, s in stock_sentiments.items()
                if s.get("requires_immediate_review")
            ],
            "strong_buy_signals": [
                t for t, s in stock_sentiments.items()
                if s.get("action_bias") in ("STRONG_BUY", "BUY")
            ],
            "sell_signals": [
                t for t, s in stock_sentiments.items()
                if s.get("action_bias") in ("SELL", "STRONG_SELL")
            ],
        }

        self._log_report_summary(report)
        return report

    def _fetch_market_headlines(self) -> List[str]:
        """Fetch broad market headlines from RSS + yfinance."""
        headlines = []

        # yfinance market news (SPY, QQQ as proxies for market news)
        for proxy in ["SPY", "QQQ", "^VIX"]:
            try:
                ticker = yf.Ticker(proxy)
                news = ticker.news or []
                for item in news[:5]:
                    title = (item.get("content", {}).get("title", "")
                             or item.get("title", ""))
                    if title:
                        headlines.append(title)
            except Exception:
                pass

        # RSS feeds
        for source, url in NEWS_RSS_FEEDS.items():
            try:
                fetched = self._fetch_rss(url, max_items=5)
                headlines.extend(fetched)
            except Exception as e:
                logger.debug(f"RSS {source} failed: {e}")

        # Deduplicate
        seen = set()
        unique = []
        for h in headlines:
            key = h.lower()[:60]
            if key not in seen:
                seen.add(key)
                unique.append(h)

        logger.info(f"  Fetched {len(unique)} unique market headlines")
        return unique[:40]

    def _fetch_rss(self, url: str, max_items: int = 10) -> List[str]:
        """Simple RSS parser — no external dependencies."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                content = response.read().decode("utf-8", errors="ignore")

            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", content)
            if not titles:
                titles = re.findall(r"<title>(.*?)</title>", content)

            # Skip feed-level title (first item)
            return [t.strip() for t in titles[1:max_items+1] if t.strip()]
        except Exception:
            return []

    def _fetch_stock_news(self, ticker: str) -> List[str]:
        """Fetch recent news headlines for a specific ticker."""
        try:
            stock = yf.Ticker(ticker)
            news = stock.news or []
            headlines = []
            for item in news[:10]:
                title = (item.get("content", {}).get("title", "")
                         or item.get("title", ""))
                if title:
                    headlines.append(title)
            return headlines
        except Exception:
            return []

    def _analyze_market_sentiment(self, headlines: List[str]) -> Dict:
        """Use Claude to analyze broad market sentiment."""
        if not headlines:
            return {"overall_market_sentiment": 0.5, "sentiment_label": "NEUTRAL",
                    "trading_bias": "NEUTRAL", "macro_risk_level": "MODERATE"}

        headlines_text = "\n".join(f"- {h}" for h in headlines[:30])
        session_context = (
            "These are PRE-MARKET headlines. Markets have not opened yet."
            if self.session_type == "PRE_MARKET"
            else "These are POST-MARKET headlines. Markets have closed."
        )

        prompt = f"""{session_context}
Date: {datetime.now().strftime('%Y-%m-%d')}
Session: {self.session_type}

NEWS HEADLINES:
{headlines_text}

Analyze these headlines and return market sentiment JSON."""

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                system=SENTIMENT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
        except Exception as e:
            logger.error(f"Market sentiment analysis failed: {e}")
            return {
                "overall_market_sentiment": 0.5,
                "sentiment_label":          "NEUTRAL",
                "trading_bias":             "NEUTRAL",
                "macro_risk_level":         "MODERATE",
                "summary":                  "Sentiment analysis unavailable",
            }

    def _analyze_stock_sentiment(self, ticker: str, headlines: List[str]) -> Dict:
        """Deep per-stock sentiment analysis via Claude."""
        try:
            info = yf.Ticker(ticker).info
            company = info.get("longName", ticker)
        except Exception:
            company = ticker

        headlines_text = "\n".join(f"- {h}" for h in headlines[:10])

        system = STOCK_SENTIMENT_PROMPT.replace("{ticker}", ticker).replace("{company}", company)
        prompt = f"""NEWS FOR {ticker} ({company}):
{headlines_text}

Analyze and return sentiment JSON."""

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            result = json.loads(raw)
            result["headlines"] = headlines[:5]
            return result
        except Exception as e:
            logger.error(f"  {ticker} sentiment error: {e}")
            return {
                "ticker":          ticker,
                "sentiment_score": 0.5,
                "sentiment_label": "NEUTRAL",
                "confidence":      0.3,
                "requires_immediate_review": False,
                "action_bias":     "NEUTRAL",
            }

    def _log_report_summary(self, report: Dict):
        ms = report.get("market_sentiment", {})
        logger.info(f"\n{'─'*40}")
        logger.info(f"MARKET: {ms.get('sentiment_label')} "
                    f"(score: {ms.get('overall_market_sentiment', 0.5):.2f}) "
                    f"| Bias: {ms.get('trading_bias')} "
                    f"| Macro risk: {ms.get('macro_risk_level')}")
        if ms.get("summary"):
            logger.info(f"  {ms['summary']}")
        if report["immediate_reviews"]:
            logger.warning(f"IMMEDIATE REVIEW: {', '.join(report['immediate_reviews'])}")
        if report["sell_signals"]:
            logger.warning(f"SELL SIGNALS: {', '.join(report['sell_signals'])}")
        if report["strong_buy_signals"]:
            logger.info(f"BUY SIGNALS: {', '.join(report['strong_buy_signals'])}")
        logger.info(f"{'─'*40}\n")
