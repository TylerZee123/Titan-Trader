"""
FallenAngelScanner — Asymmetric Recovery Plays
================================================
Scans for stocks that are:
  1. Down 40%+ from 52-week high
  2. Have intact or recovering fundamentals
  3. Show early signs of technical reversal
  4. Have insider buying (management sees value)

These are Druckenmiller-style asymmetric bets:
  - Limited downside (already beaten down)
  - High upside if thesis plays out
  - Small initial position, add on confirmation

Also identifies "value traps" — stocks cheap for a reason.
"""

import logging
from typing import Dict, List, Optional
import yfinance as yf

logger = logging.getLogger("titan_trader")


class FallenAngelScanner:

    def scan(self, tickers: List[str]) -> Dict[str, Dict]:
        """
        Scan universe for fallen angel opportunities.
        Returns dict of qualifying tickers with analysis.
        """
        results = {}

        for ticker in tickers:
            try:
                analysis = self._analyze(ticker)
                if analysis and analysis.get("qualifies"):
                    results[ticker] = analysis
                    logger.info(
                        f"  Fallen angel: {ticker} "
                        f"down {analysis['drawdown_pct']:.0f}% from high | "
                        f"grade: {analysis['grade']}"
                    )
            except Exception as e:
                logger.debug(f"  {ticker} fallen angel scan error: {e}")

        return results

    def _analyze(self, ticker: str) -> Optional[Dict]:
        stock = yf.Ticker(ticker)
        info  = stock.info or {}

        current = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        high_52  = float(info.get("fiftyTwoWeekHigh") or 0)
        low_52   = float(info.get("fiftyTwoWeekLow") or 0)

        if not current or not high_52:
            return None

        drawdown_pct = ((high_52 - current) / high_52) * 100

        # Must be down at least 30% from high
        if drawdown_pct < 30:
            return None

        # Fundamental health check — is this a value trap?
        revenue_growth  = float(info.get("revenueGrowth") or 0)
        profit_margin   = float(info.get("profitMargins") or 0)
        current_ratio   = float(info.get("currentRatio") or 0)
        debt_to_equity  = float(info.get("debtToEquity") or 999)
        free_cash_flow  = float(info.get("freeCashflow") or 0)
        insider_pct     = float(info.get("heldPercentInsiders") or 0)

        # Value trap indicators (avoid these)
        is_value_trap = (
            revenue_growth < -0.20 and      # revenue declining fast
            profit_margin < 0 and           # losing money
            current_ratio < 0.8 and         # liquidity crisis
            free_cash_flow < 0              # burning cash
        )

        if is_value_trap:
            return {"qualifies": False, "reason": "value_trap"}

        # Recovery signals
        recovery_signals = []
        pts = 0

        if revenue_growth > -0.05:
            pts += 2
            recovery_signals.append("Revenue stabilizing")
        if free_cash_flow > 0:
            pts += 2
            recovery_signals.append("Positive FCF")
        if insider_pct > 0.05:
            pts += 2
            recovery_signals.append(f"Insider ownership {insider_pct*100:.1f}%")
        if current_ratio > 1.5:
            pts += 1
            recovery_signals.append("Strong liquidity")
        if debt_to_equity < 100:
            pts += 1
            recovery_signals.append("Manageable debt")

        # Technical reversal check
        try:
            import pandas as pd
            hist = yf.download(ticker, period="3mo", progress=False, auto_adjust=True)
            if not hist.empty and len(hist) >= 20:
                if isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.get_level_values(0)
                close  = hist["Close"]
                ma20   = float(close.rolling(20).mean().iloc[-1])
                recent = float(close.iloc[-1])
                prev5  = float(close.iloc[-5])

                if recent > ma20:
                    pts += 2
                    recovery_signals.append("Price above 20MA — early reversal")
                if recent > prev5 * 1.03:
                    pts += 1
                    recovery_signals.append("Gaining momentum last 5 days")
        except Exception:
            pass

        # Grade the opportunity
        if pts >= 7:
            grade = "A"   # Strong recovery setup
        elif pts >= 5:
            grade = "B"   # Decent recovery setup
        elif pts >= 3:
            grade = "C"   # Speculative
        else:
            grade = "D"   # Too early or too risky

        # Position size recommendation (start small)
        if grade == "A":
            recommended_pct = 0.04   # 4% of portfolio
        elif grade == "B":
            recommended_pct = 0.025
        elif grade == "C":
            recommended_pct = 0.015
        else:
            recommended_pct = 0.0    # don't enter D grades

        return {
            "qualifies":        grade in ("A", "B", "C"),
            "grade":            grade,
            "drawdown_pct":     round(drawdown_pct, 1),
            "current_price":    current,
            "high_52":          high_52,
            "low_52":           low_52,
            "pct_above_low":    round(((current - low_52) / low_52) * 100, 1) if low_52 else 0,
            "recovery_signals": recovery_signals,
            "recovery_pts":     pts,
            "recommended_pct":  recommended_pct,
            "is_value_trap":    is_value_trap,
            "score_bonus":      pts * 1.5,  # bonus points added to overall score
        }
