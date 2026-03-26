"""
IntradayWatchdog — Fixed Version
===================================
Fixes from audit:
  - Optional imported at module level (not inside class)
  - schedule package added to requirements.txt
  - Stop-loss proximity uses real tier-based stop percentages
  - Partial sell uses executor.partial_sell (Claude decides trim %)
  - Market open/close enforced via market_calendar
"""

import os
import sys
import time
import logging
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

import schedule
import yfinance as yf
import anthropic
import requests

from utils.logger import setup_logger
from utils.notifier import Notifier
from utils.market_calendar import is_market_open, is_trading_day
from core.executor import TradeExecutor

logger = setup_logger("watchdog")

# Alert thresholds
STOP_WARN_THRESHOLD  = 0.035  # warn when within 3.5% of stop
INTRADAY_LOSS_ALERT  = 0.05   # alert if down 5%+ intraday
INTRADAY_GAIN_TRIM   = 0.10   # flag if up 10%+ intraday (Claude decides whether to trim)
SECTOR_DROP_ALERT    = 0.02   # alert if sector ETF drops 2%+ today
SPY_HALT_THRESHOLD   = 0.015  # halt new buys if SPY down 1.5%+ intraday
VIX_SPIKE_THRESHOLD  = 0.20   # alert if VIX up 20%+ from open

# Stop loss percentages by tier — matches position_allocator.py
STOP_BY_TIER = {
    "UBER":     0.10,
    "HIGH":     0.07,
    "MODERATE": 0.05,
    "LOW":      0.04,
    "UNKNOWN":  0.07,  # default
}

SECTOR_ETFS = {
    "Technology":           "XLK",
    "Healthcare":           "XLV",
    "Financials":           "XLF",
    "Consumer Discretionary":"XLY",
    "Consumer Staples":     "XLP",
    "Industrials":          "XLI",
    "Energy":               "XLE",
}

CLAUDE_INTRADAY_PROMPT = """You are a real-time trading monitor for a position we hold.
Something has triggered an alert. Analyze and return ONLY JSON:
{
  "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "action": "HOLD" | "WATCH" | "TRIM" | "EXIT",
  "trim_pct": float or null,   // 0.01-0.99, only if action=TRIM. You choose the amount.
  "reasoning": "1-2 sentences",
  "urgent": boolean
}
CRITICAL + EXIT = sell immediately.
HIGH + TRIM = reduce position by the percentage you choose.
MEDIUM/LOW = monitor, no immediate action.
No markdown. Only JSON."""


class IntradayWatchdog:

    def __init__(self):
        config = {
            "alpaca_api_key":    os.environ.get("ALPACA_API_KEY"),
            "alpaca_secret_key": os.environ.get("ALPACA_SECRET_KEY"),
            "alpaca_paper":      os.environ.get("ALPACA_PAPER", "true").lower() == "true",
            "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY"),
            "notification_email":os.environ.get("NOTIFICATION_EMAIL"),
            "mode":              "trade",
        }
        self.config    = config
        self.notifier  = Notifier(config)
        self.executor  = TradeExecutor(config)
        self.client    = anthropic.Anthropic(api_key=config["anthropic_api_key"])

        self.halt_new_buys   = False
        self.spy_open_price  = None
        self.vix_open_price  = None
        self.position_alerts = {}   # ticker → last alert time
        self.cycle_count     = 0
        self.position_tiers  = {}   # ticker → tier (loaded from Supabase)

    def run(self):
        """Start watchdog. Runs every 15 min during market hours."""
        if not is_trading_day():
            logger.info("Not a trading day — watchdog exiting.")
            return

        logger.info("=" * 50)
        logger.info(f"WATCHDOG STARTING — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
        logger.info("=" * 50)

        self._capture_open_prices()
        self._load_position_tiers()

        schedule.every(15).minutes.do(self._run_cycle)
        self._run_cycle()  # run immediately on start

        while True:
            if not is_market_open():
                logger.info("Market closed — watchdog shutting down.")
                break
            schedule.run_pending()
            time.sleep(30)

    def _run_cycle(self):
        self.cycle_count += 1
        logger.info(f"\n── Watchdog cycle #{self.cycle_count} — {datetime.now().strftime('%H:%M')} ──")

        if not is_market_open():
            return

        try:
            self._check_macro()

            positions = self.executor.get_positions()
            if not positions:
                logger.info("  No open positions.")
                return

            logger.info(f"  Monitoring {len(positions)} positions...")
            alerts = []

            for position in positions:
                alert = self._check_position(position)
                if alert:
                    alerts.append(alert)

            # News check every other cycle
            if self.cycle_count % 2 == 0:
                tickers = [p["ticker"] for p in positions]
                news_alerts = self._check_breaking_news(tickers)
                alerts.extend(news_alerts)

            # Sector health
            sector_alerts = self._check_sectors(positions)
            alerts.extend(sector_alerts)

            if alerts:
                self._process_alerts(alerts)
            else:
                logger.info("  All clear.")

        except Exception as e:
            logger.error(f"Watchdog cycle error: {e}", exc_info=True)

    def _check_macro(self):
        try:
            spy_info = yf.Ticker("SPY").info
            vix_info = yf.Ticker("^VIX").info

            spy = float(spy_info.get("regularMarketPrice") or 0)
            vix = float(vix_info.get("regularMarketPrice") or 0)

            alerts = []

            if self.spy_open_price and spy:
                chg = (spy / self.spy_open_price - 1)
                if chg < -SPY_HALT_THRESHOLD and not self.halt_new_buys:
                    self.halt_new_buys = True
                    alerts.append(f"SPY down {chg*100:.1f}% intraday — halting new buys")
                elif chg > -0.005 and self.halt_new_buys:
                    self.halt_new_buys = False
                    logger.info("  SPY recovered — resuming")

            if self.vix_open_price and vix:
                vix_chg = (vix / self.vix_open_price - 1)
                if vix_chg > VIX_SPIKE_THRESHOLD:
                    alerts.append(f"VIX spiked {vix_chg*100:.0f}% (now {vix:.1f})")

            if alerts:
                self.notifier.send_alert_sms("TITAN WATCHDOG — MACRO\n" + "\n".join(alerts))

        except Exception as e:
            logger.error(f"  Macro check error: {e}")

    def _check_position(self, position: Dict) -> Optional[Dict]:
        ticker         = position["ticker"]
        current        = position["current"]
        avg_entry      = position["avg_entry"]
        unrealized_pct = position["unrealized_pct"]
        market_val     = position["market_val"]

        # Get real stop % for this position's tier
        tier     = self.position_tiers.get(ticker, "UNKNOWN")
        stop_pct = STOP_BY_TIER.get(tier, STOP_BY_TIER["UNKNOWN"])
        stop_px  = avg_entry * (1 - stop_pct)
        dist_to_stop = (current - stop_px) / stop_px if stop_px > 0 else 1.0

        alerts   = []
        severity = "LOW"
        action   = "HOLD"
        trim_pct = None

        # Approaching stop
        if dist_to_stop < STOP_WARN_THRESHOLD:
            alerts.append(
                f"Near stop: ${current:.2f} vs stop ${stop_px:.2f} "
                f"({dist_to_stop*100:.1f}% away) [{tier} tier]"
            )
            severity = "HIGH"
            action   = "WATCH"

        # Intraday info
        try:
            info = yf.Ticker(ticker).info
            day_open     = float(info.get("regularMarketOpen") or current)
            intraday_chg = (current / day_open - 1) if day_open else 0
        except Exception:
            intraday_chg = 0

        if intraday_chg < -INTRADAY_LOSS_ALERT:
            alerts.append(f"Down {intraday_chg*100:.1f}% today — ${market_val:,.0f} at risk")
            severity = "HIGH"
            action   = "WATCH"

        if intraday_chg > INTRADAY_GAIN_TRIM:
            alerts.append(f"Up {intraday_chg*100:.1f}% today — consider trimming")
            severity = "MEDIUM"
            action   = "TRIM"

        if not alerts:
            return None

        # Rate limit alerts per ticker (max once per hour)
        last = self.position_alerts.get(ticker)
        if last and (datetime.now() - last).seconds < 3600:
            return None
        self.position_alerts[ticker] = datetime.now()

        return {
            "ticker":   ticker,
            "type":     "POSITION",
            "severity": severity,
            "action":   action,
            "trim_pct": trim_pct,
            "messages": alerts,
            "details":  position,
        }

    def _check_breaking_news(self, tickers: List[str]) -> List[Dict]:
        alerts = []
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                news  = stock.news or []
                recent_headlines = []

                for item in news[:5]:
                    title    = (item.get("content", {}).get("title", "") or item.get("title", ""))
                    pub_time = item.get("content", {}).get("pubDate") or item.get("providerPublishTime", 0)
                    try:
                        age_hours = (time.time() - float(pub_time)) / 3600
                        if age_hours <= 2 and title:
                            recent_headlines.append(title)
                    except Exception:
                        if title:
                            recent_headlines.append(title)

                if not recent_headlines:
                    continue

                analysis = self._claude_news_impact(ticker, recent_headlines)
                if analysis.get("severity") in ("HIGH", "CRITICAL"):
                    alerts.append({
                        "ticker":    ticker,
                        "type":      "NEWS",
                        "severity":  analysis["severity"],
                        "action":    analysis["action"],
                        "trim_pct":  analysis.get("trim_pct"),
                        "messages":  recent_headlines[:3],
                        "reasoning": analysis.get("reasoning", ""),
                    })

            except Exception as e:
                logger.debug(f"  News check {ticker}: {e}")

        return alerts

    def _claude_news_impact(self, ticker: str, headlines: List[str]) -> Dict:
        headlines_text = "\n".join(f"- {h}" for h in headlines)
        prompt = f"We hold {ticker}. Breaking news in last 2 hours:\n{headlines_text}\nShould we act?"
        try:
            resp = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                system=CLAUDE_INTRADAY_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = re.sub(r"```json|```", "", resp.content[0].text).strip()
            return json.loads(raw)
        except Exception:
            return {"severity": "LOW", "action": "HOLD", "trim_pct": None, "reasoning": ""}

    def _check_sectors(self, positions: List[Dict]) -> List[Dict]:
        from data.universe import get_sector
        alerts = []
        seen_sectors = set()

        for pos in positions:
            sector = get_sector(pos["ticker"])
            if sector in seen_sectors:
                continue
            seen_sectors.add(sector)

            etf = SECTOR_ETFS.get(sector)
            if not etf:
                continue
            try:
                info = yf.Ticker(etf).info
                chg  = float(info.get("regularMarketChangePercent") or 0) / 100
                if chg < -SECTOR_DROP_ALERT:
                    alerts.append({
                        "ticker":   etf,
                        "type":     "SECTOR",
                        "severity": "MEDIUM",
                        "action":   "WATCH",
                        "messages": [f"{sector} ({etf}) down {chg*100:.1f}% today"],
                    })
            except Exception:
                pass

        return alerts

    def _process_alerts(self, alerts: List[Dict]):
        """Handle alerts — execute trims/exits if Claude decides, SMS for critical."""
        critical = [a for a in alerts if a["severity"] in ("CRITICAL", "HIGH")]

        if critical:
            lines = ["🚨 TITAN WATCHDOG"]
            for a in critical:
                lines.append(f"\n{a['ticker']} [{a['type']}] → {a['action']}")
                for msg in a.get("messages", [])[:2]:
                    lines.append(f"  {msg}")
                if a.get("reasoning"):
                    lines.append(f"  {a['reasoning']}")
            self.notifier.send_alert_sms("\n".join(lines))

        # Execute any trim/exit actions Claude decided
        for a in alerts:
            if a.get("action") == "EXIT" and a.get("severity") == "CRITICAL":
                try:
                    position = next(
                        (p for p in self.executor.get_positions()
                         if p["ticker"] == a["ticker"]), None
                    )
                    if position:
                        self.executor.market_sell(
                            a["ticker"], position["qty"],
                            f"Watchdog EXIT: {a.get('reasoning','')}"
                        )
                        logger.warning(f"  WATCHDOG EXIT: {a['ticker']}")
                except Exception as e:
                    logger.error(f"  Watchdog EXIT failed {a['ticker']}: {e}")

            elif a.get("action") == "TRIM" and a.get("trim_pct"):
                try:
                    self.executor.partial_sell(
                        a["ticker"],
                        a["trim_pct"],
                        f"Watchdog TRIM {a['trim_pct']*100:.0f}%: {a.get('reasoning','')}"
                    )
                except Exception as e:
                    logger.error(f"  Watchdog TRIM failed {a['ticker']}: {e}")

        for a in alerts:
            lvl = logger.warning if a["severity"] in ("CRITICAL","HIGH") else logger.info
            lvl(f"  [{a['severity']}] {a['ticker']} {a['type']}: {'; '.join(a.get('messages',[]))}")

    def _capture_open_prices(self):
        try:
            self.spy_open_price = float(yf.Ticker("SPY").info.get("regularMarketOpen") or 0)
            self.vix_open_price = float(yf.Ticker("^VIX").info.get("regularMarketOpen") or 0)
            logger.info(f"Open: SPY=${self.spy_open_price:.2f}, VIX={self.vix_open_price:.1f}")
        except Exception as e:
            logger.error(f"Could not capture open prices: {e}")

    def _load_position_tiers(self):
        """Load tier info for each current position from Supabase."""
        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_key = os.environ.get("SUPABASE_KEY", "")
        if not supabase_url:
            return
        try:
            headers = {
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            }
            resp = requests.get(
                f"{supabase_url}/rest/v1/trades?status=eq.OPEN&select=ticker,tier",
                headers=headers,
                timeout=10,
            )
            if resp.ok:
                for row in resp.json():
                    self.position_tiers[row["ticker"]] = row.get("tier", "UNKNOWN")
                logger.info(f"  Loaded tiers for {len(self.position_tiers)} positions")
        except Exception as e:
            logger.debug(f"  Could not load position tiers: {e}")


if __name__ == "__main__":
    watchdog = IntradayWatchdog()
    watchdog.run()
