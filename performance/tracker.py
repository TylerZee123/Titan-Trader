"""
PerformanceTracker — Fixed Version
=====================================
Fixes from audit:
  - Google Sheets uses service account OAuth (not API key) for write operations
  - Lessons backed up to Supabase on every save (not just local JSON)
  - validate_performance_metrics called before displaying any metrics
  - Real P&L from DataValidator.validate_trade_pnl only
"""

import logging
import json
import os
import statistics
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional
import yfinance as yf

from data.validator import DataValidator

logger = logging.getLogger("titan_trader")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SHEETS_ID    = os.environ.get("GOOGLE_SHEETS_ID", "")

MIN_TRADES_FOR_METRICS = 10


class PerformanceTracker:

    def __init__(self, config: Dict):
        self.config  = config
        self._sb_headers = {
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation",
        }
        self._sheets_service = None   # lazy init

    # ── Trade Logging ──────────────────────────────────────────────────────

    def log_entry(self, trade: Dict, signals: Dict, market_context: Dict) -> Optional[str]:
        """Log trade entry to Supabase. Returns record id."""
        if not SUPABASE_URL:
            logger.debug("Supabase not configured — skipping trade log")
            return None

        record = {
            "ticker":           trade["ticker"],
            "entry_date":       datetime.now(timezone.utc).isoformat(),
            "entry_price":      trade.get("price", 0),
            "quantity":         trade.get("qty", 0),
            "dollars_invested": trade.get("dollars", 0),
            "allocation_pct":   trade.get("pct", 0),
            "tier":             trade.get("tier", "UNKNOWN"),
            "bucket":           trade.get("bucket", "UNKNOWN"),
            "strategy":         trade.get("strategy", "UNKNOWN"),
            "entry_score":      trade.get("score", 0),
            "signal":           trade.get("signal", "BUY"),
            "stop_loss_pct":    trade.get("stop_loss_pct", 0.07),
            "take_profit_pct":  trade.get("take_profit_pct"),
            "trail_pct":        trade.get("trail_pct"),
            "stop_price":       trade.get("stop_price", 0),
            "target_price":     trade.get("target_price"),
            "ai_reasoning":     trade.get("reasoning", "")[:500],
            "market_regime":    market_context.get("regime", ""),
            "vix_at_entry":     market_context.get("vix", 0),
            "sig_technical":    signals.get("technical", 0),
            "sig_fundamental":  signals.get("fundamental", 0),
            "sig_moat":         signals.get("moat", 0),
            "sig_sentiment":    signals.get("sentiment", 0),
            "sig_growth":       signals.get("growth", 0),
            "sig_management":   signals.get("management", 0),
            "sig_ai_analysis":  signals.get("ai_analysis", 0),
            "status":           "OPEN",
        }

        try:
            resp = requests.post(
                f"{SUPABASE_URL}/rest/v1/trades",
                headers=self._sb_headers,
                json=record,
                timeout=10,
            )
            if resp.ok and resp.json():
                trade_id = resp.json()[0].get("id")
                logger.info(f"  Trade entry logged: {trade['ticker']} (id={trade_id})")
                return str(trade_id)
            else:
                logger.error(f"  Supabase entry failed: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            logger.error(f"  Trade log error: {e}")
        return None

    def log_exit(
        self,
        ticker: str,
        exit_price: float,
        exit_reason: str,
    ):
        """
        Update open trade with real exit data.
        P&L calculated from actual filled prices via DataValidator.
        """
        if not SUPABASE_URL:
            return

        try:
            resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/trades"
                f"?ticker=eq.{ticker}&status=eq.OPEN&order=entry_date.desc&limit=1",
                headers=self._sb_headers,
                timeout=10,
            )
            if not resp.ok or not resp.json():
                logger.warning(f"  No open trade found for {ticker}")
                return

            trade      = resp.json()[0]
            trade_id   = trade["id"]
            entry_px   = float(trade.get("entry_price") or 0)
            qty        = float(trade.get("quantity") or 0)
            entry_dt   = datetime.fromisoformat(
                trade["entry_date"].replace("Z", "+00:00")
            )
            hold_days  = (datetime.now(timezone.utc) - entry_dt).days

            # Real P&L — no estimates
            pnl_data = DataValidator.validate_trade_pnl(entry_px, exit_price, qty)

            if not pnl_data["valid"]:
                logger.error(
                    f"  Cannot compute P&L for {ticker}: {pnl_data.get('error')}"
                )
                return

            update = {
                "status":      "CLOSED",
                "exit_date":   datetime.now(timezone.utc).isoformat(),
                "exit_price":  exit_price,
                "exit_reason": exit_reason,
                "pnl":         pnl_data["pnl"],
                "pnl_pct":     pnl_data["pnl_pct"],
                "hold_days":   hold_days,
                "won":         pnl_data["pnl"] > 0,
            }

            requests.patch(
                f"{SUPABASE_URL}/rest/v1/trades?id=eq.{trade_id}",
                headers=self._sb_headers,
                json=update,
                timeout=10,
            )
            logger.info(
                f"  Trade exit logged: {ticker} "
                f"P&L ${pnl_data['pnl']:+,.2f} "
                f"({pnl_data['pnl_pct']:+.1f}%) "
                f"after {hold_days}d"
            )

        except Exception as e:
            logger.error(f"  Trade exit log error {ticker}: {e}")

    def log_lesson(self, lesson: Dict):
        """Backup loss lesson to Supabase — not just local JSON."""
        if not SUPABASE_URL:
            return
        try:
            record = {
                "ticker":       lesson.get("ticker"),
                "pnl":          lesson.get("pnl"),
                "pnl_pct":      lesson.get("pnl_pct"),
                "failure_mode": lesson.get("autopsy", {}).get("failure_mode"),
                "lesson":       lesson.get("autopsy", {}).get("lesson", "")[:500],
                "rule_added":   lesson.get("autopsy", {}).get("rule_to_add", "")[:200],
                "severity":     lesson.get("autopsy", {}).get("severity"),
            }
            requests.post(
                f"{SUPABASE_URL}/rest/v1/lessons",
                headers=self._sb_headers,
                json=record,
                timeout=10,
            )
        except Exception as e:
            logger.error(f"  Lesson backup error: {e}")

    def log_daily_snapshot(self, account: Dict, market_context: Dict):
        """Log daily portfolio snapshot for benchmark comparison."""
        if not SUPABASE_URL:
            return
        try:
            spy_price = self._get_spy_price()
            record = {
                "date":            datetime.now(timezone.utc).date().isoformat(),
                "portfolio_value": float(account.get("portfolio_value", 0)),
                "cash":            float(account.get("cash", 0)),
                "pnl_today":       float(account.get("pnl_today", 0)),
                "pnl_today_pct":   float(account.get("pnl_today_pct", 0)),
                "spy_price":       spy_price,
                "vix":             market_context.get("vix", 0),
                "regime":          market_context.get("regime", ""),
                "open_positions":  account.get("open_positions", 0),
            }
            requests.post(
                f"{SUPABASE_URL}/rest/v1/daily_snapshots",
                headers=self._sb_headers,
                json=record,
                timeout=10,
            )
        except Exception as e:
            logger.error(f"  Snapshot log error: {e}")

    # ── Performance Metrics ────────────────────────────────────────────────

    def get_performance_summary(self) -> Dict:
        """Compute all metrics from Supabase trade history."""
        if not SUPABASE_URL:
            return DataValidator.validate_performance_metrics({"total_trades": 0})

        try:
            resp   = requests.get(
                f"{SUPABASE_URL}/rest/v1/trades?status=eq.CLOSED&order=exit_date.desc",
                headers=self._sb_headers,
                timeout=10,
            )
            trades = resp.json() if resp.ok else []

            if not trades:
                return DataValidator.validate_performance_metrics({"total_trades": 0})

            pnls      = [float(t.get("pnl") or 0) for t in trades]
            pnl_pcts  = [float(t.get("pnl_pct") or 0) for t in trades]
            hold_days = [int(t.get("hold_days") or 0) for t in trades]
            winners   = [p for p in pnls if p > 0]
            losers    = [p for p in pnls if p <= 0]

            win_rate     = len(winners) / len(pnls) if pnls else 0
            avg_win      = sum(winners) / len(winners) if winners else 0
            avg_loss     = sum(losers) / len(losers) if losers else 0
            profit_factor= abs(sum(winners) / sum(losers)) if losers and sum(losers) != 0 else 0

            # Benchmark + drawdown from daily snapshots
            snap_resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/daily_snapshots?order=date.asc",
                headers=self._sb_headers,
                timeout=10,
            )
            snapshots = snap_resp.json() if snap_resp.ok else []

            total_return_pct = 0
            vs_benchmark     = 0
            max_drawdown     = 0
            sharpe           = 0

            if len(snapshots) >= 2:
                first_val   = float(snapshots[0].get("portfolio_value") or 5000)
                last_val    = float(snapshots[-1].get("portfolio_value") or 5000)
                total_return_pct = ((last_val / first_val) - 1) * 100

                first_spy = float(snapshots[0].get("spy_price") or 1)
                last_spy  = float(snapshots[-1].get("spy_price") or 1)
                spy_ret   = ((last_spy / first_spy) - 1) * 100
                vs_benchmark = total_return_pct - spy_ret

                # Drawdown
                values   = [float(s.get("portfolio_value") or 0) for s in snapshots]
                peak     = values[0]
                for v in values:
                    if v > peak: peak = v
                    dd = (peak - v) / peak * 100 if peak > 0 else 0
                    if dd > max_drawdown: max_drawdown = dd

                # Sharpe
                daily_rets = [float(s.get("pnl_today_pct") or 0) / 100 for s in snapshots]
                if len(daily_rets) > 1:
                    avg_ret = statistics.mean(daily_rets)
                    std_ret = statistics.stdev(daily_rets) if len(daily_rets) > 1 else 0.001
                    sharpe  = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0

            raw = {
                "total_trades":     len(trades),
                "total_return_pct": round(total_return_pct, 2),
                "vs_benchmark":     round(vs_benchmark, 2),
                "win_rate":         round(win_rate, 3),
                "avg_win":          round(avg_win, 2),
                "avg_loss":         round(avg_loss, 2),
                "profit_factor":    round(profit_factor, 2),
                "sharpe":           round(sharpe, 2),
                "max_drawdown":     round(max_drawdown, 2),
                "avg_hold_days":    round(sum(hold_days) / len(hold_days), 1) if hold_days else 0,
                "total_pnl":        round(sum(pnls), 2),
                "best_trade":       round(max(pnl_pcts), 1) if pnl_pcts else 0,
                "worst_trade":      round(min(pnl_pcts), 1) if pnl_pcts else 0,
                "beating_market":   vs_benchmark > 0,
            }

            # Suppress metrics until sufficient history exists
            return DataValidator.validate_performance_metrics(raw)

        except Exception as e:
            logger.error(f"  Performance summary error: {e}")
            return DataValidator.validate_performance_metrics({"total_trades": 0})

    # ── Google Sheets (OAuth service account) ─────────────────────────────

    def sync_to_sheets(self, perf: Dict, trades_today: List[Dict]):
        """
        Push data to Google Sheets using service account OAuth.
        Requires GOOGLE_SERVICE_ACCOUNT_JSON env var (full JSON string).
        """
        if not SHEETS_ID:
            logger.debug("Google Sheets not configured — skipping sync")
            return

        service = self._get_sheets_service()
        if not service:
            return

        try:
            self._update_scorecard(service, perf)
            if trades_today:
                self._append_trades(service, trades_today)
            logger.info("  Google Sheets synced")
        except Exception as e:
            logger.error(f"  Sheets sync error: {e}")

    def _get_sheets_service(self):
        """Get authenticated Google Sheets service using service account."""
        if self._sheets_service:
            return self._sheets_service

        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sa_json:
            logger.debug("GOOGLE_SERVICE_ACCOUNT_JSON not set — skipping Sheets")
            return None

        try:
            import json as json_lib
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds_dict = json_lib.loads(sa_json)
            scopes     = ["https://www.googleapis.com/auth/spreadsheets"]
            creds      = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=scopes
            )
            self._sheets_service = build("sheets", "v4", credentials=creds)
            return self._sheets_service
        except Exception as e:
            logger.error(f"  Sheets auth error: {e}")
            return None

    def _update_scorecard(self, service, perf: Dict):
        """Write performance metrics to Scorecard tab."""
        status = perf.get("status", "")
        if status == "INSUFFICIENT_DATA":
            values = [
                ["TITAN TRADER SCORECARD", datetime.now().strftime("%Y-%m-%d %H:%M")],
                [""],
                [perf.get("message", "Insufficient data for metrics")],
                ["Total P&L so far", f"${perf.get('total_pnl', 0):+,.2f}"],
                ["Trades completed", str(perf.get("total_trades", 0))],
                [f"Need {MIN_TRADES_FOR_METRICS} trades for full metrics", ""],
            ]
        else:
            bm = perf.get("beating_market")
            values = [
                ["TITAN TRADER SCORECARD", datetime.now().strftime("%Y-%m-%d %H:%M")],
                [""],
                ["METRIC", "VALUE", "TARGET"],
                ["Total Return",    f"{perf.get('total_return_pct',0):+.2f}%",   ">10%/yr"],
                ["vs S&P 500",      f"{perf.get('vs_benchmark',0):+.2f}%",        ">0%"],
                ["Win Rate",        f"{perf.get('win_rate',0)*100:.1f}%",          ">55%"],
                ["Profit Factor",   f"{perf.get('profit_factor',0):.2f}x",         ">1.5x"],
                ["Sharpe Ratio",    f"{perf.get('sharpe',0):.2f}",                 ">1.0"],
                ["Max Drawdown",    f"-{perf.get('max_drawdown',0):.1f}%",          "<15%"],
                ["Avg Hold Days",   f"{perf.get('avg_hold_days',0):.1f}",           "varies"],
                ["Total Trades",    str(perf.get("total_trades",0)),                ""],
                ["Total P&L",       f"${perf.get('total_pnl',0):+,.2f}",           ""],
                ["Beating Market?", "YES ✓" if bm else "NO ✗",                    ""],
            ]

        service.spreadsheets().values().update(
            spreadsheetId=SHEETS_ID,
            range="Scorecard!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    def _append_trades(self, service, trades: List[Dict]):
        """Append today's trades to Trades tab."""
        rows = []
        for t in trades:
            rows.append([
                datetime.now().strftime("%Y-%m-%d"),
                t.get("ticker", ""),
                "BUY",
                f"${t.get('dollars', 0):,.2f}",
                t.get("tier", ""),
                t.get("bucket", ""),
                f"{t.get('score', 0):.1f}",
                t.get("signal", ""),
                t.get("reasoning", "")[:100],
            ])
        service.spreadsheets().values().append(
            spreadsheetId=SHEETS_ID,
            range="Trades!A:I",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

    def _get_spy_price(self) -> float:
        try:
            return float(yf.Ticker("SPY").info.get("regularMarketPrice") or 0)
        except Exception:
            return 0.0
