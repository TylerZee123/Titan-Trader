"""
FundamentalSignals
====================
Scores companies across:
- Overall fundamental health (balance sheet, earnings, valuation)
- Economic moat (competitive advantage durability — Buffett style)
- Dividend quality and sustainability
- Management / CEO quality
- Growth trajectory (Lynch style)
- AI exposure (tailwind or disruption risk)
"""

import logging
import math
from typing import Dict

logger = logging.getLogger("titan_trader")

# Companies with known strong AI/tech moats or AI revenue streams
AI_LEADERS = {"NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "AMD", "CRWD", "NET", "SNOW"}
AI_ADOPTERS = {"AAPL", "TSLA", "UNH", "JPM", "V", "MA", "COST"}
AI_DISRUPTION_RISK = {"T", "DE", "CAT", "UPS"}  # slower to adapt


class FundamentalSignals:

    def analyze(self, fundamentals: Dict) -> float:
        """
        Overall fundamental health score (0–1).
        Combines: valuation, profitability, balance sheet strength.
        """
        if not fundamentals or "error" in fundamentals:
            return 0.5

        pts = 0.0
        max_pts = 10.0

        # ── Valuation ──────────────────────────────────────────────────────
        pe = fundamentals.get("pe_ratio", 0)
        forward_pe = fundamentals.get("forward_pe", 0)
        peg = fundamentals.get("peg_ratio", 0)

        # Reasonable PE (5–35 is healthy for most sectors)
        if 0 < pe < 20:        pts += 1.5  # cheap/value
        elif 0 < pe < 35:      pts += 1.0  # fair
        elif pe == 0:          pts += 0.5  # no earnings yet (growth co)
        else:                  pts += 0.0  # expensive

        # Forward PE improvement = earnings growing
        if forward_pe > 0 and pe > 0 and forward_pe < pe:
            pts += 0.5  # earnings expected to grow

        # PEG < 1 = growth at reasonable price (Lynch's key metric)
        if 0 < peg < 1.0:     pts += 1.5
        elif 0 < peg < 2.0:   pts += 0.75

        # ── Profitability ──────────────────────────────────────────────────
        profit_margin = fundamentals.get("profit_margin", 0)
        roe = fundamentals.get("roe", 0)
        fcf = fundamentals.get("free_cash_flow", 0)

        if profit_margin > 0.20:  pts += 1.5  # exceptional margin
        elif profit_margin > 0.10: pts += 1.0
        elif profit_margin > 0.05: pts += 0.5
        elif profit_margin < 0:   pts -= 0.5  # losing money

        if roe > 0.20:    pts += 1.0  # excellent return on equity
        elif roe > 0.10:  pts += 0.5

        if fcf > 0:       pts += 1.0  # positive free cash flow = real business

        # ── Balance Sheet ──────────────────────────────────────────────────
        current_ratio = fundamentals.get("current_ratio", 1.0)
        d2e = fundamentals.get("debt_to_equity", 50)
        net_cash = fundamentals.get("net_cash", 0)

        if current_ratio > 2.0:   pts += 0.5
        elif current_ratio > 1.5: pts += 0.25

        if d2e < 30:      pts += 1.0  # low debt
        elif d2e < 80:    pts += 0.5
        elif d2e > 200:   pts -= 0.5  # overleveraged

        if net_cash > 0:  pts += 0.5  # more cash than debt

        score = min(max(pts / max_pts, 0), 1.0)
        return round(score, 3)

    def moat_score(self, fundamentals: Dict) -> float:
        """
        Economic moat score (0–1) — Buffett's favorite concept.

        Strong moat indicators:
        - High and consistent gross/operating margins
        - High ROE sustained over time
        - Strong brand (reflected in margins)
        - Pricing power (margins holding even with inflation)
        - Network effects (tech platforms)
        - Switching costs
        - Scale advantages
        """
        if not fundamentals or "error" in fundamentals:
            return 0.5

        pts = 0.0
        max_pts = 7.0

        gross_margin     = fundamentals.get("gross_margin", 0)
        operating_margin = fundamentals.get("operating_margin", 0)
        profit_margin    = fundamentals.get("profit_margin", 0)
        roe              = fundamentals.get("roe", 0)
        market_cap       = fundamentals.get("market_cap", 0)
        revenue_ttm      = fundamentals.get("revenue_ttm", 1) or 1

        # High gross margins = pricing power (Buffett's key moat indicator)
        if gross_margin > 0.60:   pts += 2.0  # exceptional (software, pharma)
        elif gross_margin > 0.40: pts += 1.5  # strong
        elif gross_margin > 0.25: pts += 1.0  # decent
        elif gross_margin > 0.15: pts += 0.5

        # High operating margin = operational efficiency moat
        if operating_margin > 0.25: pts += 1.5
        elif operating_margin > 0.15: pts += 1.0
        elif operating_margin > 0.08: pts += 0.5

        # High sustained ROE = durable competitive advantage
        if roe > 0.25:    pts += 1.5
        elif roe > 0.15:  pts += 1.0
        elif roe > 0.10:  pts += 0.5

        # Scale (large cap = harder to displace)
        if market_cap > 100e9:    pts += 1.0  # mega cap
        elif market_cap > 10e9:   pts += 0.5  # large cap

        # Analyst consensus (high conviction = recognized moat)
        recommendation = fundamentals.get("recommendation", 3.0)
        if recommendation < 2.0:  pts += 0.5  # strong buy consensus

        score = min(max(pts / max_pts, 0), 1.0)
        return round(score, 3)

    def dividend_score(self, fundamentals: Dict) -> float:
        """
        Dividend quality score (0–1).
        Rewards: sustainable yield, consistent payer, room to grow.
        Penalizes: unsustainable payout ratio, no dividend (neutral, not penalized).
        """
        if not fundamentals or "error" in fundamentals:
            return 0.5

        dy    = fundamentals.get("dividend_yield", 0)
        pay   = fundamentals.get("payout_ratio", 0)
        fcf   = fundamentals.get("free_cash_flow", 0)
        fa5   = fundamentals.get("five_year_avg_div", 0)

        # No dividend — neutral score (growth stocks don't pay dividends)
        if dy == 0:
            return 0.5

        pts = 0.0
        max_pts = 4.0

        # Yield quality
        if 0.02 <= dy <= 0.06:   pts += 1.5  # sweet spot
        elif dy < 0.01:          pts += 0.5  # token dividend
        elif dy > 0.08:          pts += 0.5  # possibly unsustainable

        # Payout ratio (lower = more sustainable)
        if 0 < pay < 0.50:       pts += 1.5  # easily covered
        elif pay < 0.75:         pts += 0.75
        elif pay > 1.0:          pts -= 1.0  # paying more than earning = dangerous

        # FCF coverage
        if fcf > 0:              pts += 0.5  # backed by real cash

        # Dividend history (if paying above historical avg, good sign)
        if fa5 > 0 and dy >= fa5 * 0.9:
            pts += 0.5

        score = min(max(pts / max_pts, 0), 1.0)
        return round(score, 3)

    def management_score(self, fundamentals: Dict, ticker: str) -> float:
        """
        Management quality score (0–1).
        Signals: insider buying, institutional ownership, ROE efficiency,
        low share dilution, analyst trust.
        """
        if not fundamentals or "error" in fundamentals:
            return 0.5

        pts = 0.0
        max_pts = 5.0

        insider_buy_ratio  = fundamentals.get("insider_buy_ratio", 0.5)
        insider_pct        = fundamentals.get("insider_pct", 0)
        institutional_pct  = fundamentals.get("institutional_pct", 0)
        short_ratio        = fundamentals.get("short_ratio", 2)
        roe                = fundamentals.get("roe", 0)
        num_analysts       = fundamentals.get("num_analysts", 0)
        recommendation     = fundamentals.get("recommendation", 3.0)

        # Insider buying > selling = management has skin in the game
        if insider_buy_ratio > 0.7:   pts += 1.5
        elif insider_buy_ratio > 0.5:  pts += 0.75

        # High insider ownership = aligned incentives
        if insider_pct > 0.10:    pts += 1.0
        elif insider_pct > 0.05:  pts += 0.5

        # High institutional ownership = sophisticated capital agrees
        if institutional_pct > 0.70: pts += 0.75
        elif institutional_pct > 0.50: pts += 0.5

        # Low short interest = shorts aren't betting against management
        if short_ratio < 2:    pts += 0.5
        elif short_ratio > 8:  pts -= 0.5  # high short interest = concern

        # Analyst confidence
        if recommendation < 2.0 and num_analysts >= 5:
            pts += 0.5

        score = min(max(pts / max_pts, 0), 1.0)
        return round(score, 3)

    def growth_score(self, fundamentals: Dict) -> float:
        """
        Growth trajectory score (0–1) — Peter Lynch style.
        PEG < 1 = growth at a reasonable price.
        Rewards: accelerating revenue, EPS growth, analyst upgrades.
        """
        if not fundamentals or "error" in fundamentals:
            return 0.5

        pts = 0.0
        max_pts = 6.0

        rev_growth  = fundamentals.get("revenue_growth", 0)
        earn_growth = fundamentals.get("earnings_growth", 0)
        earn_q      = fundamentals.get("earnings_quarterly", 0)
        peg         = fundamentals.get("peg_ratio", 0)
        eps_ttm     = fundamentals.get("eps_ttm", 0)
        eps_fwd     = fundamentals.get("eps_forward", 0)
        upside      = fundamentals.get("analyst_upside", 0)

        # Revenue growth
        if rev_growth > 0.25:    pts += 1.5  # hypergrowth
        elif rev_growth > 0.15:  pts += 1.0  # strong
        elif rev_growth > 0.08:  pts += 0.5  # healthy
        elif rev_growth < 0:     pts -= 0.5  # declining

        # Earnings growth
        if earn_growth > 0.20:   pts += 1.5
        elif earn_growth > 0.10: pts += 1.0
        elif earn_growth > 0:    pts += 0.5

        # Quarterly acceleration
        if earn_q > 0.15:        pts += 0.5

        # PEG (Lynch's favorite: PEG < 1 = buy)
        if 0 < peg < 1.0:        pts += 1.5
        elif 0 < peg < 2.0:      pts += 0.5

        # EPS trajectory
        if eps_fwd > 0 and eps_ttm > 0 and eps_fwd > eps_ttm:
            pts += 0.5  # forward EPS above trailing = growing

        # Analyst price target upside
        if upside > 0.20:        pts += 0.5

        score = min(max(pts / max_pts, 0), 1.0)
        return round(score, 3)

    def ai_exposure_score(self, fundamentals: Dict, ticker: str) -> float:
        """
        AI tailwind / disruption score (0–1).
        - AI leaders: revenue directly from AI = major tailwind
        - AI adopters: using AI to improve margins = moderate tailwind
        - AI disruption risk: business model at risk from AI = penalty
        - Others: neutral
        """
        if ticker in AI_LEADERS:
            base = 0.85
        elif ticker in AI_ADOPTERS:
            base = 0.65
        elif ticker in AI_DISRUPTION_RISK:
            base = 0.35
        else:
            base = 0.55  # neutral

        # Adjust based on R&D investment as % of revenue
        revenue = fundamentals.get("revenue_ttm", 1) or 1
        # yfinance doesn't expose R&D directly in info, so we use sector proxy
        sector = fundamentals.get("sector", "Unknown")
        if sector == "Technology":
            base = min(base + 0.05, 1.0)
        elif sector in ("Energy", "Utilities"):
            base = max(base - 0.05, 0.0)

        return round(base, 3)
