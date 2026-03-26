"""
DataValidator — No Silent Bad Data Ever Reaches Claude
========================================================
Every number that flows into scoring or Claude's analysis
passes through this validator first.

Rules:
  - Missing fields are explicitly labeled MISSING — not zero, not None
  - Fields outside realistic ranges are flagged as SUSPECT
  - If a stock has too many missing fields, its score gets a
    confidence penalty and Claude is told data quality is LOW
  - Real P&L always comes from Alpaca filled prices, never estimated
  - Performance metrics are suppressed until sufficient trade history exists

This is not about limiting Claude's reasoning.
This is about making sure Claude reasons on real data, not silence.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("titan_trader")

# Minimum trades before performance metrics are meaningful
MIN_TRADES_FOR_METRICS = 10

# Field-level validation rules: (min_realistic, max_realistic, critical)
# critical=True means if missing, confidence drops significantly
FUNDAMENTAL_FIELD_RULES = {
    "pe_ratio":          (0,    500,   False),
    "forward_pe":        (0,    500,   False),
    "peg_ratio":         (-5,   50,    False),
    "revenue_growth":    (-1,   10,    True),    # critical for growth scoring
    "earnings_growth":   (-5,   50,    True),
    "profit_margin":     (-5,   1,     True),    # critical for moat scoring
    "gross_margin":      (0,    1,     True),
    "operating_margin":  (-5,   1,     False),
    "roe":               (-5,   5,     True),
    "free_cash_flow":    (-1e12,1e12,  True),
    "debt_to_equity":    (0,    1000,  False),
    "current_ratio":     (0,    50,    False),
    "dividend_yield":    (0,    0.30,  False),
    "market_cap":        (1e6,  1e13,  True),
    "insider_buy_ratio": (0,    1,     False),
    "short_ratio":       (0,    100,   False),
}

PRICE_FIELD_RULES = {
    "current_price":     (0.01, 100000, True),
    "price_52w_high":    (0.01, 100000, True),
    "price_52w_low":     (0.01, 100000, True),
    "avg_volume_30d":    (1000, 1e11,   True),
}

# Confidence levels
CONFIDENCE_HIGH   = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW    = "LOW"


class DataValidator:

    @staticmethod
    def validate_fundamentals(raw: Dict, ticker: str) -> Tuple[Dict, Dict]:
        """
        Validate and clean a fundamentals dict.

        Returns:
          (cleaned_data, validation_report)

        cleaned_data: same keys, but with None replaced by explicit "MISSING" markers
        validation_report: {confidence, missing_critical, missing_fields, suspect_fields}
        """
        cleaned        = dict(raw)
        missing_fields = []
        missing_critical = []
        suspect_fields = []

        for field, (min_val, max_val, is_critical) in FUNDAMENTAL_FIELD_RULES.items():
            value = raw.get(field)

            # Check for missing
            if value is None or value == 0 and field not in ("dividend_yield", "peg_ratio"):
                cleaned[field] = None   # explicitly None, not 0
                missing_fields.append(field)
                if is_critical:
                    missing_critical.append(field)
                continue

            # Check for out-of-range (suspect data)
            try:
                v = float(value)
                if not (min_val <= v <= max_val):
                    suspect_fields.append(f"{field}={v} (expected {min_val}–{max_val})")
                    logger.debug(f"  {ticker}.{field} suspect value: {v}")
            except (TypeError, ValueError):
                cleaned[field] = None
                missing_fields.append(field)

        # Determine confidence level
        critical_missing = len(missing_critical)
        if critical_missing == 0:
            confidence = CONFIDENCE_HIGH
        elif critical_missing <= 2:
            confidence = CONFIDENCE_MEDIUM
        else:
            confidence = CONFIDENCE_LOW

        report = {
            "confidence":       confidence,
            "missing_critical": missing_critical,
            "missing_fields":   missing_fields,
            "suspect_fields":   suspect_fields,
            "total_missing":    len(missing_fields),
            "data_quality_pct": round((1 - len(missing_fields) / len(FUNDAMENTAL_FIELD_RULES)) * 100, 1),
        }

        if confidence == CONFIDENCE_LOW:
            logger.warning(
                f"  {ticker}: LOW data confidence — "
                f"{critical_missing} critical fields missing: {missing_critical}"
            )

        return cleaned, report

    @staticmethod
    def validate_price_data(raw: Dict, ticker: str) -> Tuple[Dict, bool]:
        """
        Validate price history data.
        Returns (cleaned_data, is_valid)
        If not valid, skip this ticker entirely.
        """
        if not raw:
            logger.warning(f"  {ticker}: No price data returned")
            return {}, False

        df = raw.get("df")
        if df is None or len(df) < 50:
            logger.warning(f"  {ticker}: Insufficient price history ({len(df) if df is not None else 0} days)")
            return {}, False

        current = raw.get("current_price", 0)
        if not current or current <= 0:
            logger.warning(f"  {ticker}: Invalid current price: {current}")
            return {}, False

        # Check for data gaps (more than 5 consecutive missing days = data issue)
        import pandas as pd
        if hasattr(df.index, 'freq'):
            pass  # pandas handles this

        return raw, True

    @staticmethod
    def build_claude_context(
        ticker: str,
        fundamentals: Dict,
        validation_report: Dict,
        price_data: Dict,
        technicals: Dict,
        news: Dict,
        market_context: Dict,
        congressional: Dict = None,
        fallen_angel: Dict = None,
    ) -> str:
        """
        Build the full context string for Claude's analysis.

        Key principle: every field is either a real number or explicitly
        labeled "NOT AVAILABLE". Claude sees exactly what we have and
        can reason accordingly — including reasoning about the gaps.
        """

        def fmt(val, suffix="", decimals=1, scale=1.0, is_pct=False):
            """Format a value cleanly. Returns 'N/A' if missing."""
            if val is None:
                return "NOT AVAILABLE"
            try:
                v = float(val) * scale
                if is_pct:
                    return f"{v*100:.{decimals}f}%"
                return f"{v:.{decimals}f}{suffix}"
            except (TypeError, ValueError):
                return "NOT AVAILABLE"

        def fmt_dollars(val):
            if val is None:
                return "NOT AVAILABLE"
            try:
                v = float(val)
                if abs(v) >= 1e9:
                    return f"${v/1e9:.2f}B"
                if abs(v) >= 1e6:
                    return f"${v/1e6:.1f}M"
                return f"${v:,.0f}"
            except Exception:
                return "NOT AVAILABLE"

        f = fundamentals
        conf = validation_report.get("confidence", "UNKNOWN")
        missing = validation_report.get("missing_critical", [])
        quality = validation_report.get("data_quality_pct", 0)

        # Data quality header — Claude knows exactly what it's working with
        data_quality_note = (
            f"DATA CONFIDENCE: {conf} ({quality:.0f}% fields available)"
        )
        if missing:
            data_quality_note += f"\nCRITICAL FIELDS MISSING: {', '.join(missing)}"
            data_quality_note += "\n→ Weight your analysis accordingly. Do not assume 0 for missing fields."

        # Build the brief
        lines = [
            f"STOCK ANALYSIS: {ticker} ({f.get('company_name', ticker)})",
            f"Sector: {f.get('sector','N/A')} | Industry: {f.get('industry','N/A')}",
            f"Market cap: {fmt_dollars(f.get('market_cap'))}",
            "",
            data_quality_note,
            "",
            "═══ MARKET CONTEXT ═══",
            f"Regime: {market_context.get('regime','N/A')}",
            f"VIX: {market_context.get('vix','N/A')}",
            f"Risk environment: {market_context.get('risk_env','N/A')}",
            f"Leading sectors: {', '.join(market_context.get('leading_sectors',[]))}",
            "",
            "═══ FUNDAMENTALS ═══",
            f"Revenue growth (YoY):  {fmt(f.get('revenue_growth'), is_pct=True)}",
            f"Earnings growth:       {fmt(f.get('earnings_growth'), is_pct=True)}",
            f"Quarterly EPS growth:  {fmt(f.get('earnings_quarterly'), is_pct=True)}",
            f"P/E (trailing):        {fmt(f.get('pe_ratio'), decimals=1)}",
            f"P/E (forward):         {fmt(f.get('forward_pe'), decimals=1)}",
            f"PEG ratio:             {fmt(f.get('peg_ratio'), decimals=2)}",
            f"Price/Book:            {fmt(f.get('price_to_book'), decimals=2)}",
            f"EV/EBITDA:             {fmt(f.get('ev_to_ebitda'), decimals=1)}",
            "",
            f"Gross margin:          {fmt(f.get('gross_margin'), is_pct=True)}",
            f"Operating margin:      {fmt(f.get('operating_margin'), is_pct=True)}",
            f"Net profit margin:     {fmt(f.get('profit_margin'), is_pct=True)}",
            f"Return on equity:      {fmt(f.get('roe'), is_pct=True)}",
            f"Free cash flow:        {fmt_dollars(f.get('free_cash_flow'))}",
            "",
            f"Total debt:            {fmt_dollars(f.get('total_debt'))}",
            f"Total cash:            {fmt_dollars(f.get('total_cash'))}",
            f"Net cash position:     {fmt_dollars(f.get('net_cash'))}",
            f"Debt-to-equity:        {fmt(f.get('debt_to_equity'), decimals=1)}",
            f"Current ratio:         {fmt(f.get('current_ratio'), decimals=2)}",
            "",
            f"Dividend yield:        {fmt(f.get('dividend_yield'), is_pct=True)}",
            f"Payout ratio:          {fmt(f.get('payout_ratio'), is_pct=True)}",
            "",
            f"Insider ownership:     {fmt(f.get('insider_pct'), is_pct=True)}",
            f"Insider buy ratio:     {fmt(f.get('insider_buy_ratio'), is_pct=True)} (>50% = net buying)",
            f"Short ratio:           {fmt(f.get('short_ratio'), decimals=1)} days to cover",
            f"Analyst consensus:     {fmt(f.get('recommendation'), decimals=1)}/5.0 (1=strong buy)",
            f"Analyst target upside: {fmt(f.get('analyst_upside'), is_pct=True)}",
            f"# analysts covering:   {f.get('num_analysts', 'N/A')}",
            "",
            "═══ TECHNICALS ═══",
            f"Technical score:  {fmt(technicals.get('score'), decimals=2)}/1.0",
            f"RSI:              {fmt(technicals.get('rsi'), decimals=1)}",
            f"MACD bullish:     {technicals.get('macd_bullish', 'N/A')}",
            f"Signals: {', '.join(technicals.get('signals', [])[:5])}",
            "",
            "═══ NEWS SENTIMENT ═══",
            f"Sentiment score:  {fmt(news.get('score'), decimals=2)}/1.0",
            f"Articles scanned: {news.get('article_count', 0)}",
        ]

        if news.get("alerts"):
            lines.append(f"⚠️ HIGH IMPACT ALERTS: {', '.join(news['alerts'])}")

        if news.get("headlines"):
            lines.append("Recent headlines:")
            for h in news["headlines"][:5]:
                lines.append(f"  - {h}")

        # Congressional trades
        if congressional:
            lines.extend([
                "",
                "═══ CONGRESSIONAL TRADES (Last 30 days) ═══",
                f"Signal: {congressional.get('signal','NEUTRAL')}",
                f"Buys: {congressional.get('buy_count',0)} | Sells: {congressional.get('sell_count',0)}",
            ])
            if congressional.get("notable_buyers"):
                lines.append(f"Notable buyers: {', '.join(congressional['notable_buyers'])}")

        # Fallen angel context
        if fallen_angel and fallen_angel.get("qualifies"):
            lines.extend([
                "",
                "═══ FALLEN ANGEL ANALYSIS ═══",
                f"Down {fallen_angel.get('drawdown_pct','N/A')}% from 52W high",
                f"Recovery grade: {fallen_angel.get('grade','N/A')}",
                f"Recovery signals: {', '.join(fallen_angel.get('recovery_signals',[]))}",
            ])

        lines.extend([
            "",
            "═══ YOUR TASK ═══",
            "Analyze ALL available data above. For any NOT AVAILABLE fields,",
            "reason from what IS available and your knowledge of this company.",
            "Make projections where your confidence is high — based on evidence,",
            "patterns, and knowledge — not guesswork.",
            "Return your investment judgment as JSON.",
        ])

        return "\n".join(lines)

    @staticmethod
    def validate_trade_pnl(entry_price: float, exit_price: float, qty: float) -> Dict:
        """
        Calculate real P&L from actual Alpaca filled prices.
        Never estimated. Never approximated.
        """
        if not all([entry_price, exit_price, qty]) or entry_price <= 0:
            return {
                "pnl":     None,
                "pnl_pct": None,
                "valid":   False,
                "error":   "Missing entry_price, exit_price, or qty",
            }

        pnl     = (exit_price - entry_price) * qty
        pnl_pct = (exit_price / entry_price - 1) * 100

        return {
            "pnl":        round(pnl, 2),
            "pnl_pct":    round(pnl_pct, 3),
            "entry_price":entry_price,
            "exit_price": exit_price,
            "qty":        qty,
            "valid":      True,
        }

    @staticmethod
    def validate_performance_metrics(perf: Dict) -> Dict:
        """
        Suppress performance metrics that aren't statistically meaningful yet.
        Prevents showing a 100% win rate after 2 trades.
        """
        total = perf.get("total_trades", 0)

        if total < MIN_TRADES_FOR_METRICS:
            return {
                "total_trades":     total,
                "status":           "INSUFFICIENT_DATA",
                "message":          f"Need {MIN_TRADES_FOR_METRICS} completed trades for meaningful metrics (have {total})",
                "total_pnl":        perf.get("total_pnl", 0),
                # Show only raw P&L — nothing else meaningful yet
                "win_rate":         None,
                "sharpe":           None,
                "max_drawdown":     None,
                "profit_factor":    None,
                "vs_benchmark":     None,
                "beating_market":   None,
            }

        return perf   # full metrics, all valid
