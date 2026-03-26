"""
TITAN UNIVERSE — Full Stock Universe
======================================
Split into 6 buckets:
  1. Long-term core compounders (moat + AI)
  2. Short-term momentum / growth
  3. Crypto-adjacent
  4. High-growth mid-caps
  5. Fallen angels (beaten-down high upside)
  6. Dividend / income / ballast

Each bucket has a strategy tag that affects how the scoring
weights and hold-time logic are applied.
"""

from typing import Dict, List

# ── Universe Definition ────────────────────────────────────────────────────

UNIVERSE: Dict[str, Dict] = {

    # ── 1. Long-Term Core Compounders ──────────────────────────────────────
    # Hold months to years. Moat + AI + growth are the dominant signals.
    "NVDA":  {"bucket": "CORE",     "strategy": "LONG",  "sector": "Technology"},
    "MSFT":  {"bucket": "CORE",     "strategy": "LONG",  "sector": "Technology"},
    "GOOGL": {"bucket": "CORE",     "strategy": "LONG",  "sector": "Technology"},
    "META":  {"bucket": "CORE",     "strategy": "LONG",  "sector": "Technology"},
    "AMZN":  {"bucket": "CORE",     "strategy": "LONG",  "sector": "Consumer Discretionary"},
    "AAPL":  {"bucket": "CORE",     "strategy": "LONG",  "sector": "Technology"},
    "BRK-B": {"bucket": "CORE",     "strategy": "LONG",  "sector": "Financials"},
    "LLY":   {"bucket": "CORE",     "strategy": "LONG",  "sector": "Healthcare"},
    "V":     {"bucket": "CORE",     "strategy": "LONG",  "sector": "Financials"},
    "MA":    {"bucket": "CORE",     "strategy": "LONG",  "sector": "Financials"},
    "COST":  {"bucket": "CORE",     "strategy": "LONG",  "sector": "Consumer Staples"},
    "UNH":   {"bucket": "CORE",     "strategy": "LONG",  "sector": "Healthcare"},

    # ── 2. Short-Term Momentum / Growth ───────────────────────────────────
    # Hold days to weeks. Technical + momentum + news are dominant signals.
    "TSLA":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Consumer Discretionary"},
    "AMD":   {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "PLTR":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "CRWD":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "NET":   {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "SNOW":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "PANW":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "SMCI":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "ARM":   {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "UBER":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "SHOP":  {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Technology"},
    "XYZ":   {"bucket": "MOMENTUM", "strategy": "SWING", "sector": "Financials"},

    # ── 3. Crypto-Adjacent ────────────────────────────────────────────────
    # High volatility. Smaller position sizes. Momentum-driven.
    "COIN":  {"bucket": "CRYPTO",   "strategy": "SWING", "sector": "Financials"},
    "MSTR":  {"bucket": "CRYPTO",   "strategy": "SWING", "sector": "Technology"},
    "RIOT":  {"bucket": "CRYPTO",   "strategy": "SWING", "sector": "Technology"},
    "MARA":  {"bucket": "CRYPTO",   "strategy": "SWING", "sector": "Technology"},
    "HOOD":  {"bucket": "CRYPTO",   "strategy": "SWING", "sector": "Financials"},

    # ── 4. High-Growth Mid-Caps ───────────────────────────────────────────
    # Higher risk, higher reward. Requires stronger fundamental validation.
    "RKLB":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Industrials"},
    "IONQ":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Technology"},
    "LUNR":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Industrials"},
    "ACHR":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Industrials"},
    "RGTI":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Technology"},
    "AFRM":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Financials"},
    "DUOL":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Technology"},
    "CELH":  {"bucket": "HIGHGROWTH","strategy": "SWING", "sector": "Consumer Staples"},

    # ── 5. Fallen Angels (beaten-down, high upside asymmetry) ─────────────
    # Stocks down 40-80% from highs with intact or recovering fundamentals.
    # Druckenmiller-style asymmetric bets. Small initial positions, add on confirmation.
    "INTC":  {"bucket": "FALLEN",   "strategy": "LONG",  "sector": "Technology"},
    "PFE":   {"bucket": "FALLEN",   "strategy": "LONG",  "sector": "Healthcare"},
    "DIS":   {"bucket": "FALLEN",   "strategy": "SWING", "sector": "Communication Services"},
    "NKE":   {"bucket": "FALLEN",   "strategy": "SWING", "sector": "Consumer Discretionary"},
    "MPW":   {"bucket": "FALLEN",   "strategy": "LONG",  "sector": "Real Estate"},
    "FOXA":  {"bucket": "FALLEN",   "strategy": "SWING", "sector": "Communication Services"},
    "CVS":   {"bucket": "FALLEN",   "strategy": "SWING", "sector": "Consumer Staples"},
    "PYPL":  {"bucket": "FALLEN",   "strategy": "SWING", "sector": "Financials"},

    # ── 6. Dividend / Income / Ballast ────────────────────────────────────
    # Defensive positions. Provide income + stability during downturns.
    # Hold long-term. Dividend sustainability is the key signal.
    "ABBV":  {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Healthcare"},
    "JNJ":   {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Healthcare"},
    "O":     {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Real Estate"},
    "MAIN":  {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Financials"},
    "PG":    {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Consumer Staples"},
    "KO":    {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Consumer Staples"},
    "JPM":   {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Financials"},
    "XOM":   {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Energy"},
    "CVX":   {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Energy"},
    "T":     {"bucket": "DIVIDEND", "strategy": "LONG",  "sector": "Communication Services"},

    # ── Macro Hedges ──────────────────────────────────────────────────────
    "GLD":   {"bucket": "HEDGE",    "strategy": "LONG",  "sector": "Materials"},
    "SLV":   {"bucket": "HEDGE",    "strategy": "SWING", "sector": "Materials"},
    "TLT":   {"bucket": "HEDGE",    "strategy": "SWING", "sector": "Bonds"},

    # ── Meme / Momentum Wild Cards ────────────────────────────────────────
    # Only entered when momentum score is extremely high + volume confirms
    "GME":   {"bucket": "MEME",     "strategy": "SWING", "sector": "Consumer Discretionary"},
    "AMC":   {"bucket": "MEME",     "strategy": "SWING", "sector": "Communication Services"},
}

# Bucket-level portfolio allocation caps (% of total portfolio)
BUCKET_CAPS = {
    "CORE":       0.40,   # max 40% in long-term core
    "MOMENTUM":   0.25,   # max 25% in swing/momentum
    "CRYPTO":     0.08,   # max 8% crypto-adjacent (volatile)
    "HIGHGROWTH": 0.10,   # max 10% high-growth mid-caps
    "FALLEN":     0.08,   # max 8% fallen angels (asymmetric bets)
    "DIVIDEND":   0.15,   # max 15% dividend/ballast
    "HEDGE":      0.05,   # max 5% macro hedges
    "MEME":       0.02,   # max 2% meme — only on extreme momentum signals
}

# Strategy-specific scoring weight overrides
# LONG positions weight fundamentals/moat more heavily
# SWING positions weight technicals/momentum/news more heavily
STRATEGY_WEIGHTS = {
    "LONG": {
        "technical":   0.08,
        "volume":      0.04,
        "fundamental": 0.20,
        "moat":        0.18,
        "dividend":    0.07,
        "management":  0.10,
        "growth":      0.12,
        "ai_exposure": 0.08,
        "sector":      0.04,
        "sentiment":   0.04,
        "ai_analysis": 0.05,
    },
    "SWING": {
        "technical":   0.20,
        "volume":      0.10,
        "fundamental": 0.10,
        "moat":        0.06,
        "dividend":    0.01,
        "management":  0.04,
        "growth":      0.08,
        "ai_exposure": 0.06,
        "sector":      0.10,
        "sentiment":   0.12,
        "ai_analysis": 0.13,
    },
}

# Minimum score thresholds by bucket (higher bar for riskier buckets)
MIN_SCORE_BY_BUCKET = {
    "CORE":       62,
    "MOMENTUM":   65,
    "CRYPTO":     72,   # needs strong signal to justify crypto exposure
    "HIGHGROWTH": 68,
    "FALLEN":     58,   # slightly lower bar — asymmetry is the edge
    "DIVIDEND":   60,
    "HEDGE":      55,
    "MEME":       80,   # extremely high bar for meme stocks
}

# Position size multipliers by bucket (relative to base Kelly size)
SIZE_MULTIPLIER_BY_BUCKET = {
    "CORE":       1.2,   # slightly larger for core compounders
    "MOMENTUM":   1.0,
    "CRYPTO":     0.5,   # half size for crypto volatility
    "HIGHGROWTH": 0.7,
    "FALLEN":     0.6,   # start small, add on confirmation
    "DIVIDEND":   1.0,
    "HEDGE":      0.8,
    "MEME":       0.3,   # tiny size — lottery ticket only
}

def get_tickers() -> List[str]:
    return list(UNIVERSE.keys())

def get_bucket(ticker: str) -> str:
    return UNIVERSE.get(ticker, {}).get("bucket", "MOMENTUM")

def get_strategy(ticker: str) -> str:
    return UNIVERSE.get(ticker, {}).get("strategy", "SWING")

def get_sector(ticker: str) -> str:
    return UNIVERSE.get(ticker, {}).get("sector", "Unknown")

def get_weights(ticker: str) -> Dict:
    strategy = get_strategy(ticker)
    return STRATEGY_WEIGHTS.get(strategy, STRATEGY_WEIGHTS["SWING"])
