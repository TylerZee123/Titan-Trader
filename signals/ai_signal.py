"""
AISignalEngine — Fixed Version
================================
Fixes from audit:
  - analyze() signature correct with all parameters
  - Uses DataValidator.build_claude_context for all briefs
  - claude-haiku-4-5-20251001 for scoring (fast + cheap)
  - claude-sonnet for position reviews (higher stakes)
"""

import logging
import json
import re
from typing import Dict, Optional
import anthropic

logger = logging.getLogger("titan_trader")

ANALYST_SYSTEM_PROMPT = """You are the world's greatest stock analyst — a synthesis of Warren Buffett, Peter Lynch, Stanley Druckenmiller, and Jim Simons.

Your job is to analyze a stock and return a JSON object with:
- score: float 0.0–1.0 (your conviction score)
- signal: one of "STRONG_BUY" | "BUY" | "HOLD" | "WATCH" | "AVOID"
- reasoning: 2-3 sentence explanation of your key thesis
- risks: list of 1-3 specific risks
- catalysts: list of 1-3 specific upside catalysts
- time_horizon: "SHORT" (days-weeks) | "MEDIUM" (months) | "LONG" (1+ year)
- projected_return_12m: float or null (your best estimate of 12-month return %, null if insufficient data)

Score guide:
0.85–1.0 = STRONG_BUY (extremely rare, high conviction)
0.70–0.84 = BUY (clear edge, favorable risk/reward)
0.50–0.69 = HOLD (decent but not compelling)
0.35–0.49 = WATCH (monitor, not ready yet)
0.00–0.34 = AVOID (material concerns)

You CAN and SHOULD make projections where you have genuine conviction.
Use your knowledge of the business, industry dynamics, competitive position,
and market conditions to project where this stock is headed.
Only mark projected_return_12m as null if you truly cannot form a view.

For fields marked NOT AVAILABLE in the data, reason from what IS available
and your own knowledge. A great analyst works with incomplete information.

Be honest and contrarian when warranted.
Respond ONLY with valid JSON. No markdown, no preamble."""


class AISignalEngine:

    def __init__(self, config: Dict):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config["anthropic_api_key"])
        self._cache: Dict = {}

    def analyze(
        self,
        ticker: str,
        fundamentals: Dict,
        technicals: Dict,
        news: Dict,
        market_context: Dict,
        validation_report: Dict = None,
        congressional: Dict = None,
        fallen_angel: Dict = None,
    ) -> Dict:
        """
        Full Claude analysis of a stock.
        Returns scored result with reasoning, risks, catalysts, projection.
        """
        if ticker in self._cache:
            return self._cache[ticker]

        from data.validator import DataValidator
        report = validation_report or {
            "confidence": "UNKNOWN",
            "missing_critical": [],
            "data_quality_pct": 50,
        }

        brief = DataValidator.build_claude_context(
            ticker=ticker,
            fundamentals=fundamentals,
            validation_report=report,
            price_data={},
            technicals=technicals,
            news=news,
            market_context=market_context,
            congressional=congressional,
            fallen_angel=fallen_angel,
        )

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                system=ANALYST_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": brief}],
            )

            raw    = re.sub(r"```json|```", "", response.content[0].text).strip()
            result = json.loads(raw)

            result["score"] = float(max(0.0, min(1.0, result.get("score", 0.5))))

            logger.debug(
                f"  Claude {ticker}: {result['score']:.2f} "
                f"[{result.get('signal')}] "
                f"proj={result.get('projected_return_12m')}% "
                f"— {result.get('reasoning','')[:70]}"
            )

            self._cache[ticker] = result
            return result

        except json.JSONDecodeError as e:
            logger.error(f"  Claude JSON parse error {ticker}: {e}")
            return self._fallback(ticker, fundamentals, technicals)
        except Exception as e:
            logger.error(f"  Claude API error {ticker}: {e}")
            return self._fallback(ticker, fundamentals, technicals)

    def _fallback(self, ticker: str, fundamentals: Dict, technicals: Dict) -> Dict:
        """Rule-based fallback if Claude API fails. Bot keeps running."""
        score = 0.5
        pe = fundamentals.get("pe_ratio") or 25
        if 0 < pe < 20:                                          score += 0.08
        if (fundamentals.get("revenue_growth") or 0) > 0.10:    score += 0.08
        if (fundamentals.get("profit_margin") or 0) > 0.10:     score += 0.08
        if (fundamentals.get("free_cash_flow") or 0) > 0:       score += 0.05
        score = min(max(score * 0.6 + technicals.get("score", 0.5) * 0.4, 0), 1)
        return {
            "score":               round(score, 3),
            "signal":              "HOLD",
            "reasoning":           "Fallback rule-based score (Claude API unavailable)",
            "risks":               ["Claude API unavailable — reduced confidence"],
            "catalysts":           [],
            "time_horizon":        "MEDIUM",
            "projected_return_12m":None,
        }
