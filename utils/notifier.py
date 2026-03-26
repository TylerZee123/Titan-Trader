"""
Notifier — SMS via Twilio + Email Reports
===========================================
SMS: Daily morning briefing to 516.784.0478
Email: Full HTML report to tylerzar24@gmail.com

SMS is intentionally brief — what you need in 10 seconds:
  - Portfolio value + today's P&L
  - What the bot is buying/selling today
  - Market sentiment + any urgent alerts

Email has the full breakdown.
"""

import logging
import os
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger("titan_trader")

PHONE_NUMBER  = "516.784.0478"   # stored here as reference; actual value from env
TO_EMAIL      = "tylerzar24@gmail.com"


class Notifier:

    def __init__(self, config: Dict):
        self.config       = config
        self.to_email     = config.get("notification_email", TO_EMAIL)
        self.to_phone     = os.environ.get("NOTIFICATION_PHONE", "+15167840478")
        self.from_email   = os.environ.get("SMTP_FROM_EMAIL", self.to_email)
        self.smtp_host    = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port    = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user    = os.environ.get("SMTP_USER")
        self.smtp_pass    = os.environ.get("SMTP_PASS")
        # Twilio
        self.twilio_sid   = os.environ.get("TWILIO_ACCOUNT_SID")
        self.twilio_token = os.environ.get("TWILIO_AUTH_TOKEN")
        self.twilio_from  = os.environ.get("TWILIO_FROM_NUMBER")

    # ── SMS ────────────────────────────────────────────────────────────────

    def sms(self, message: str):
        """Route all alerts to email instead of SMS."""
        subject = message.split("\n")[0][:80]
        body    = f"<html><body style='font-family:monospace;background:#060d1a;color:#e2e8f0;padding:24px'><pre style='color:#e2e8f0'>{message}</pre></body></html>"
        self._send_email(f"⚡ Titan Alert — {subject}", body)

    def send_morning_sms(self, report: Dict):
        """Morning brief — sent as email."""
        ms   = report.get("market_sentiment", {})
        buys = report.get("strong_buy_signals", [])
        sells= report.get("sell_signals", [])
        bias = ms.get("trading_bias", "NEUTRAL")
        sent = ms.get("sentiment_label", "NEUTRAL")

        emoji_map = {
            "VERY_BULLISH": "🚀", "BULLISH": "📈",
            "NEUTRAL": "➡️", "BEARISH": "📉", "VERY_BEARISH": "🔴",
        }
        emoji = emoji_map.get(sent, "📊")

        lines = [
            f"⚡ TITAN — {datetime.now().strftime('%a %b %d')}",
            f"{emoji} Market: {sent} | Bias: {bias}",
        ]
        if buys:
            lines.append(f"📈 Watching: {', '.join(buys[:4])}")
        if sells:
            lines.append(f"🚨 Exit signals: {', '.join(sells[:3])}")
        lines.append("Full report coming at 9:35 AM.")

        subject = f"🌅 Titan Pre-Market — {datetime.now().strftime('%b %d')} | {sent}"
        body    = f"<html><body style='font-family:monospace;background:#060d1a;color:#e2e8f0;padding:24px'><pre style='color:#e2e8f0'>{chr(10).join(lines)}</pre></body></html>"
        self._send_email(subject, body)

    def send_trade_sms(self, trade_plan: Dict, account: Dict):
        """Trade confirmation — sent as email."""
        buys = trade_plan.get("buys", [])
        sells= trade_plan.get("sells", [])
        pv   = float(account.get("portfolio_value", 0))

        lines = [f"⚡ TITAN TRADED — {datetime.now().strftime('%H:%M ET')}"]
        for b in buys[:4]:
            tp = b.get('take_profit_pct') or 0
            lines.append(
                f"✅ BUY {b['ticker']} ${b['dollars']:,.0f} "
                f"| SL:-{b['stop_loss_pct']*100:.0f}% TP:+{tp*100:.0f}%"
            )
        for s in sells[:3]:
            lines.append(f"🔴 SELL {s['ticker']} — {s.get('reason','')[:60]}")
        if not buys and not sells:
            lines.append("No trades today — no qualifying signals.")
        lines.append(f"Portfolio: ${pv:,.0f}")

        subject = f"⚡ Titan Trades — {datetime.now().strftime('%b %d %H:%M')} | {len(buys)} buys {len(sells)} sells"
        body    = f"<html><body style='font-family:monospace;background:#060d1a;color:#e2e8f0;padding:24px'><pre style='color:#e2e8f0'>{chr(10).join(lines)}</pre></body></html>"
        self._send_email(subject, body)

    def send_alert_sms(self, message: str):
        """Urgent alert — sent as email."""
        self.sms(f"🚨 TITAN ALERT\n{message}")

    def send_post_market_sms(self, account: Dict, lessons_count: int):
        """End of day summary — sent as email."""
        pnl     = float(account.get("pnl_today", 0))
        pnl_pct = float(account.get("pnl_today_pct", 0))
        pv      = float(account.get("portfolio_value", 0))
        emoji   = "📈" if pnl >= 0 else "📉"

        lines = [
            f"{emoji} TITAN EOD — {datetime.now().strftime('%b %d')}",
            f"P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)",
            f"Portfolio: ${pv:,.2f}",
        ]
        if lessons_count > 0:
            lines.append(f"📚 {lessons_count} loss lesson(s) recorded.")
        lines.append("Full post-market report coming shortly.")

        subject = f"{emoji} Titan EOD — {datetime.now().strftime('%b %d')} | ${pnl:+,.2f}"
        body    = f"<html><body style='font-family:monospace;background:#060d1a;color:#e2e8f0;padding:24px'><pre style='color:#e2e8f0'>{chr(10).join(lines)}</pre></body></html>"
        self._send_email(subject, body)

    # ── Email ──────────────────────────────────────────────────────────────

    def send_pre_market_email(self, report: Dict):
        ms      = report.get("market_sentiment", {})
        subject = (
            f"🌅 Titan Pre-Market — {datetime.now().strftime('%b %d')} | "
            f"{ms.get('sentiment_label','?')} | {ms.get('trading_bias','?')}"
        )
        body = self._build_pre_market_html(report)
        self._send_email(subject, body)

    def send_daily_report(self, report: Dict):
        account = report.get("account", {})
        plan    = report.get("trade_plan", {})
        pnl     = float(account.get("pnl_today", 0))
        buys    = len(plan.get("buys", []))
        sells   = len(plan.get("sells", []))
        emoji   = "📈" if pnl >= 0 else "📉"

        subject = (
            f"{emoji} Titan Trader — {datetime.now().strftime('%b %d')} | "
            f"{buys} buys, {sells} sells | P&L: ${pnl:+,.2f}"
        )
        body = self._build_daily_html(report)
        self._send_email(subject, body)

    def send_post_market_email(self, account: Dict, news_report: Dict, lesson_summary: Dict, perf_summary: Dict):
        pnl   = float(account.get("pnl_today", 0))
        emoji = "📈" if pnl >= 0 else "📉"
        subject = (
            f"{emoji} Titan Post-Market — {datetime.now().strftime('%b %d')} | "
            f"P&L: ${pnl:+,.2f}"
        )
        body = self._build_post_market_html(account, news_report, lesson_summary, perf_summary)
        self._send_email(subject, body)

    def send_alert(self, message: str):
        self._send_email(f"🚨 TITAN ALERT — {datetime.now().strftime('%b %d %H:%M')}", message)
        self.send_alert_sms(message)

    # ── HTML Builders ──────────────────────────────────────────────────────

    def _build_pre_market_html(self, report: Dict) -> str:
        ms   = report.get("market_sentiment", {})
        bias = ms.get("trading_bias", "NEUTRAL")
        bias_color = "#22c55e" if bias == "RISK_ON" else "#ef4444" if bias == "RISK_OFF" else "#fbbf24"
        buys  = ", ".join(report.get("strong_buy_signals", [])[:6]) or "None"
        sells = ", ".join(report.get("sell_signals", [])[:4]) or "None"
        reviews = ", ".join(report.get("immediate_reviews", [])[:4]) or "None"

        themes = ""
        for t in ms.get("key_themes", [])[:4]:
            themes += f"<li style='color:#94a3b8;font-size:13px;margin-bottom:4px'>{t}</li>"

        return f"""<html><body style="font-family:monospace;background:#060d1a;color:#e2e8f0;padding:24px;max-width:600px">
        <h1 style="color:#f59e0b;margin:0 0 4px">🌅 PRE-MARKET BRIEF</h1>
        <p style="color:#475569;margin:0 0 20px">{datetime.now().strftime('%A, %B %d, %Y')}</p>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px;border-left:3px solid {bias_color}">
            <div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:8px">MARKET SENTIMENT</div>
            <div style="font-size:20px;font-weight:bold;color:{bias_color}">{ms.get('sentiment_label','?')}</div>
            <div style="color:#94a3b8;font-size:13px;margin-top:4px">Trading bias: <strong style="color:{bias_color}">{bias}</strong> | Macro risk: {ms.get('macro_risk_level','?')} | VIX context noted</div>
            <p style="color:#94a3b8;font-size:13px;margin-top:10px;line-height:1.6">{ms.get('summary','')}</p>
        </div>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">
            <div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:8px">KEY THEMES</div>
            <ul style="margin:0;padding-left:16px">{themes}</ul>
        </div>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">
            <div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:8px">SIGNALS</div>
            <p style="margin:4px 0;font-size:13px">📈 <strong style="color:#22c55e">Buy signals:</strong> {buys}</p>
            <p style="margin:4px 0;font-size:13px">📉 <strong style="color:#ef4444">Sell signals:</strong> {sells}</p>
            <p style="margin:4px 0;font-size:13px">⚠️  <strong style="color:#fbbf24">Immediate review:</strong> {reviews}</p>
        </div>
        </body></html>"""

    def _build_daily_html(self, report: Dict) -> str:
        account    = report.get("account", {})
        plan       = report.get("trade_plan", {})
        allocation = plan.get("allocation", {})
        top_stocks = report.get("top_10_stocks", [])
        market     = report.get("market_context", {})
        reviews    = report.get("position_reviews", {})
        pnl        = float(account.get("pnl_today", 0))
        pnl_color  = "#22c55e" if pnl >= 0 else "#ef4444"
        regime     = market.get("regime", "?")
        regime_color = "#22c55e" if regime == "BULL" else "#ef4444" if regime == "BEAR" else "#fbbf24"
        risk_mult  = plan.get("risk_multiplier", 1.0)
        sig_colors = {"STRONG_BUY":"#22c55e","BUY":"#86efac","HOLD":"#fbbf24","WATCH":"#f97316","AVOID":"#ef4444"}

        # ── Trade rows ────────────────────────────────────────────────────
        buy_rows = ""
        for b in plan.get("buys", []):
            tp = b.get('take_profit_pct') or 0
            buy_rows += f"""<tr style="border-bottom:1px solid #1e293b">
                <td style="padding:8px;color:#22c55e;font-weight:bold">BUY</td>
                <td style="padding:8px;font-weight:bold">{b['ticker']}</td>
                <td style="padding:8px">${b['dollars']:,.0f}</td>
                <td style="padding:8px;font-size:11px;color:#fbbf24">{b.get('tier','?')} | {b.get('bucket','?')}</td>
                <td style="padding:8px;font-size:11px">SL:-{b['stop_loss_pct']*100:.0f}% {'TP:+'+str(round(tp*100))+'%' if tp else 'TRAIL:15%'}</td>
                <td style="padding:8px;font-size:11px;color:#64748b">{b.get('reasoning','')[:80]}</td>
            </tr>"""

        sell_rows = ""
        for s in plan.get("sells", []):
            sell_rows += f"""<tr style="border-bottom:1px solid #1e293b">
                <td style="padding:8px;color:#ef4444;font-weight:bold">{'TRIM' if s.get('is_trim') else 'SELL'}</td>
                <td style="padding:8px;font-weight:bold">{s['ticker']}</td>
                <td colspan="4" style="padding:8px;color:#94a3b8;font-size:12px">{s.get('reason','')}</td>
            </tr>"""

        no_trade_reason = ""
        if not buy_rows and not sell_rows:
            holds = plan.get("holds", [])
            regime_note = f"Market regime: {regime} (risk multiplier: {risk_mult:.0%}) — position sizes reduced" if risk_mult < 1.0 else ""
            scored_count = report.get("all_scored", 0)
            min_score = 60
            qualifying = [s for s in top_stocks if s.get("total_score", 0) >= min_score and s.get("signal") in ("BUY","STRONG_BUY")]
            no_trade_reason = f"""
            <p style="color:#94a3b8;font-size:13px;margin:0 0 6px">
                <strong style="color:#fbbf24">Why no trades?</strong> Scored {scored_count} stocks today.
                {len(qualifying)} met the minimum score threshold of {min_score}/100.
                {f"However, {regime_note}." if regime_note else ""}
                {f"All {len(qualifying)} qualifying stocks were either already held, blocked by earnings, or below minimum dollar size after risk adjustment." if qualifying else "No stocks scored above the 60/100 minimum threshold today."}
            </p>"""

        # ── Top 10 scored stocks ──────────────────────────────────────────
        stock_rows = ""
        for s in top_stocks[:10]:
            color = sig_colors.get(s.get("signal","HOLD"), "#fff")
            proj  = s.get("projected_return")
            proj_str = f"+{proj:.0f}%" if proj and proj > 0 else (f"{proj:.0f}%" if proj else "—")
            proj_color = "#22c55e" if proj and proj > 0 else "#ef4444" if proj and proj < 0 else "#64748b"
            risks = ", ".join(s.get("ai_risks", [])[:2]) or "—"
            cats  = ", ".join(s.get("ai_catalysts", [])[:2]) or "—"
            stock_rows += f"""
            <tr style="border-bottom:1px solid #1e293b">
                <td style="padding:8px 6px;font-weight:bold;font-size:13px">{s.get('ticker')}</td>
                <td style="padding:8px 6px;color:#f59e0b;font-weight:bold">{s.get('total_score',0):.1f}</td>
                <td style="padding:8px 6px;color:{color};font-size:11px;font-weight:bold">{s.get('signal','?')}</td>
                <td style="padding:8px 6px;color:{proj_color};font-size:12px">{proj_str}</td>
                <td style="padding:8px 6px;font-size:11px;color:#94a3b8">{s.get('ai_reasoning','')[:80]}</td>
            </tr>
            <tr style="border-bottom:2px solid #0f1a2e;background:#070e1c">
                <td colspan="2" style="padding:2px 6px 8px;font-size:10px;color:#475569">
                    {s.get('bucket','?')} | {s.get('strategy','?')} | conf:{s.get('data_confidence','?')}
                </td>
                <td colspan="3" style="padding:2px 6px 8px;font-size:10px;color:#475569">
                    ✅ {cats[:60]} &nbsp;&nbsp; ⚠️ {risks[:60]}
                </td>
            </tr>"""

        # ── Position reviews ──────────────────────────────────────────────
        review_rows = ""
        for ticker, rv in reviews.items():
            dec = rv.get("decision","HOLD")
            dec_color = "#22c55e" if dec == "HOLD" else "#fbbf24" if dec == "TRIM" else "#ef4444"
            trim_str = f" {rv['trim_pct']*100:.0f}%" if rv.get("trim_pct") else ""
            review_rows += f"""<tr style="border-bottom:1px solid #1e293b">
                <td style="padding:8px;font-weight:bold">{ticker}</td>
                <td style="padding:8px;color:{dec_color};font-weight:bold">{dec}{trim_str}</td>
                <td style="padding:8px;font-size:12px;color:#94a3b8">{rv.get('reasoning','')[:100]}</td>
            </tr>"""

        # ── Sector breakdown ──────────────────────────────────────────────
        sector_rows = ""
        for sector, data in allocation.get("sector_breakdown", {}).items():
            sector_rows += f"""<tr style="border-bottom:1px solid #1e293b">
                <td style="padding:5px 8px;font-size:12px">{sector}</td>
                <td style="padding:5px 8px;font-size:12px;color:#fbbf24">{data['count']} positions</td>
                <td style="padding:5px 8px;font-size:12px">${data['dollars']:,.0f}</td>
                <td style="padding:5px 8px;font-size:11px;color:#64748b">{', '.join(data['tickers'])}</td>
            </tr>"""

        position_reviews_label = "POSITION REVIEWS (Claude Decisions)"
        reviews_html = (
            '<div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">'
            '<div style="font-size:10px;color:#64748b;letter-spacing:2px;margin-bottom:10px">' + position_reviews_label + '</div>'
            '<table style="width:100%;border-collapse:collapse">' + review_rows + '</table></div>'
        ) if review_rows else ''

        sector_html = (
            '<div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">'
            '<div style="font-size:10px;color:#64748b;letter-spacing:2px;margin-bottom:10px">SECTOR BREAKDOWN</div>'
            '<table style="width:100%;border-collapse:collapse">' + sector_rows + '</table></div>'
        ) if sector_rows else ''

        trades_html = (
            '<table style="width:100%;border-collapse:collapse">' + buy_rows + sell_rows + '</table>'
        ) if buy_rows or sell_rows else ''

        risk_mult_color = "#22c55e" if risk_mult >= 1 else "#fbbf24"
        risk_mult_pct   = f"{risk_mult:.0%}"
        date_str        = datetime.now().strftime('%A, %B %d, %Y - %I:%M %p ET')
        portfolio_val   = f"{float(account.get('portfolio_value', 0)):,.2f}"
        cash_val        = f"{float(account.get('cash', 0)):,.2f}"
        vix_val         = str(market.get('vix', '?'))
        risk_env        = str(market.get('risk_env', '?'))
        all_scored      = str(report.get('all_scored', 0))
        dynamic_adds    = str(report.get('dynamic_adds', 0))

        return (
            '<html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,monospace;background:#060d1a;color:#e2e8f0;padding:24px;max-width:780px;margin:0 auto">'
            '<h1 style="color:#f59e0b;margin:0 0 4px;font-size:22px">TITAN TRADER - DAILY REPORT</h1>'
            f'<p style="color:#475569;margin:0 0 20px;font-size:13px">{date_str}</p>'

            '<div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:24px">'
            f'<div><div style="font-size:10px;color:#64748b">PORTFOLIO</div><div style="font-size:24px;font-weight:bold">${portfolio_val}</div></div>'
            f'<div><div style="font-size:10px;color:#64748b">TODAY P&L</div><div style="font-size:24px;font-weight:bold;color:{pnl_color}">${pnl:+,.2f}</div></div>'
            f'<div><div style="font-size:10px;color:#64748b">CASH</div><div style="font-size:24px">${cash_val}</div></div>'
            f'<div><div style="font-size:10px;color:#64748b">REGIME</div><div style="font-size:18px;font-weight:bold;color:{regime_color}">{regime}</div></div>'
            f'<div><div style="font-size:10px;color:#64748b">VIX</div><div style="font-size:18px">{vix_val}</div></div>'
            f'<div><div style="font-size:10px;color:#64748b">RISK MULT</div><div style="font-size:18px;color:{risk_mult_color}">{risk_mult_pct}</div></div>'
            '</div>'

            f'<div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px;border-left:3px solid {regime_color}">'
            '<div style="font-size:10px;color:#64748b;letter-spacing:2px;margin-bottom:8px">MARKET CONTEXT</div>'
            f'<p style="color:#94a3b8;font-size:13px;line-height:1.7;margin:0">'
            f'Regime: <strong style="color:{regime_color}">{regime}</strong> | '
            f'Risk env: <strong>{risk_env}</strong> | '
            f'Stocks scored: <strong>{all_scored}</strong> | '
            f'Dynamic candidates: <strong>{dynamic_adds}</strong><br>'
            f'Position sizes adjusted to <strong style="color:#fbbf24">{risk_mult_pct}</strong> of normal.'
            '</p></div>'

            '<div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">'
            '<div style="font-size:10px;color:#64748b;letter-spacing:2px;margin-bottom:10px">TRADES EXECUTED TODAY</div>'
            + trades_html + no_trade_reason +
            '</div>'

            + reviews_html +

            '<div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">'
            '<div style="font-size:10px;color:#64748b;letter-spacing:2px;margin-bottom:10px">TOP 10 SCORED STOCKS TODAY</div>'
            '<table style="width:100%;border-collapse:collapse">'
            '<tr style="border-bottom:1px solid #1e293b">'
            '<th style="padding:6px;text-align:left;font-size:10px;color:#475569">TICKER</th>'
            '<th style="padding:6px;text-align:left;font-size:10px;color:#475569">SCORE</th>'
            '<th style="padding:6px;text-align:left;font-size:10px;color:#475569">SIGNAL</th>'
            '<th style="padding:6px;text-align:left;font-size:10px;color:#475569">PROJ 12M</th>'
            '<th style="padding:6px;text-align:left;font-size:10px;color:#475569">AI ANALYSIS</th>'
            '</tr>'
            + stock_rows +
            '</table></div>'

            + sector_html +

            '<p style="color:#1e293b;font-size:11px;text-align:center;margin-top:24px">Titan Trader - AI-powered portfolio management</p>'
            '</body></html>'
        )

    def _build_post_market_html(self, account: Dict, news: Dict, lessons: Dict, perf: Dict) -> str:
        pnl       = float(account.get("pnl_today", 0))
        pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"
        ms        = news.get("market_sentiment", {})

        lesson_rows = ""
        for l in lessons.get("recent_lessons", [])[:3]:
            lesson_rows += f"""<tr style="border-bottom:1px solid #1e293b">
                <td style="padding:6px 10px;color:#ef4444;font-weight:bold">{l['ticker']}</td>
                <td style="padding:6px 10px;color:#ef4444">${l['pnl']:,.2f}</td>
                <td style="padding:6px 10px;font-size:12px;color:#94a3b8">{l.get('lesson','')}</td>
            </tr>"""

        perf_rows = ""
        for k, v in {
            "Total return": f"{perf.get('total_return_pct',0):+.2f}%",
            "Win rate": f"{perf.get('win_rate',0)*100:.1f}%",
            "vs S&P 500": f"{perf.get('vs_benchmark',0):+.2f}%",
            "Sharpe ratio": f"{perf.get('sharpe',0):.2f}",
            "Max drawdown": f"{perf.get('max_drawdown',0):.1f}%",
            "Avg hold days": f"{perf.get('avg_hold_days',0):.1f}",
        }.items():
            perf_rows += f"""<tr style="border-bottom:1px solid #1e293b">
                <td style="padding:6px 10px;color:#64748b;font-size:13px">{k}</td>
                <td style="padding:6px 10px;font-weight:bold;font-size:13px">{v}</td>
            </tr>"""

        return f"""<html><body style="font-family:monospace;background:#060d1a;color:#e2e8f0;padding:24px;max-width:650px">
        <h1 style="color:#f59e0b;margin:0 0 4px">🌆 POST-MARKET REPORT</h1>
        <p style="color:#475569;margin:0 0 20px">{datetime.now().strftime('%A, %B %d, %Y')}</p>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">
            <div style="display:flex;gap:32px">
                <div><div style="font-size:11px;color:#64748b">PORTFOLIO</div><div style="font-size:20px;font-weight:bold">${float(account.get('portfolio_value',0)):,.2f}</div></div>
                <div><div style="font-size:11px;color:#64748b">TODAY P&L</div><div style="font-size:20px;font-weight:bold;color:{pnl_color}">${pnl:+,.2f}</div></div>
            </div>
        </div>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">
            <div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:8px">PERFORMANCE SCORECARD</div>
            <table style="width:100%;border-collapse:collapse">{perf_rows}</table>
        </div>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">
            <div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:8px">AFTER-HOURS INTELLIGENCE</div>
            <p style="color:#94a3b8;font-size:13px;line-height:1.6;margin:0">{ms.get('summary','No post-market summary available.')}</p>
            <p style="color:#64748b;font-size:12px;margin-top:8px">Tomorrow bias: <strong style="color:#f59e0b">{ms.get('trading_bias','NEUTRAL')}</strong></p>
        </div>

        {'<div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px"><div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:8px">LOSS LESSONS TODAY</div><table style="width:100%;border-collapse:collapse">' + lesson_rows + '</table></div>' if lesson_rows else ''}
        </body></html>"""

    def _send_email(self, subject: str, body: str):
        if not all([self.smtp_user, self.smtp_pass]):
            logger.info(f"Email not configured — would send: {subject}")
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = self.from_email
            msg["To"]      = self.to_email
            msg.attach(MIMEText(body, "html"))
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as s:
                s.starttls()
                s.login(self.smtp_user, self.smtp_pass)
                s.sendmail(self.from_email, self.to_email, msg.as_string())
            logger.info(f"Email sent: {subject[:60]}")
        except Exception as e:
            logger.error(f"Email failed: {e}")
