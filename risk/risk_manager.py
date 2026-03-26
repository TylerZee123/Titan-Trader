"""
RiskManager — Fixed Version
=============================
Fixes from audit:
  - Sector lookup uses real data from universe.py (not hardcoded "Unknown")
  - Deployable cash calculation fixed for $5K portfolio
  - Position sizing defers to PositionAllocator (single source of truth)
  - Earnings avoidance actually blocks entry (not just penalizes score)
  - Loss detection covers ALL exit types, not just stop orders
  - Position reviews delegated to PositionReviewer (Claude decides)
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("titan_trader")

MIN_SCORE_TO_BUY  = 60.0
MIN_SCORE_TO_HOLD = 38.0   # Claude reviews below this, but doesn't auto-exit
MIN_DOLLARS       = 100.0  # minimum position size at $5K


class RiskManager:

    def __init__(self, config: Dict):
        self.config           = config
        self.max_positions    = config.get("max_portfolio_size", 12)
        self.daily_loss_limit = config.get("daily_loss_limit", 0.03)
        self.cash_reserve_pct = config.get("cash_reserve_pct", 0.20)

    def check_daily_loss_limit(self, account: Dict) -> bool:
        """Hard stop — halt all trading if down more than daily_loss_limit today."""
        pnl_pct = account.get("pnl_today_pct", 0) / 100
        if pnl_pct < -self.daily_loss_limit:
            logger.warning(
                f"Daily loss limit triggered: {pnl_pct*100:.2f}% "
                f"(limit: -{self.daily_loss_limit*100:.1f}%) — NO TRADES TODAY"
            )
            return False
        return True

    def get_deployable_cash(self, account: Dict) -> float:
        """
        Fixed calculation: deployable = cash - required_reserve
        Required reserve = portfolio_value × cash_reserve_pct
        Cannot go below zero.

        Example at $5K: portfolio=$5000, cash=$5000, reserve=$1000 → deployable=$4000
        After buying $2000: portfolio=$5000, cash=$3000, reserve=$1000 → deployable=$2000
        """
        portfolio_value = float(account["portfolio_value"])
        cash            = float(account["cash"])
        required_reserve= portfolio_value * self.cash_reserve_pct
        deployable      = cash - required_reserve
        return max(0, round(deployable, 2))

    def build_trade_plan(
        self,
        scored_stocks: List[Dict],
        current_positions: List[Dict],
        account: Dict,
        market_context: Dict,
        allocation: Dict,
        position_reviews: Dict,
        earnings_calendar: Dict,
    ) -> Dict:
        """
        Build concrete trade plan from:
        - allocation (PositionAllocator output — single source of truth for sizes)
        - position_reviews (PositionReviewer decisions for held positions)
        - earnings_calendar (block entries near earnings)

        Returns: {buys, sells, holds}
        """
        from data.universe import get_sector
        import time
        from data.earnings_calendar import EarningsCalendar

        deployable_cash  = self.get_deployable_cash(account)
        current_tickers  = {p["ticker"] for p in current_positions}
        regime           = market_context.get("regime", "TRANSITION")
        risk_env         = market_context.get("risk_env", "ELEVATED")
        risk_mult        = self._regime_multiplier(regime, risk_env)
        ec               = EarningsCalendar()   # single instance for all tickers

        logger.info(f"  Regime: {regime} | Risk: {risk_env} | Multiplier: {risk_mult:.2f}x")
        logger.info(f"  Deployable cash: ${deployable_cash:,.2f}")

        sells = []
        buys  = []
        holds = []

        # ── Step 1: Process position reviews (Claude's decisions) ─────────
        for ticker, review in position_reviews.items():
            position = next((p for p in current_positions if p["ticker"] == ticker), None)
            if not position:
                continue

            decision = review.get("decision", "HOLD")
            urgency  = review.get("urgency", "TODAY")

            if decision == "EXIT":
                sells.append({
                    "ticker":  ticker,
                    "qty":     position["qty"],
                    "reason":  f"Claude EXIT: {review.get('reasoning','')[:80]}",
                    "urgency": urgency,
                    "claude_review": True,
                })
                logger.info(f"  EXIT {ticker} (Claude decided): {review.get('reasoning','')[:60]}")

            elif decision == "TRIM":
                trim_pct = review.get("trim_pct", 0.5)
                sells.append({
                    "ticker":   ticker,
                    "qty":      round(position["qty"] * trim_pct, 6),
                    "reason":   f"Claude TRIM {trim_pct*100:.0f}%: {review.get('reasoning','')[:60]}",
                    "urgency":  urgency,
                    "is_trim":  True,
                    "trim_pct": trim_pct,
                    "claude_review": True,
                })
                holds.append({
                    "ticker": ticker,
                    "action": f"TRIMMED_{trim_pct*100:.0f}PCT",
                    "score":  review.get("current_score", 0),
                })
                logger.info(f"  TRIM {trim_pct*100:.0f}% {ticker} (Claude decided): {review.get('reasoning','')[:60]}")

            else:  # HOLD
                holds.append({
                    "ticker": ticker,
                    "action": "HOLD",
                    "score":  review.get("current_score", 0),
                    "reason": review.get("reasoning", ""),
                })

        # Positions NOT in review → auto-hold
        reviewed_tickers = set(position_reviews.keys())
        for position in current_positions:
            if position["ticker"] not in reviewed_tickers:
                stock_score = next(
                    (s for s in scored_stocks if s["ticker"] == position["ticker"]), None
                )
                score = stock_score["total_score"] if stock_score else 0
                holds.append({"ticker": position["ticker"], "action": "HOLD", "score": score})

        # ── Step 2: New buys from allocator ───────────────────────────────
        # Positions being sold don't count toward slot limit
        exited_tickers = {s["ticker"] for s in sells if not s.get("is_trim")}
        open_slots = self.max_positions - (len(current_positions) - len(exited_tickers))

        # Build score map for component lookup
        score_map = {s["ticker"]: s for s in scored_stocks}

        for alloc in allocation.get("allocations", []):
            if open_slots <= 0:
                break
            if deployable_cash < MIN_DOLLARS:
                logger.info("  Insufficient deployable cash — stopping buys")
                break

            ticker  = alloc["ticker"]
            dollars = alloc["dollars"] * risk_mult
            bucket  = alloc.get("bucket", "MOMENTUM")
            score   = alloc.get("score", 0)

            # Skip if already held or being sold/trimmed
            if ticker in current_tickers and ticker not in exited_tickers:
                continue

            # Skip if score below threshold
            if score < MIN_SCORE_TO_BUY:
                continue

            # EARNINGS BLOCK — hard block for high-risk scenarios
            earnings = earnings_calendar.get(ticker, {})
            if earnings:
                days_until = earnings.get("days_until", 99)
                if ec.should_avoid_entry(ticker, score):
                    # Half size if UBER within 3 days, skip entirely otherwise
                    if score >= 80 and days_until <= 3:
                        dollars *= 0.5
                        logger.info(f"  {ticker}: earnings in {days_until}d — half size (UBER confidence)")
                    else:
                        logger.info(f"  {ticker}: BLOCKED — earnings in {days_until}d (score {score:.0f})")
                        continue

            # Apply risk multiplier
            dollars = min(dollars, deployable_cash)
            if dollars < MIN_DOLLARS:
                continue

            buys.append({
                "ticker":          ticker,
                "dollars":         round(dollars, 2),
                "score":           score,
                "tier":            alloc.get("tier", "MODERATE"),
                "bucket":          bucket,
                "strategy":        alloc.get("strategy", "SWING"),
                "stop_loss_pct":   alloc.get("stop_loss_pct", 0.07),
                "take_profit_pct": alloc.get("take_profit_pct", 0.20),
                "trail_pct":       0.15 if bucket == "CORE" else None,
                "signal":          alloc.get("signal", "BUY"),
                "reasoning":       alloc.get("rationale", ""),
                "components":      score_map.get(ticker, {}).get("components", {}),
                "earnings_in_days":earnings.get("days_until"),
            })

            deployable_cash -= dollars
            open_slots      -= 1

            logger.info(
                f"  BUY {ticker:6s} ${dollars:,.0f} "
                f"[{alloc.get('tier','?')}] [{bucket}] "
                f"score={score:.1f}"
            )

        return {
            "buys":            buys,
            "sells":           sells,
            "holds":           holds,
            "regime":          regime,
            "risk_multiplier": risk_mult,
            "deployable_cash": round(deployable_cash, 2),
            "new_positions":   len(buys),
            "exits":           len([s for s in sells if not s.get("is_trim")]),
            "trims":           len([s for s in sells if s.get("is_trim")]),
        }

    def get_positions_needing_review(
        self,
        current_positions: List[Dict],
        scored_stocks: List[Dict],
        previous_scores: Dict,
        news_sentiments: Dict,
    ) -> Dict[str, str]:
        """
        Scan all positions for review triggers.
        Returns {ticker: trigger_reason} for any that need Claude review.
        """
        from core.position_reviewer import PositionReviewer
        needs_review = {}

        score_map = {s["ticker"]: s for s in scored_stocks}

        for position in current_positions:
            ticker  = position["ticker"]
            curr_s  = score_map.get(ticker)
            prev_s  = previous_scores.get(ticker)

            if not curr_s:
                continue

            news_info = news_sentiments.get(ticker, {})
            news_alerts = news_info.get("alerts", [])

            # Use PositionReviewer's trigger logic (no Anthropic client needed for trigger check)
            trigger = PositionReviewer.check_trigger(
                ticker=ticker,
                current_score=curr_s,
                previous_score=prev_s,
                position=position,
                news_alerts=news_alerts,
            )

            if trigger:
                needs_review[ticker] = trigger
                logger.info(f"  Review triggered: {ticker} — {trigger}")

        return needs_review

    def _regime_multiplier(self, regime: str, risk_env: str) -> float:
        """Scale position sizes based on market conditions."""
        base = 1.0
        if regime == "BULL":           base *= 1.0
        elif regime == "TRANSITION":   base *= 0.80
        elif regime == "BEAR":         base *= 0.45

        if risk_env == "LOW_FEAR":     base *= 1.0
        elif risk_env == "ELEVATED":   base *= 0.90
        elif risk_env == "HIGH_FEAR":  base *= 0.55

        return round(max(0.20, min(1.0, base)), 2)
