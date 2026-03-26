"""
TITAN TRADER — Final Entry Point
==================================
Fixes from audit:
  - Market holiday check at top of every run
  - engine.FIXED_UNIVERSE used (not engine.UNIVERSE)
  - _analyze_closed_losses uses real P&L from Alpaca, not hardcoded -7%
  - Previous scores loaded from Supabase for review trigger detection
  - PerformanceTracker wired into post-market loss logging
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Dict

from core.engine import TitanEngine
from data.news_scanner import NewsScanner
from learning.loss_learner import LossLearningEngine
from performance.tracker import PerformanceTracker
from utils.logger import setup_logger
from utils.notifier import Notifier
from utils.market_calendar import assert_trading_day, is_trading_day

logger = setup_logger("titan_trader")


def build_config() -> Dict:
    return {
        "alpaca_api_key":     os.environ.get("ALPACA_API_KEY"),
        "alpaca_secret_key":  os.environ.get("ALPACA_SECRET_KEY"),
        "alpaca_paper":       os.environ.get("ALPACA_PAPER", "true").lower() == "true",
        "anthropic_api_key":  os.environ.get("ANTHROPIC_API_KEY"),
        "notification_email": os.environ.get("NOTIFICATION_EMAIL", "tylerzar24@gmail.com"),
        "max_portfolio_size": int(os.environ.get("MAX_PORTFOLIO_SIZE", "12")),
        "max_position_pct":   float(os.environ.get("MAX_POSITION_PCT", "0.15")),
        "daily_loss_limit":   float(os.environ.get("DAILY_LOSS_LIMIT", "0.03")),
        "cash_reserve_pct":   float(os.environ.get("CASH_RESERVE_PCT", "0.20")),
        "mode":               os.environ.get("TRADE_MODE", "analyze"),
    }


def main():
    mode = os.environ.get("RUN_MODE", "trade")

    logger.info("=" * 60)
    logger.info(f"TITAN TRADER [{mode.upper()}] — "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    # Market holiday check — exit cleanly if market is closed
    if not assert_trading_day(mode):
        sys.exit(0)

    config = build_config()

    missing = [k for k in ["alpaca_api_key", "alpaca_secret_key", "anthropic_api_key"]
               if not config[k]]
    if missing:
        logger.error(f"Missing required env vars: {missing}")
        sys.exit(1)

    notifier = Notifier(config)

    try:
        if mode == "pre_market":
            run_pre_market(config, notifier)
        elif mode == "post_market":
            run_post_market(config, notifier)
        else:
            run_trading_session(config, notifier)

    except Exception as e:
        logger.error(f"FATAL: {e}", exc_info=True)
        notifier.send_alert(f"Titan Trader FATAL [{mode}]: {e}")
        sys.exit(1)


def run_pre_market(config: Dict, notifier):
    """8:00 AM ET — News scan + morning SMS brief."""
    logger.info("PRE-MARKET SCAN starting...")

    scanner = NewsScanner(config)
    # Use FIXED_UNIVERSE from engine class
    universe= TitanEngine.FIXED_UNIVERSE
    report  = scanner.run_full_scan(universe)

    with open("/tmp/pre_market_news.json", "w") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Sentiment: {report['market_sentiment'].get('sentiment_label')} | "
                f"Bias: {report['market_sentiment'].get('trading_bias')}")

    notifier.send_pre_market_email(report)
    notifier.send_morning_sms(report)


def run_trading_session(config: Dict, notifier):
    """9:35 AM ET — Score + allocate + review + execute."""

    # Load pre-market news
    news_data = {}
    try:
        with open("/tmp/pre_market_news.json", "r") as f:
            pre_market = json.load(f)
            news_data  = pre_market.get("stock_sentiments", {})
            logger.info(f"Pre-market sentiment loaded for {len(news_data)} stocks")
    except FileNotFoundError:
        logger.info("No pre-market file — running fresh scan...")
        scanner   = NewsScanner(config)
        fresh     = scanner.run_full_scan(TitanEngine.FIXED_UNIVERSE)
        news_data = fresh.get("stock_sentiments", {})

    # Load lessons
    learner        = LossLearningEngine(config)
    lesson_summary = learner.get_lesson_summary()
    if lesson_summary["total_losses"] > 0:
        logger.info(f"Applying {lesson_summary['total_losses']} prior lessons to weights")

    # Load previous scores for review trigger detection
    previous_scores = _load_previous_scores(config)

    engine = TitanEngine(
        config,
        news_sentiments=news_data,
        loss_learner=learner,
        previous_scores=previous_scores,
    )
    report = engine.run()

    # Save today's scores for tomorrow's review triggers
    # Use all_scored_detail only — top_10_stocks is already a subset of it
    _save_current_scores(report.get("all_scored_detail", []))

    # Send notifications
    notifier.send_daily_report(report)
    notifier.send_trade_sms(
        report.get("trade_plan", {}),
        report.get("account", {}),
    )

    with open("/tmp/trading_session.json", "w") as f:
        json.dump(report, f, indent=2, default=str)


def run_post_market(config: Dict, notifier):
    """5:00 PM ET — Performance review + news scan + loss analysis."""
    logger.info("POST-MARKET REVIEW starting...")

    from core.executor import TradeExecutor
    from data.validator import DataValidator

    executor = TradeExecutor(config)
    learner  = LossLearningEngine(config)
    tracker  = PerformanceTracker(config)
    scanner  = NewsScanner(config)

    account   = executor.get_account()
    positions = executor.get_positions()

    logger.info(f"Portfolio: ${float(account['portfolio_value']):,.2f} | "
                f"P&L: ${account['pnl_today']:+,.2f} ({account['pnl_today_pct']:+.2f}%)")

    # Post-market news scan
    news_report = scanner.run_full_scan(TitanEngine.FIXED_UNIVERSE)
    with open("/tmp/post_market_news.json", "w") as f:
        json.dump(news_report, f, indent=2)

    # Analyze losing trades — real P&L from Alpaca
    try:
        market_context = _get_market_context()
        filled_orders  = executor.get_filled_orders(limit=50)
        lessons_today  = _analyze_closed_losses(
            orders=filled_orders,
            positions_before=positions,
            learner=learner,
            tracker=tracker,
            market_context=market_context,
            news_report=news_report,
            executor=executor,
        )
        if lessons_today:
            logger.info(f"Loss lessons recorded: {lessons_today}")
        else:
            logger.info("No losing trades today ✓")
    except Exception as e:
        logger.warning(f"Post-market loss analysis error: {e}", exc_info=True)

    lesson_summary = learner.get_lesson_summary()
    perf_summary   = tracker.get_performance_summary()

    notifier.send_post_market_email(account, news_report, lesson_summary, perf_summary)
    notifier.send_post_market_sms(account, lesson_summary.get("total_losses", 0))


def _analyze_closed_losses(
    orders,
    positions_before,
    learner,
    tracker,
    market_context: Dict,
    news_report: Dict,
    executor,
) -> int:
    """
    Find genuinely losing closed trades and run autopsy.
    Uses REAL P&L from Alpaca filled prices — no estimates.
    Detects ALL exit types: stop-loss, score-degraded sells, watchdog exits.
    """
    from data.validator import DataValidator

    count = 0

    # All sell-side filled orders
    sell_orders = [
        o for o in orders
        if o.get("side") == "sell" and o.get("status") == "filled"
    ]

    if not sell_orders:
        return 0

    # Fetch ALL filled orders once — not inside the loop
    all_filled = executor.get_filled_orders(limit=200)
    all_buy_orders = [
        o for o in all_filled
        if o.get("side") == "buy" and o.get("status") == "filled"
    ]

    for order in sell_orders:
        ticker    = order.get("symbol")
        exit_px   = float(order.get("filled_avg_price") or 0)
        filled_at = order.get("filled_at", "")[:10]

        if not ticker or not exit_px:
            continue

        # Find matching buy order from the pre-fetched list
        ticker_buys = [
            o for o in all_buy_orders
            if o.get("symbol") == ticker
        ]

        if not ticker_buys:
            continue

        # Most recent buy before this sell
        entry_order = ticker_buys[0]
        entry_px    = float(entry_order.get("filled_avg_price") or 0)
        qty         = float(order.get("filled_qty") or 0)

        if not entry_px or not qty:
            continue

        # Real P&L — validated, no estimates
        pnl_data = DataValidator.validate_trade_pnl(entry_px, exit_px, qty)
        if not pnl_data["valid"]:
            continue

        # Only analyze actual losses
        if pnl_data["pnl"] >= 0:
            continue

        # Log exit to Supabase
        tracker.log_exit(
            ticker=ticker,
            exit_price=exit_px,
            exit_reason=_classify_exit_reason(order),
        )

        # Run loss autopsy
        stock_news = news_report.get("stock_sentiments", {}).get(ticker, {})
        trade = {
            "ticker":      ticker,
            "exit_date":   filled_at,
            "exit_reason": _classify_exit_reason(order),
            "pnl":         pnl_data["pnl"],         # REAL
            "pnl_pct":     pnl_data["pnl_pct"],     # REAL
            "entry_score": 0,    # Would need to pull from Supabase trades table
            "hold_days":   0,    # Computed inside tracker.log_exit
            "vix_at_entry":market_context.get("vix", 20),
            "signals_at_entry": {},
        }

        lesson = learner.analyze_loss(trade, market_context, stock_news.get("headlines", []))

        # Backup lesson to Supabase
        tracker.log_lesson(lesson)

        count += 1

    return count


def _classify_exit_reason(order: Dict) -> str:
    """Determine why a sell order was placed."""
    order_type = order.get("type", "")
    order_class= order.get("order_class", "")
    client_id  = order.get("client_order_id", "")

    if order_type in ("stop", "stop_limit"):
        return "stop_loss_triggered"
    if order_type == "limit" and order_class == "bracket":
        return "take_profit_triggered"
    if order_type == "trailing_stop":
        return "trailing_stop_triggered"
    if "watchdog" in client_id.lower():
        return "watchdog_exit"
    if "review" in client_id.lower():
        return "claude_review_exit"
    return "manual_or_score_exit"


def _get_market_context() -> Dict:
    import yfinance as yf
    try:
        spy   = yf.Ticker("SPY").info
        vix   = yf.Ticker("^VIX").info
        price = float(spy.get("regularMarketPrice") or 0)
        ma50  = float(spy.get("fiftyDayAverage") or price)
        v     = float(vix.get("regularMarketPrice") or 20)
        return {
            "regime":  "BULL" if price > ma50 else "BEAR",
            "risk_env":"HIGH_FEAR" if v > 30 else ("ELEVATED" if v > 20 else "LOW_FEAR"),
            "vix":     v,
        }
    except Exception:
        return {"regime": "UNKNOWN", "risk_env": "ELEVATED", "vix": 20}


def _load_previous_scores(config: Dict) -> Dict:
    """Load YESTERDAY's scores from Supabase for review trigger detection.
    Explicitly filters to date < today to avoid loading today's own scores
    if the session runs more than once (retry / manual trigger).
    """
    try:
        import requests
        from datetime import date
        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_key = os.environ.get("SUPABASE_KEY", "")
        if not supabase_url:
            return {}
        today   = date.today().isoformat()
        headers = {
            "apikey":        supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }
        resp = requests.get(
            f"{supabase_url}/rest/v1/daily_scores"
            f"?date=lt.{today}&order=date.desc&limit=200",
            headers=headers,
            timeout=10,
        )
        if resp.ok:
            rows = resp.json()
            # Keep only the most recent score per ticker
            seen: Dict[str, Dict] = {}
            for r in rows:
                t = r["ticker"]
                if t not in seen:
                    seen[t] = r
            logger.info(f"Loaded previous scores for {len(seen)} tickers from Supabase")
            return seen
    except Exception as e:
        logger.debug(f"Could not load previous scores: {e}")
    return {}


def _save_current_scores(scored_stocks: list):
    """Save today's scores to Supabase for tomorrow's review triggers."""
    try:
        import requests
        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_key = os.environ.get("SUPABASE_KEY", "")
        if not supabase_url or not scored_stocks:
            return
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        records = [
            {
                "ticker":      s["ticker"],
                "date":        datetime.now(timezone.utc).date().isoformat(),
                "total_score": s.get("total_score", 0),
                "signal":      s.get("signal", ""),
                "components":  json.dumps(s.get("components", {})),
                "ai_reasoning":s.get("ai_reasoning", "")[:200],
            }
            for s in scored_stocks if s.get("ticker")
        ]
        requests.post(
            f"{supabase_url}/rest/v1/daily_scores",
            headers=headers,
            json=records,
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"Could not save scores: {e}")


if __name__ == "__main__":
    main()
