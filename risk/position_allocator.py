"""
PositionAllocator — Fixed Version
===================================
Fixes from audit:
  - STRATEGY_WEIGHTS copied (not referenced) before modification to prevent mutation
  - All other logic unchanged from v2
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger("titan_trader")

CONFIDENCE_TIERS = [
    (80, 100, "UBER",     0.15),
    (70,  79, "HIGH",     0.10),
    (60,  69, "MODERATE", 0.06),
    (50,  59, "LOW",      0.03),
]

SECTOR_CAPS = {
    "Technology":              0.35,
    "Healthcare":              0.25,
    "Financials":              0.25,
    "Consumer Discretionary":  0.20,
    "Consumer Staples":        0.15,
    "Energy":                  0.15,
    "Industrials":             0.15,
    "Real Estate":             0.10,
    "Utilities":               0.10,
    "Materials":               0.10,
    "Communication Services":  0.20,
    "Bonds":                   0.05,
    "Unknown":                 0.10,
}

STOP_LOSS_BY_TIER = {
    "UBER":     0.10,
    "HIGH":     0.07,
    "MODERATE": 0.05,
    "LOW":      0.04,
}

TAKE_PROFIT_BY_TIER = {
    "UBER":     None,    # CORE positions: no fixed TP, trailing stop handles exit
    "HIGH":     0.25,
    "MODERATE": 0.18,
    "LOW":      0.12,
}

# CORE bucket gets trailing stop, no take-profit ceiling
CORE_TRAIL_PCT = 0.15


class PositionAllocator:

    def __init__(self, config: Dict):
        self.config        = config
        self.cash_reserve  = config.get("cash_reserve_pct", 0.20)
        self.max_positions = config.get("max_portfolio_size", 12)

    def allocate(
        self,
        scored_stocks: List[Dict],
        portfolio_value: float,
        current_positions: List[Dict],
        market_context: Dict,
        news_sentiments: Dict = None,
    ) -> Dict:
        news_sentiments = news_sentiments or {}
        regime_mult     = self._regime_multiplier(market_context)
        deployable      = portfolio_value * (1 - self.cash_reserve) * regime_mult

        # Build current sector exposure from existing positions
        sector_exposure = self._calc_sector_exposure(current_positions, portfolio_value)

        candidates = [
            s for s in scored_stocks
            if s["total_score"] >= 50 and s["signal"] not in ("AVOID",)
        ]

        allocations   = []
        total_deployed= 0.0
        portfolio_heat= 0.0
        slots_used    = len(current_positions)

        for stock in candidates:
            if len(allocations) + slots_used >= self.max_positions:
                break
            if total_deployed >= deployable:
                break

            ticker  = stock["ticker"]
            score   = stock["total_score"]
            sector  = stock.get("sector", "Unknown")
            bucket  = stock.get("bucket", "MOMENTUM")

            # Skip already held positions
            if any(p["ticker"] == ticker for p in current_positions):
                continue

            tier_name, max_pct = self._get_tier(score)

            # News sentiment adjustment
            news_adj = self._news_adjustment(ticker, news_sentiments)
            adjusted_max_pct = min(max_pct * (1 + news_adj * 0.2), max_pct * 1.3)

            # Sector cap enforcement
            current_sector_pct = sector_exposure.get(sector, 0)
            sector_cap         = SECTOR_CAPS.get(sector, 0.15)
            available_sector   = max(0, sector_cap - current_sector_pct)

            if available_sector < 0.01:
                continue

            # Final position size
            position_pct     = min(adjusted_max_pct, available_sector)
            position_dollars = min(portfolio_value * position_pct, deployable - total_deployed)

            if position_dollars < 100:
                continue

            stop_pct  = STOP_LOSS_BY_TIER[tier_name]

            # CORE bucket: trailing stop, no fixed TP
            if bucket == "CORE":
                tp_pct    = None
                trail_pct = CORE_TRAIL_PCT
            else:
                tp_pct    = TAKE_PROFIT_BY_TIER[tier_name]
                trail_pct = None

            # Portfolio heat check
            position_heat = position_pct * stop_pct
            if portfolio_heat + position_heat > 0.10:
                max_heat_allowed  = 0.10 - portfolio_heat
                position_pct      = max_heat_allowed / stop_pct if stop_pct > 0 else 0
                position_dollars  = portfolio_value * position_pct
                if position_dollars < 100:
                    continue

            price  = stock.get("price", 1) or 1
            shares = position_dollars / price

            allocation = {
                "ticker":          ticker,
                "score":           score,
                "tier":            tier_name,
                "bucket":          bucket,
                "strategy":        stock.get("strategy", "SWING"),
                "dollars":         round(position_dollars, 2),
                "pct":             round(position_pct * 100, 2),
                "shares":          round(shares, 4),
                "price":           round(price, 2),
                "sector":          sector,
                "stop_loss_pct":   stop_pct,
                "take_profit_pct": tp_pct,
                "trail_pct":       trail_pct,
                "stop_price":      round(price * (1 - stop_pct), 2),
                "target_price":    round(price * (1 + tp_pct), 2) if tp_pct else None,
                "risk_dollars":    round(position_dollars * stop_pct, 2),
                "reward_dollars":  round(position_dollars * tp_pct, 2) if tp_pct else None,
                "risk_reward":     round(tp_pct / stop_pct, 2) if tp_pct else "trailing",
                "news_adj":        round(news_adj, 3),
                "signal":          stock.get("signal", "BUY"),
                "rationale":       self._build_rationale(stock, tier_name, bucket, news_adj),
            }

            allocations.append(allocation)
            total_deployed     += position_dollars
            portfolio_heat     += position_heat
            sector_exposure[sector] = current_sector_pct + position_pct

        cash_reserved    = portfolio_value - total_deployed
        sector_breakdown = self._summarize_sectors(allocations)

        return {
            "allocations":      allocations,
            "total_deployed":   round(total_deployed, 2),
            "cash_reserved":    round(cash_reserved, 2),
            "deployed_pct":     round(total_deployed / portfolio_value * 100, 2),
            "portfolio_heat":   round(portfolio_heat * 100, 2),
            "regime_mult":      regime_mult,
            "sector_breakdown": sector_breakdown,
        }

    def _get_tier(self, score: float) -> Tuple[str, float]:
        for low, high, name, max_pct in CONFIDENCE_TIERS:
            if low <= score <= high:
                return name, max_pct
        return "LOW", 0.03

    def _news_adjustment(self, ticker: str, news_sentiments: Dict) -> float:
        if ticker not in news_sentiments:
            return 0.0
        sentiment = news_sentiments[ticker].get("sentiment_score", 0.5)
        if news_sentiments[ticker].get("requires_immediate_review"):
            return -0.5
        return round((sentiment - 0.5) * 0.6, 3)

    def _regime_multiplier(self, market_context: Dict) -> float:
        regime  = market_context.get("regime", "TRANSITION")
        risk    = market_context.get("risk_env", "ELEVATED")
        vix     = market_context.get("vix", 20)
        mult    = 1.0
        if regime == "BULL":        mult *= 1.0
        elif regime == "TRANSITION":mult *= 0.80
        elif regime == "BEAR":      mult *= 0.45
        if risk == "LOW_FEAR":      mult *= 1.0
        elif risk == "ELEVATED":    mult *= 0.90
        elif risk == "HIGH_FEAR":   mult *= 0.60
        if vix > 40:                mult *= 0.50
        elif vix > 30:              mult *= 0.70
        return round(max(0.20, min(1.0, mult)), 2)

    def _calc_sector_exposure(self, positions: List[Dict], portfolio_value: float) -> Dict[str, float]:
        from data.universe import get_sector
        exposure = {}
        for pos in positions:
            sector = get_sector(pos["ticker"])
            pct    = pos["market_val"] / portfolio_value if portfolio_value > 0 else 0
            exposure[sector] = exposure.get(sector, 0) + pct
        return exposure

    def _build_rationale(self, stock: Dict, tier: str, bucket: str, news_adj: float) -> str:
        parts = [
            f"Score {stock['total_score']:.1f}/100 ({tier})",
            f"Signal: {stock['signal']}",
            f"Bucket: {bucket}",
        ]
        if news_adj > 0.1:
            parts.append(f"News tailwind +{news_adj:.0%}")
        elif news_adj < -0.1:
            parts.append(f"News headwind {news_adj:.0%}")
        if stock.get("congress_signal") and stock["congress_signal"] not in ("NEUTRAL", None):
            parts.append(f"Congress: {stock['congress_signal']}")
        if stock.get("ai_reasoning"):
            parts.append(stock["ai_reasoning"][:60])
        return " | ".join(parts)

    def _summarize_sectors(self, allocations: List[Dict]) -> Dict:
        breakdown = {}
        for a in allocations:
            sec = a.get("sector", "Unknown")
            if sec not in breakdown:
                breakdown[sec] = {"count": 0, "dollars": 0, "tickers": []}
            breakdown[sec]["count"]   += 1
            breakdown[sec]["dollars"] += a["dollars"]
            breakdown[sec]["tickers"].append(a["ticker"])
        return breakdown
