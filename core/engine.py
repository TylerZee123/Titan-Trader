"""
TitanEngine — Final Version
=============================
All audit bugs fixed:
  - FIXED_UNIVERSE attribute used consistently
  - Optional imported at module level
  - Phase labels sequential and correct
  - STRATEGY_WEIGHTS copied (not mutated)
  - PositionAllocator is single source of truth for sizing
  - PositionReviewer (Claude) handles all hold/trim/exit decisions
  - PerformanceTracker wired in — every trade logged to Supabase
  - Earnings calendar actually blocks entries
  - Previous scores tracked for review trigger detection
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from data.market_data import MarketDataFetcher
from data.fundamental_data import FundamentalDataFetcher
from data.news_sentiment import NewsSentimentFetcher
from data.dynamic_universe import DynamicUniverseScanner
from data.earnings_calendar import EarningsCalendar
from data.congressional_trades import CongressionalTradesScanner
from data.fallen_angel_scanner import FallenAngelScanner
from data.validator import DataValidator
from data.universe import UNIVERSE, STRATEGY_WEIGHTS
from signals.technical import TechnicalSignals
from signals.fundamental import FundamentalSignals
from signals.ai_signal import AISignalEngine
from risk.risk_manager import RiskManager
from risk.position_allocator import PositionAllocator
from core.scorer import StockScorer
from core.executor import TradeExecutor
from core.position_reviewer import PositionReviewer
from performance.tracker import PerformanceTracker

logger = logging.getLogger("titan_trader")


class TitanEngine:

    FIXED_UNIVERSE = list(UNIVERSE.keys())

    def __init__(
        self,
        config: Dict,
        news_sentiments: Dict = None,
        loss_learner=None,
        previous_scores: Dict = None,
    ):
        self.config           = config
        self.news_sentiments  = news_sentiments or {}
        self.loss_learner     = loss_learner
        self.previous_scores  = previous_scores or {}   # {ticker: score_dict} from yesterday

        self.market_data         = MarketDataFetcher(config)
        self.fundamental_data    = FundamentalDataFetcher(config)
        self.news_sentiment      = NewsSentimentFetcher(config)
        self.technical_signals   = TechnicalSignals()
        self.fundamental_signals = FundamentalSignals()
        self.ai_signal           = AISignalEngine(config)
        self.risk_manager        = RiskManager(config)
        self.allocator           = PositionAllocator(config)
        self.scorer              = StockScorer()
        self.executor            = TradeExecutor(config)
        self.position_reviewer   = PositionReviewer(config)
        self.dynamic_scanner     = DynamicUniverseScanner(set(self.FIXED_UNIVERSE))
        self.earnings_cal        = EarningsCalendar()
        self.congress_scanner    = CongressionalTradesScanner()
        self.fallen_scanner      = FallenAngelScanner()
        self.validator           = DataValidator()
        self.tracker             = PerformanceTracker(config)

    def run(self) -> Dict:
        """Main daily run."""

        # ── Phase 1: Account state ─────────────────────────────────────────
        logger.info("Phase 1: Loading account state...")
        account           = self.executor.get_account()
        current_positions = self.executor.get_positions()
        account["open_positions"] = len(current_positions)
        logger.info(f"  Portfolio: ${float(account['portfolio_value']):,.2f} | "
                    f"Cash: ${float(account['cash']):,.2f} | "
                    f"Positions: {len(current_positions)}")

        if not self.risk_manager.check_daily_loss_limit(account):
            return {"status": "HALTED", "reason": "daily_loss_limit", "account": account}

        # ── Phase 2: Market context ────────────────────────────────────────
        logger.info("Phase 2: Fetching market context...")
        market_context = self.market_data.get_market_context()
        logger.info(f"  Regime: {market_context.get('regime')} | "
                    f"VIX: {market_context.get('vix')} | "
                    f"Risk: {market_context.get('risk_env')}")

        # Log daily snapshot to Supabase
        self.tracker.log_daily_snapshot(account, market_context)

        # ── Phase 3: Dynamic universe scan ────────────────────────────────
        logger.info("Phase 3: Dynamic universe scan...")
        dynamic_candidates = self.dynamic_scanner.run_all_scanners(market_context)
        dynamic_tickers    = {c["ticker"]: c for c in dynamic_candidates}
        full_universe      = self.FIXED_UNIVERSE + [c["ticker"] for c in dynamic_candidates]
        logger.info(f"  Universe: {len(full_universe)} total "
                    f"({len(self.FIXED_UNIVERSE)} fixed + {len(dynamic_candidates)} dynamic)")

        # ── Phase 4: Supporting data ───────────────────────────────────────
        logger.info("Phase 4: Fetching supporting data...")
        congress_data     = self.congress_scanner.get_recent_trades(full_universe, days_back=30)
        upcoming_earnings = self.earnings_cal.get_upcoming_earnings(full_universe, days_ahead=5)
        if upcoming_earnings:
            logger.info(f"  Earnings this week: {list(upcoming_earnings.keys())}")

        # ── Phase 5: Score all stocks ──────────────────────────────────────
        logger.info("Phase 5: Scoring all candidates...")

        # Get base weights, apply learned adjustments
        base_weights = {
            "technical": 0.12, "volume": 0.06, "fundamental": 0.15,
            "moat": 0.12, "dividend": 0.05, "management": 0.08,
            "growth": 0.10, "ai_exposure": 0.08, "sector": 0.07,
            "sentiment": 0.07, "ai_analysis": 0.10,
        }
        learned_weights = (
            self.loss_learner.get_adjusted_weights(base_weights)
            if self.loss_learner else base_weights
        )

        scored_stocks = []
        for ticker in full_universe:
            try:
                dynamic_info  = dynamic_tickers.get(ticker)
                congress_info = congress_data.get(ticker, {})
                earnings_info = upcoming_earnings.get(ticker, {})

                score = self._score_stock(
                    ticker=ticker,
                    market_context=market_context,
                    learned_weights=learned_weights,
                    dynamic_info=dynamic_info,
                    congress_info=congress_info,
                    earnings_info=earnings_info,
                )
                if score:
                    scored_stocks.append(score)
                    tag = f"[{dynamic_info['source']}]" if dynamic_info else "[FIXED]"
                    logger.info(
                        f"  {ticker:6s} {tag:22s} "
                        f"{score['total_score']:5.1f}/100 "
                        f"[{score['signal']:10s}] "
                        f"conf={score['data_confidence']}"
                    )
            except Exception as e:
                logger.warning(f"  {ticker}: ERROR — {e}")

        scored_stocks.sort(key=lambda x: x["total_score"], reverse=True)
        score_map = {s["ticker"]: s for s in scored_stocks}

        # ── Phase 6: Review held positions (Claude decides) ───────────────
        logger.info("Phase 6: Reviewing held positions...")
        positions_needing_review = self.risk_manager.get_positions_needing_review(
            current_positions=current_positions,
            scored_stocks=scored_stocks,
            previous_scores=self.previous_scores,
            news_sentiments=self.news_sentiments,
        )

        position_reviews = {}
        for ticker, trigger in positions_needing_review.items():
            position     = next((p for p in current_positions if p["ticker"] == ticker), None)
            current_score= score_map.get(ticker, {})
            previous_score= self.previous_scores.get(ticker)
            news_info    = self.news_sentiments.get(ticker, {})
            recent_news  = news_info.get("headlines", [])

            if position and current_score:
                review = self.position_reviewer.review_position(
                    position=position,
                    current_score=current_score,
                    previous_score=previous_score,
                    trigger=trigger,
                    market_context=market_context,
                    recent_news=recent_news,
                )
                position_reviews[ticker] = review

        # ── Phase 7: Allocate new positions ───────────────────────────────
        logger.info("Phase 7: Allocating new positions...")
        allocation = self.allocator.allocate(
            scored_stocks=scored_stocks,
            portfolio_value=float(account["portfolio_value"]),
            current_positions=current_positions,
            market_context=market_context,
            news_sentiments=self.news_sentiments,
        )

        # ── Phase 8: Build trade plan ──────────────────────────────────────
        logger.info("Phase 8: Building trade plan...")
        trade_plan = self.risk_manager.build_trade_plan(
            scored_stocks=scored_stocks,
            current_positions=current_positions,
            account=account,
            market_context=market_context,
            allocation=allocation,
            position_reviews=position_reviews,
            earnings_calendar=upcoming_earnings,
        )
        trade_plan["allocation"] = allocation

        # ── Phase 9: Execute ───────────────────────────────────────────────
        logger.info("Phase 9: Executing trades...")
        if self.config["mode"] == "trade":
            execution_results = self.executor.execute_plan(
                trade_plan=trade_plan,
                performance_tracker=self.tracker,
                market_context=market_context,
            )
        else:
            execution_results = {"mode": "analyze_only", "plan": trade_plan}
            logger.info("  (Mode = analyze — no real trades placed)")

        report = {
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "account":          account,
            "market_context":   market_context,
            "top_10_stocks":    scored_stocks[:10],
            "all_scored_detail":scored_stocks,          # full list for daily_scores save
            "all_scored":       len(scored_stocks),
            "dynamic_adds":     len(dynamic_candidates),
            "position_reviews": position_reviews,
            "trade_plan":       trade_plan,
            "execution":        execution_results,
            "mode":             self.config["mode"],
        }

        # Sync performance to Google Sheets
        perf = self.tracker.get_performance_summary()
        from data.validator import DataValidator
        perf = DataValidator.validate_performance_metrics(perf)
        self.tracker.sync_to_sheets(perf, trade_plan.get("buys", []))

        report["performance"] = perf
        return report

    def _score_stock(
        self,
        ticker: str,
        market_context: Dict,
        learned_weights: Dict,
        dynamic_info: Optional[Dict] = None,
        congress_info: Optional[Dict] = None,
        earnings_info: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Score a single stock. Returns None if data quality too low."""

        # Determine strategy and bucket
        if dynamic_info:
            strategy = dynamic_info.get("strategy", "SWING")
            bucket   = dynamic_info.get("bucket", "MOMENTUM")
            sector   = dynamic_info.get("sector", "Unknown")
        else:
            meta     = UNIVERSE.get(ticker, {})
            strategy = meta.get("strategy", "SWING")
            bucket   = meta.get("bucket", "MOMENTUM")
            sector   = meta.get("sector", "Unknown")

        # Copy weights — NEVER mutate the original dict
        from data.universe import STRATEGY_WEIGHTS
        ticker_weights = dict(STRATEGY_WEIGHTS.get(strategy, STRATEGY_WEIGHTS["SWING"]))

        # Blend with learned adjustments (80% strategy, 20% learned)
        for k in ticker_weights:
            if k in learned_weights:
                ticker_weights[k] = round(
                    ticker_weights[k] * 0.80 + learned_weights[k] * 0.20, 4
                )

        # Fetch + validate price data
        raw_price              = self.market_data.get_price_history(ticker, days=365)
        price_data, price_valid= DataValidator.validate_price_data(raw_price, ticker)
        if not price_valid:
            return None

        # Fetch + validate fundamentals
        raw_fund               = self.fundamental_data.get_fundamentals(ticker)
        fundamentals, val_report = DataValidator.validate_fundamentals(raw_fund, ticker)

        # Skip if data quality is critically low
        if (val_report["confidence"] == "LOW"
                and len(val_report["missing_critical"]) > 4):
            logger.warning(f"  {ticker}: skip — data too incomplete")
            return None

        # Fallen angel check
        fallen_info = None
        if bucket == "FALLEN" or (dynamic_info and dynamic_info.get("source") == "VALUE_DISLOCATION"):
            fallen_info = self.fallen_scanner._analyze(ticker)

        # Score all 11 dimensions
        tech           = self.technical_signals.analyze(price_data)
        volume_score   = self.technical_signals.volume_analysis(price_data)
        fund_score     = self.fundamental_signals.analyze(fundamentals)
        moat_score     = self.fundamental_signals.moat_score(fundamentals)
        dividend_score = self.fundamental_signals.dividend_score(fundamentals)
        mgmt_score     = self.fundamental_signals.management_score(fundamentals, ticker)
        growth_score   = self.fundamental_signals.growth_score(fundamentals)
        ai_exp_score   = self.fundamental_signals.ai_exposure_score(fundamentals, ticker)
        sector_score   = self.market_data.get_sector_score(ticker, market_context)
        news           = self.news_sentiment.get_sentiment(ticker)
        sentiment_score= news["score"]

        # Congressional boost
        if congress_info and congress_info.get("score_boost"):
            sentiment_score = min(1.0, sentiment_score + congress_info["score_boost"] / 100)

        # Fallen angel bonus
        if fallen_info and fallen_info.get("qualifies"):
            growth_score = min(1.0, growth_score + fallen_info.get("score_bonus", 0) / 100)

        # Claude deep analysis
        ai_analysis = self.ai_signal.analyze(
            ticker=ticker,
            fundamentals=fundamentals,
            technicals=tech,
            news=news,
            market_context=market_context,
            validation_report=val_report,
            congressional=congress_info,
            fallen_angel=fallen_info,
        )

        components = {
            "technical":   tech["score"],
            "volume":      volume_score,
            "fundamental": fund_score,
            "moat":        moat_score,
            "dividend":    dividend_score,
            "management":  mgmt_score,
            "growth":      growth_score,
            "ai_exposure": ai_exp_score,
            "sector":      sector_score,
            "sentiment":   sentiment_score,
            "ai_analysis": ai_analysis["score"],
        }

        total_score = sum(
            components[k] * ticker_weights.get(k, 0.09)
            for k in components
        ) * 100

        # Data confidence penalty
        if val_report["confidence"] == "MEDIUM": total_score *= 0.95
        elif val_report["confidence"] == "LOW":  total_score *= 0.85

        # Earnings proximity penalty
        if earnings_info:
            days = earnings_info.get("days_until", 99)
            if days <= 1:   total_score *= 0.70
            elif days <= 3: total_score *= 0.87

        # Signal
        if total_score >= 72:   signal = "STRONG_BUY"
        elif total_score >= 60: signal = "BUY"
        elif total_score >= 45: signal = "HOLD"
        elif total_score >= 35: signal = "WATCH"
        else:                   signal = "AVOID"

        return {
            "ticker":             ticker,
            "total_score":        round(total_score, 2),
            "signal":             signal,
            "strategy":           strategy,
            "bucket":             bucket,
            "sector":             sector,
            "components":         {k: round(v, 3) for k, v in components.items()},
            "ai_reasoning":       ai_analysis.get("reasoning", ""),
            "ai_risks":           ai_analysis.get("risks", []),
            "ai_catalysts":       ai_analysis.get("catalysts", []),
            "projected_return":   ai_analysis.get("projected_return_12m"),
            "price":              price_data.get("current_price"),
            "data_confidence":    val_report["confidence"],
            "earnings_upcoming":  earnings_info.get("earnings_date") if earnings_info else None,
            "congress_signal":    congress_info.get("signal") if congress_info else None,
            "discovery_source":   dynamic_info.get("source") if dynamic_info else "FIXED",
            "discovery_reason":   dynamic_info.get("discovery_reason") if dynamic_info else None,
        }
