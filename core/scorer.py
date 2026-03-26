"""
StockScorer — Ranks scored stocks and detects contradictions.
"""
import logging
from typing import Dict, List

logger = logging.getLogger("titan_trader")


class StockScorer:

    def rank(self, scored_stocks: List[Dict]) -> List[Dict]:
        sorted_stocks = sorted(scored_stocks, key=lambda x: x["total_score"], reverse=True)
        for i, stock in enumerate(sorted_stocks):
            stock["rank"] = i + 1
            stock["contradictions"] = self._detect_contradictions(stock)
        return sorted_stocks

    def _detect_contradictions(self, stock: Dict) -> List[str]:
        contradictions = []
        c = stock.get("components", {})
        if c.get("fundamental", 0) > 0.7 and c.get("technical", 0) < 0.35:
            contradictions.append("Strong fundamentals but weak technicals — falling knife risk")
        if c.get("technical", 0) > 0.75 and c.get("fundamental", 0) < 0.35:
            contradictions.append("Strong price action but weak fundamentals — momentum trap risk")
        if c.get("sentiment", 0) > 0.65 and c.get("ai_analysis", 0) < 0.40:
            contradictions.append("Positive sentiment contradicts AI analysis")
        if c.get("growth", 0) > 0.70 and c.get("management", 0) < 0.35:
            contradictions.append("High growth potential undermined by management concerns")
        return contradictions
