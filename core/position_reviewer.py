"""
PositionReviewer — Claude Decides What To Do With Every Position
=================================================================
This is the engine for Q4.

When any held position triggers a review condition, Claude performs
a full autonomous analysis and decides:
  - HOLD: thesis intact, no action
  - TRIM X%: reduce exposure by any percentage Claude chooses
  - EXIT: thesis broken, close fully

Claude's decision is based on:
  1. Why did the score change or what triggered the review
  2. Current fundamental health vs when we bought
  3. What the news says right now
  4. Macro environment
  5. The position's unrealized P&L and risk profile
  6. Whether this looks like market noise vs real thesis deterioration

No hardcoded thresholds override Claude's judgment.
The score is an input. Claude is the judge.
"""

import logging
import json
import re
from typing import Dict, List, Optional, Tuple
import anthropic
import yfinance as yf

logger = logging.getLogger("titan_trader")

POSITION_REVIEW_SYSTEM = """You are the greatest portfolio manager in history — combining Buffett's patience, 
Lynch's story-checking discipline, Druckenmiller's ruthlessness about cutting losing theses, 
and Jones's rule of never letting a winner become a loser.

You are reviewing a position we currently hold. Something has triggered a review.
Your job is to make a definitive decision and explain your reasoning clearly.

Return ONLY a JSON object:
{
  "decision": "HOLD" | "TRIM" | "EXIT",
  "trim_pct": float or null,        // 0.0-1.0, only if decision is TRIM (e.g. 0.25 = trim 25%)
  "conviction": "HIGH" | "MEDIUM" | "LOW",
  "reasoning": string,              // 2-4 sentences. Be specific. What exactly changed or didn't change?
  "thesis_intact": boolean,         // Is the original reason we bought still valid?
  "noise_or_signal": "NOISE" | "SIGNAL" | "UNCLEAR",  // Is the price/score move noise or real signal?
  "reentry_likely": boolean,        // If trimming/exiting, would you re-enter if price improves?
  "urgency": "IMMEDIATE" | "TODAY" | "MONITOR"  // How fast should we act?
}

Key principles:
- HOLD if the business hasn't changed and the price move is market noise
- TRIM if you want reduced exposure but believe the thesis is partially intact
  - Choose trim % based on your conviction level — 20% if mildly cautious, 75% if mostly out
  - There is no required trim percentage. Use your judgment.
- EXIT if the thesis is broken — do not average down into a broken thesis
- Never make a decision based purely on price movement — always ask WHY
- A great business temporarily lower is an opportunity, not a problem
- A bad business temporarily unchanged is still a problem
- Be honest and contrarian. Don't hold something just because you bought it.

No markdown. Only valid JSON."""


class PositionReviewer:

    def __init__(self, config: Dict):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config["anthropic_api_key"])

    def review_position(
        self,
        position: Dict,
        current_score: Dict,
        previous_score: Optional[Dict],
        trigger: str,
        market_context: Dict,
        recent_news: List[str],
    ) -> Dict:
        """
        Full Claude analysis of a held position.
        Returns decision dict with action, trim_pct, reasoning.

        trigger: why this review was triggered
          "score_drop_significant" | "score_drop_gradual" | "score_drop_catastrophic"
          "intraday_loss" | "news_alert" | "approaching_stop" | "daily_review"
        """
        ticker = position["ticker"]
        logger.info(f"\n{'─'*40}")
        logger.info(f"POSITION REVIEW: {ticker} | Trigger: {trigger}")

        brief = self._build_review_brief(
            position=position,
            current_score=current_score,
            previous_score=previous_score,
            trigger=trigger,
            market_context=market_context,
            recent_news=recent_news,
        )

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",   # Use Sonnet for position decisions — higher stakes
                max_tokens=600,
                system=POSITION_REVIEW_SYSTEM,
                messages=[{"role": "user", "content": brief}],
            )

            raw    = re.sub(r"```json|```", "", response.content[0].text).strip()
            result = json.loads(raw)

            # Validate and sanitize
            decision  = result.get("decision", "HOLD")
            trim_pct  = float(result.get("trim_pct") or 0)
            trim_pct  = max(0.01, min(0.99, trim_pct)) if decision == "TRIM" else None

            logger.info(f"  Decision: {decision}"
                        + (f" {trim_pct*100:.0f}%" if trim_pct else "")
                        + f" | Noise/Signal: {result.get('noise_or_signal')}"
                        + f" | Thesis intact: {result.get('thesis_intact')}")
            logger.info(f"  Reasoning: {result.get('reasoning','')[:120]}")

            return {
                "ticker":          ticker,
                "decision":        decision,
                "trim_pct":        trim_pct,
                "conviction":      result.get("conviction", "MEDIUM"),
                "reasoning":       result.get("reasoning", ""),
                "thesis_intact":   result.get("thesis_intact", True),
                "noise_or_signal": result.get("noise_or_signal", "UNCLEAR"),
                "reentry_likely":  result.get("reentry_likely", False),
                "urgency":         result.get("urgency", "TODAY"),
                "trigger":         trigger,
                "current_score":   current_score.get("total_score", 0),
                "prev_score":      previous_score.get("total_score", 0) if previous_score else None,
            }

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"  Position review error for {ticker}: {e}")
            # Safe fallback — hold and flag for manual review
            return {
                "ticker":          ticker,
                "decision":        "HOLD",
                "trim_pct":        None,
                "reasoning":       f"Review failed ({e}) — defaulting to HOLD pending manual check",
                "thesis_intact":   True,
                "noise_or_signal": "UNCLEAR",
                "urgency":         "MONITOR",
                "trigger":         trigger,
            }

    def _build_review_brief(
        self,
        position: Dict,
        current_score: Dict,
        previous_score: Optional[Dict],
        trigger: str,
        market_context: Dict,
        recent_news: List[str],
    ) -> str:
        """Build the full context brief for Claude's position review."""

        ticker     = position["ticker"]
        entry      = position.get("avg_entry", 0)
        current_px = position.get("current", 0)
        unreal_pct = position.get("unrealized_pct", 0)
        market_val = position.get("market_val", 0)
        bucket     = current_score.get("bucket", "UNKNOWN")
        strategy   = current_score.get("strategy", "UNKNOWN")

        prev_score_val = previous_score.get("total_score", "N/A") if previous_score else "N/A"
        curr_score_val = current_score.get("total_score", 0)
        score_change   = (curr_score_val - float(prev_score_val)) if prev_score_val != "N/A" else 0

        # Get fresh fundamental snapshot for context
        try:
            info = yf.Ticker(ticker).info or {}
            rev_growth  = info.get("revenueGrowth", "N/A")
            profit_margin = info.get("profitMargins", "N/A")
            pe = info.get("trailingPE", "N/A")
            analyst_rec = info.get("recommendationMean", "N/A")
        except Exception:
            rev_growth = profit_margin = pe = analyst_rec = "N/A"

        news_text = "\n".join(f"  - {h}" for h in recent_news[:6]) if recent_news else "  No recent news"

        # Score component changes
        comp_changes = ""
        if previous_score and current_score.get("components") and previous_score.get("components"):
            prev_c = previous_score["components"]
            curr_c = current_score["components"]
            for k in curr_c:
                diff = curr_c[k] - prev_c.get(k, curr_c[k])
                if abs(diff) > 0.05:
                    direction = "↓" if diff < 0 else "↑"
                    comp_changes += f"  {k}: {prev_c.get(k,0):.2f} → {curr_c[k]:.2f} ({direction}{abs(diff):.2f})\n"

        return f"""POSITION REVIEW — {ticker}

TRIGGER: {trigger.upper().replace('_', ' ')}

POSITION STATUS:
  Entry price:      ${entry:.2f}
  Current price:    ${current_px:.2f}
  Unrealized P&L:   {unreal_pct:+.1f}%
  Market value:     ${market_val:,.0f}
  Bucket/Strategy:  {bucket} / {strategy}

SCORE CHANGE:
  Previous score:   {prev_score_val}
  Current score:    {curr_score_val:.1f}
  Change:           {score_change:+.1f} points
  Current signal:   {current_score.get('signal', 'N/A')}

SCORE COMPONENT CHANGES:
{comp_changes if comp_changes else '  No significant component changes'}

CURRENT AI ANALYSIS:
  {current_score.get('ai_reasoning', 'N/A')}

CURRENT RISKS FLAGGED:
  {', '.join(current_score.get('ai_risks', [])) or 'None flagged'}

CURRENT CATALYSTS:
  {', '.join(current_score.get('ai_catalysts', [])) or 'None flagged'}

FUNDAMENTAL SNAPSHOT (live):
  Revenue growth:   {f'{rev_growth*100:.1f}%' if isinstance(rev_growth, float) else rev_growth}
  Profit margin:    {f'{profit_margin*100:.1f}%' if isinstance(profit_margin, float) else profit_margin}
  P/E ratio:        {pe}
  Analyst consensus:{analyst_rec}/5.0 (1=strong buy)

MARKET CONTEXT:
  Regime:           {market_context.get('regime', 'N/A')}
  VIX:              {market_context.get('vix', 'N/A')}
  Risk environment: {market_context.get('risk_env', 'N/A')}
  SPY vs 50MA:      {market_context.get('spy_vs_ma50', 'N/A')}%

RECENT NEWS ({len(recent_news)} articles):
{news_text}

Based on ALL of the above:
1. Is the original thesis still intact?
2. Is this price/score movement noise or a real signal?
3. What should we do — HOLD, TRIM (what %), or EXIT?
Make your decision and explain specifically why."""

    @staticmethod
    def check_trigger(
        ticker: str,
        current_score: Dict,
        previous_score: Optional[Dict],
        position: Dict,
        news_alerts: List[str],
    ) -> Optional[str]:
        """
        Determine if a position warrants a Claude review today.
        Static method — callable without instantiation.
        Returns trigger string if review needed, None if all clear.
        """
        return PositionReviewer.should_trigger_review(
            ticker=ticker,
            current_score=current_score,
            previous_score=previous_score,
            position=position,
            news_alerts=news_alerts,
        )

    @staticmethod
    def should_trigger_review(
        ticker: str,
        current_score: Dict,
        previous_score: Optional[Dict],
        position: Dict,
        news_alerts: List[str],
    ) -> Optional[str]:
        """
        Determine if a position warrants a review today.
        Returns trigger string if review needed, None if all clear.

        Review triggers (in order of urgency):
        1. News high-impact alerts
        2. Score drops catastrophically (20+ points in 1 day)
        3. Score drops significantly (12+ points in 1 day)
        4. Score drops gradually (below 55 from previously above 65)
        5. Position approaching stop loss
        6. Daily review for any position held 30+ days
        """
        # 1. News alert — always review
        if news_alerts:
            return "news_alert"

        curr = current_score.get("total_score", 50)
        prev = previous_score.get("total_score", curr) if previous_score else curr
        drop = prev - curr

        # 2. Catastrophic score drop
        if drop >= 20:
            return "score_drop_catastrophic"

        # 3. Significant score drop
        if drop >= 12:
            return "score_drop_significant"

        # 4. Gradual deterioration
        if prev >= 65 and curr < 55:
            return "score_drop_gradual"

        # 5. Score below absolute floor
        if curr < 38:
            return "score_drop_catastrophic"

        # 6. Approaching stop loss
        if position.get("unrealized_pct", 0) < -5:
            return "approaching_stop"

        return None
