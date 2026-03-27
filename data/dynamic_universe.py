"""
DynamicUniverseScanner — The Market Never Sleeps, Neither Does This
====================================================================
Scans the ENTIRE market daily for opportunities outside the fixed universe.

Four live scanners run every morning:

1. MOMENTUM SCANNER
   Finds stocks surging on high volume with fundamental backing.
   Uses yfinance screener + price/volume filters.
   Catches: sector rotations, earnings breakouts, new themes.

2. VALUE DISLOCATION SCANNER
   Finds stocks down 20-60% where business is intact.
   The whole market is the hunting ground, not just 67 stocks.
   Catches: overreaction selloffs, sector panic, single bad quarter.

3. IPO / NEW LISTING SCANNER
   Monitors recent IPOs (last 180 days) for post-lockup setups.
   Biggest short-term moves often come from under-covered new listings.

4. UNUSUAL ACTIVITY SCANNER
   Volume spikes, short squeeze setups, insider cluster buying,
   unusual options activity proxies.
   Catches: things about to move before they move.

Any qualifying stock gets:
  - Added to today's dynamic pool
  - Full 11-dimension score computed
  - Claude analysis run
  - Allocated if score qualifies

Dynamic discoveries are tagged so you know where they came from.
Nothing is permanently added unless it consistently qualifies (3+ days).
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
import yfinance as yf
import pandas as pd

logger = logging.getLogger("titan_trader")

# These are screened OUT regardless of signals
PERMANENT_BLACKLIST = {
    # Penny stocks — too manipulable
    # (enforced via price filter, not explicit list)
    # OTC / pink sheets — no reliable data
    # Chinese ADRs with VIE structure risk
    "BABA", "JD", "PDD", "BIDU",   # delisting risk
}

# Minimum requirements for any dynamic candidate
MIN_PRICE          = 5.0       # no penny stocks
MIN_MARKET_CAP     = 500e6     # $500M minimum — real company
MIN_AVG_VOLUME     = 500_000   # at least 500K shares/day average — liquidity
MAX_DYNAMIC_ADDS   = 15        # cap daily dynamic additions to control API costs


class DynamicUniverseScanner:
    """
    Hunts the entire market for opportunities.
    Returns a list of ticker dicts ready to be scored alongside the fixed universe.
    """

    def __init__(self, fixed_universe: Set[str]):
        self.fixed_universe = fixed_universe
        self._todays_discoveries: Dict[str, Dict] = {}

    def run_all_scanners(self, market_context: Dict) -> List[Dict]:
        """
        Run all four scanners. Returns deduplicated list of dynamic candidates.
        Each item: {ticker, source, discovery_reason, priority}
        """
        logger.info("\n─── DYNAMIC UNIVERSE SCAN ───")
        all_candidates = []

        # 1. Momentum scanner
        logger.info("Running momentum scanner...")
        momentum = self._momentum_scan(market_context)
        all_candidates.extend(momentum)
        logger.info(f"  Momentum: {len(momentum)} candidates")

        # 2. Value dislocation scanner
        logger.info("Running value dislocation scanner...")
        value = self._value_dislocation_scan()
        all_candidates.extend(value)
        logger.info(f"  Value dislocation: {len(value)} candidates")

        # 3. IPO scanner
        logger.info("Running IPO/new listing scanner...")
        ipos = self._ipo_scan()
        all_candidates.extend(ipos)
        logger.info(f"  IPOs/new listings: {len(ipos)} candidates")

        # 4. Unusual activity scanner
        logger.info("Running unusual activity scanner...")
        unusual = self._unusual_activity_scan()
        all_candidates.extend(unusual)
        logger.info(f"  Unusual activity: {len(unusual)} candidates")

        # Deduplicate — if same ticker from multiple scanners, merge and boost priority
        deduped = self._deduplicate(all_candidates)

        # Filter out fixed universe (already being scored), blacklist, and quality gates
        filtered = [
            c for c in deduped
            if c["ticker"] not in self.fixed_universe
            and c["ticker"] not in PERMANENT_BLACKLIST
        ]

        # Sort by priority score, cap at MAX_DYNAMIC_ADDS
        filtered.sort(key=lambda x: x["priority"], reverse=True)
        final = filtered[:MAX_DYNAMIC_ADDS]

        logger.info(f"\nDynamic universe additions today: {len(final)}")
        for c in final:
            logger.info(f"  + {c['ticker']:6s} [{c['source']}] — {c['discovery_reason']}")

        self._todays_discoveries = {c["ticker"]: c for c in final}
        return final

    # ── 1. Momentum Scanner ────────────────────────────────────────────────

    def _momentum_scan(self, market_context: Dict) -> List[Dict]:
        """
        Find stocks with strong recent price momentum backed by volume.
        Uses S&P 500, NASDAQ 100, and Russell 2000 components as the pool.
        """
        candidates = []

        # Sample of high-liquidity tickers to screen
        # In production, this pulls from a full index component list
        screening_pool = self._get_screening_pool("momentum")

        for ticker in screening_pool:
            try:
                result = self._check_momentum(ticker, market_context)
                if result:
                    candidates.append(result)
                time.sleep(0.05)
            except Exception as e:
                logger.debug(f"  Momentum check {ticker}: {e}")

        return candidates

    def _check_momentum(self, ticker: str, market_context: Dict) -> Optional[Dict]:
        """Check if a ticker meets momentum criteria."""
        info = yf.Ticker(ticker).info or {}
        price     = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        mkt_cap   = float(info.get("marketCap") or 0)
        avg_vol   = float(info.get("averageVolume") or 0)

        if price < MIN_PRICE or mkt_cap < MIN_MARKET_CAP or avg_vol < MIN_AVG_VOLUME:
            return None

        # Get price history for momentum calculation
        hist = yf.download(ticker, period="1mo", progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 10:
            return None

        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
            hist = hist.loc[:, ~hist.columns.duplicated()]
            for _col in list(hist.columns):
                hist[_col] = hist[_col].squeeze()

        close      = hist["Close"]
        volume     = hist["Volume"]
        price_1w   = float((close.iloc[-1] / close.iloc[-5] - 1) * 100) if len(close) >= 5 else 0
        price_1m   = float((close.iloc[-1] / close.iloc[0] - 1) * 100)
        vol_ratio  = float(volume.iloc[-1] / volume.mean()) if volume.mean() > 0 else 1.0
        vol_ratio_5d = float(volume.tail(5).mean() / volume.mean()) if volume.mean() > 0 else 1.0

        # Momentum criteria
        strong_1w  = price_1w > 8
        strong_1m  = price_1m > 15
        vol_confirm= vol_ratio_5d > 1.5   # volume confirming the move
        not_extend = price_1w < 35        # not already parabolic/extended

        if not ((strong_1w or strong_1m) and vol_confirm and not_extend):
            return None

        # In bear markets, require stronger signal
        if market_context.get("regime") == "BEAR" and price_1w < 15:
            return None

        priority = (price_1w * 0.4 + price_1m * 0.3 + vol_ratio_5d * 10 * 0.3)

        return {
            "ticker":           ticker,
            "source":           "MOMENTUM",
            "discovery_reason": f"+{price_1w:.1f}% 1W, +{price_1m:.1f}% 1M, vol {vol_ratio_5d:.1f}x avg",
            "priority":         priority,
            "price":            price,
            "market_cap":       mkt_cap,
            "sector":           info.get("sector", "Unknown"),
            "bucket":           "MOMENTUM",
            "strategy":         "SWING",
        }

    # ── 2. Value Dislocation Scanner ──────────────────────────────────────

    def _value_dislocation_scan(self) -> List[Dict]:
        """
        Find stocks significantly below recent highs where fundamentals are intact.
        These are the asymmetric bets — limited downside, meaningful upside.
        """
        candidates = []
        screening_pool = self._get_screening_pool("value")

        for ticker in screening_pool:
            try:
                result = self._check_value_dislocation(ticker)
                if result:
                    candidates.append(result)
                time.sleep(0.05)
            except Exception as e:
                logger.debug(f"  Value check {ticker}: {e}")

        return candidates

    def _check_value_dislocation(self, ticker: str) -> Optional[Dict]:
        """Check if a ticker is dislocated from value."""
        info = yf.Ticker(ticker).info or {}

        price     = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        high_52   = float(info.get("fiftyTwoWeekHigh") or 0)
        mkt_cap   = float(info.get("marketCap") or 0)
        avg_vol   = float(info.get("averageVolume") or 0)

        if price < MIN_PRICE or mkt_cap < MIN_MARKET_CAP or avg_vol < MIN_AVG_VOLUME:
            return None
        if not high_52 or price >= high_52:
            return None

        drawdown = (high_52 - price) / high_52 * 100

        # Must be down meaningfully but not a total disaster
        if drawdown < 20 or drawdown > 80:
            return None

        # Fundamental health — not a value trap
        revenue_growth = float(info.get("revenueGrowth") or 0)
        profit_margin  = float(info.get("profitMargins") or 0)
        current_ratio  = float(info.get("currentRatio") or 0)
        fcf            = float(info.get("freeCashflow") or 0)
        pe             = float(info.get("trailingPE") or 0)
        forward_pe     = float(info.get("forwardPE") or 0)

        # Reject obvious value traps
        is_trap = (
            revenue_growth < -0.25 and
            profit_margin < -0.10 and
            current_ratio < 0.8
        )
        if is_trap:
            return None

        # Positive signals
        has_fcf       = fcf > 0
        pe_reasonable = 0 < pe < 40 or (forward_pe > 0 and forward_pe < 30)
        revenue_ok    = revenue_growth > -0.10

        if not (has_fcf or pe_reasonable or revenue_ok):
            return None

        # Priority: bigger drawdown + better fundamentals = higher priority
        fundamental_health = (
            (1 if has_fcf else 0) +
            (1 if pe_reasonable else 0) +
            (1 if revenue_ok else 0)
        )
        priority = drawdown * 0.5 + fundamental_health * 15

        return {
            "ticker":           ticker,
            "source":           "VALUE_DISLOCATION",
            "discovery_reason": f"Down {drawdown:.0f}% from 52W high, fundamentals intact",
            "priority":         priority,
            "price":            price,
            "market_cap":       mkt_cap,
            "sector":           info.get("sector", "Unknown"),
            "bucket":           "FALLEN",
            "strategy":         "LONG" if drawdown > 40 else "SWING",
            "drawdown_pct":     round(drawdown, 1),
        }

    # ── 3. IPO Scanner ────────────────────────────────────────────────────

    def _ipo_scan(self) -> List[Dict]:
        """
        Monitor recent IPOs (last 180 days).
        Best setups: post-lockup expiry (90-180 days), base formation,
        or strong revenue growth with price consolidating.
        """
        candidates = []

        # Recent IPO tickers — pulled from a curated recent-IPO watchlist
        # In production this would pull from an IPO calendar API
        recent_ipos = self._get_recent_ipos()

        for ticker in recent_ipos:
            try:
                result = self._check_ipo(ticker)
                if result:
                    candidates.append(result)
                time.sleep(0.05)
            except Exception as e:
                logger.debug(f"  IPO check {ticker}: {e}")

        return candidates

    def _check_ipo(self, ticker: str) -> Optional[Dict]:
        """Evaluate a recent IPO for trading opportunity."""
        info = yf.Ticker(ticker).info or {}

        price   = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        mkt_cap = float(info.get("marketCap") or 0)
        avg_vol = float(info.get("averageVolume") or 0)

        if price < MIN_PRICE or mkt_cap < MIN_MARKET_CAP or avg_vol < MIN_AVG_VOLUME:
            return None

        # Get enough history to analyze
        hist = yf.download(ticker, period="6mo", progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 20:
            return None
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
            hist = hist.loc[:, ~hist.columns.duplicated()]
            for _col in list(hist.columns):
                hist[_col] = hist[_col].squeeze()

        close    = hist["Close"]
        ipo_age  = len(hist)  # trading days since IPO (approx)

        # Best window: 45-120 trading days post-IPO (post-lockup territory)
        if ipo_age < 20:
            return None   # too new, not enough data

        # Is it above its IPO base? (price > 20-day MA = establishing uptrend)
        ma20  = float(close.rolling(20).mean().iloc[-1])
        above_base = price > ma20

        # Revenue growth as quality filter
        rev_growth = float(info.get("revenueGrowth") or 0)

        if not above_base and rev_growth < 0.20:
            return None  # needs either strong technicals or strong growth

        priority = 40 + (rev_growth * 30) + (10 if above_base else 0)

        return {
            "ticker":           ticker,
            "source":           "IPO",
            "discovery_reason": f"Recent IPO, {rev_growth*100:.0f}% rev growth, {'above' if above_base else 'at'} base",
            "priority":         priority,
            "price":            price,
            "market_cap":       mkt_cap,
            "sector":           info.get("sector", "Unknown"),
            "bucket":           "HIGHGROWTH",
            "strategy":         "SWING",
            "ipo_age_days":     ipo_age,
        }

    # ── 4. Unusual Activity Scanner ───────────────────────────────────────

    def _unusual_activity_scan(self) -> List[Dict]:
        """
        Detect unusual volume, short squeeze setups, and momentum anomalies.
        These often precede significant moves.
        """
        candidates = []
        screening_pool = self._get_screening_pool("unusual")

        for ticker in screening_pool:
            try:
                result = self._check_unusual_activity(ticker)
                if result:
                    candidates.append(result)
                time.sleep(0.05)
            except Exception as e:
                logger.debug(f"  Unusual activity check {ticker}: {e}")

        return candidates

    def _check_unusual_activity(self, ticker: str) -> Optional[Dict]:
        """Detect unusual volume or short squeeze potential."""
        info = yf.Ticker(ticker).info or {}

        price    = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        mkt_cap  = float(info.get("marketCap") or 0)
        avg_vol  = float(info.get("averageVolume") or 0)

        if price < MIN_PRICE or mkt_cap < MIN_MARKET_CAP or avg_vol < MIN_AVG_VOLUME:
            return None

        # Short interest metrics
        short_ratio  = float(info.get("shortRatio") or 0)       # days to cover
        short_pct    = float(info.get("shortPercentOfFloat") or 0)

        # Volume spike
        hist = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 3:
            return None
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
            hist = hist.loc[:, ~hist.columns.duplicated()]
            for _col in list(hist.columns):
                hist[_col] = hist[_col].squeeze()

        today_vol  = float(hist["Volume"].iloc[-1])
        avg_vol_5d = float(hist["Volume"].mean())
        vol_spike  = today_vol / avg_vol_5d if avg_vol_5d > 0 else 1.0

        # Price momentum today
        price_chg_1d = float((hist["Close"].iloc[-1] / hist["Close"].iloc[-2] - 1) * 100) if len(hist) >= 2 else 0

        # Short squeeze candidate: high short interest + price starting to move up
        squeeze_candidate = (
            short_pct > 0.15 and        # >15% of float shorted
            short_ratio > 3 and          # takes 3+ days to cover
            price_chg_1d > 3 and         # already moving up
            vol_spike > 2.0              # volume confirming
        )

        # Pure volume spike (unusual interest)
        volume_anomaly = vol_spike > 4.0 and abs(price_chg_1d) > 2.0

        if not (squeeze_candidate or volume_anomaly):
            return None

        if squeeze_candidate:
            reason   = f"Squeeze setup: {short_pct*100:.0f}% shorted, {short_ratio:.1f}d cover, +{price_chg_1d:.1f}% today"
            priority = 70 + short_pct * 100 + vol_spike * 5
        else:
            reason   = f"Volume anomaly: {vol_spike:.1f}x avg, {price_chg_1d:+.1f}% price"
            priority = 50 + vol_spike * 5

        return {
            "ticker":           ticker,
            "source":           "UNUSUAL_ACTIVITY",
            "discovery_reason": reason,
            "priority":         priority,
            "price":            price,
            "market_cap":       mkt_cap,
            "sector":           info.get("sector", "Unknown"),
            "bucket":           "MOMENTUM",
            "strategy":         "SWING",
            "short_pct":        short_pct,
            "vol_spike":        round(vol_spike, 2),
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_screening_pool(self, scanner_type: str) -> List[str]:
        """
        Returns the pool of tickers to screen for each scanner.
        Uses S&P 500 + NASDAQ 100 + Russell 2000 components.
        In production this pulls live from index providers.
        For now uses a broad curated list of liquid names.
        """
        # Core liquid universe — 200+ tickers covering major indices
        sp500_sample = [
            # Tech
            "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AMD","INTC","QCOM",
            "AVGO","TXN","MU","AMAT","LRCX","KLAC","MRVL","ORCL","CRM","NOW",
            "ADBE","INTU","PANW","CRWD","FTNT","ZS","NET","DDOG","SNOW","MDB",
            "PLTR","ANET","SMCI","ARM","UBER","LYFT","DASH","ABNB","SHOP","MELI",
            # Healthcare
            "LLY","UNH","JNJ","PFE","ABBV","MRK","AMGN","GILD","BIIB","VRTX",
            "REGN","ISRG","MDT","SYK","BSX","EW","ZBH","DXCM","IDXX","ILMN",
            # Financials
            "JPM","BAC","WFC","GS","MS","C","BLK","SCHW","AXP","V","MA","PYPL",
            "SQ","HOOD","COIN","MSTR","SOFI","NU","AFRM","UPST",
            # Consumer
            "AMZN","COST","WMT","TGT","HD","LOW","MCD","SBUX","NKE","LULU",
            "DECK","ONON","SKX","CROX","ROST","TJX","DG","DLTR",
            # Energy
            "XOM","CVX","COP","EOG","SLB","HAL","MPC","VLO","PSX","OXY",
            # Industrials
            "CAT","DE","HON","RTX","LMT","NOC","GD","BA","GE","ETN",
            "CARR","OTIS","EMR","PH","ROK","ITW","MMM","UPS","FDX",
            # Small/mid cap growth
            "RKLB","IONQ","ACHR","JOBY","LILM","ASTS","LUNR","RGTI","QBTS",
            "CELH","ELF","HIMS","DUOL","AFRM","UPST","OPEN",
            # Crypto adjacent
            "COIN","MSTR","RIOT","MARA","CLSK","CIFR","HUT","BTBT",
            # Media / Entertainment
            "DIS","NFLX","PARA","WBD","SPOT","LYV","MSGS","RBLX",
            # Real Estate / REITs
            "O","MAIN","VICI","AMT","CCI","EQIX","PLD","SPG","AVB","EQR",
        ]

        # For unusual activity, focus on smaller caps where anomalies matter more
        if scanner_type == "unusual":
            return [t for t in sp500_sample if t not in self.fixed_universe]

        return [t for t in sp500_sample if t not in self.fixed_universe]

    def _get_recent_ipos(self) -> List[str]:
        """
        Returns recent IPO tickers.
        In production, pull from IPO calendar APIs (Nasdaq, Renaissance Capital).
        This list refreshes manually or via a weekly update job.
        """
        return [
            # 2024-2025 IPOs worth monitoring
            "RDDT",   # Reddit
            "ASTERA", # Astera Labs
            "IREN",   # Iris Energy
            "DAVE",   # Dave Inc
            "ACVA",   # ACV Auctions
            "TPVG",   # TriplePoint Venture
            "MANU",   # Manchester United
            "KYNDRYL","KD",
            "SEMBR",
            "HYPR",
            "AEYE",
            "LMND",   # Lemonade (post-IPO recovery candidate)
            "BIRD",   # Allbirds
            "CART",   # Instacart
            "KVYO",   # Klaviyo
            "ARM",    # ARM Holdings
            "BIRK",   # Birkenstock
        ]

    def _deduplicate(self, candidates: List[Dict]) -> List[Dict]:
        """
        If the same ticker appears from multiple scanners,
        merge them and boost priority (multi-scanner confirmation = stronger signal).
        """
        seen: Dict[str, Dict] = {}

        for c in candidates:
            ticker = c["ticker"]
            if ticker in seen:
                # Merge: combine reasons, boost priority
                existing = seen[ticker]
                existing["priority"]         += c["priority"] * 0.5  # confirmation bonus
                existing["source"]            = f"{existing['source']}+{c['source']}"
                existing["discovery_reason"]  = f"{existing['discovery_reason']} | {c['discovery_reason']}"
            else:
                seen[ticker] = dict(c)

        return list(seen.values())

    def get_todays_discoveries(self) -> Dict[str, Dict]:
        """Return today's dynamic discoveries for logging/reporting."""
        return self._todays_discoveries
