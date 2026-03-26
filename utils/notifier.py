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
        pnl        = float(account.get("pnl_today", 0))
        pnl_color  = "#22c55e" if pnl >= 0 else "#ef4444"

        sig_colors = {"STRONG_BUY":"#22c55e","BUY":"#86efac","HOLD":"#fbbf24","WATCH":"#f97316","AVOID":"#ef4444"}

        buy_rows = ""
        for b in plan.get("buys", []):
            buy_rows += f"""<tr>
                <td style="padding:8px;color:#22c55e;font-weight:bold">BUY</td>
                <td style="padding:8px;font-weight:bold">{b['ticker']}</td>
                <td style="padding:8px">${b['dollars']:,.0f} ({b.get('pct',0):.1f}%)</td>
                <td style="padding:8px;font-size:11px">{b.get('tier','?')} | SL:-{b['stop_loss_pct']*100:.0f}% TP:+{b['take_profit_pct']*100:.0f}%</td>
                <td style="padding:8px;font-size:11px;color:#64748b">{b.get('reasoning','')[:60]}...</td>
            </tr>"""

        sell_rows = ""
        for s in plan.get("sells", []):
            sell_rows += f"""<tr>
                <td style="padding:8px;color:#ef4444;font-weight:bold">SELL</td>
                <td style="padding:8px;font-weight:bold">{s['ticker']}</td>
                <td colspan="3" style="padding:8px;color:#64748b;font-size:12px">{s.get('reason','')}</td>
            </tr>"""

        stock_rows = ""
        for s in top_stocks[:10]:
            color = sig_colors.get(s.get("signal","HOLD"), "#fff")
            stock_rows += f"""<tr style="border-bottom:1px solid #1e293b">
                <td style="padding:6px 10px;font-weight:bold">{s.get('ticker')}</td>
                <td style="padding:6px 10px;color:#f59e0b">{s.get('total_score',0):.1f}</td>
                <td style="padding:6px 10px;color:{color};font-size:11px;font-weight:bold">{s.get('signal','?')}</td>
                <td style="padding:6px 10px;font-size:11px;color:#64748b">{s.get('ai_reasoning','')[:70]}...</td>
            </tr>"""

        return f"""<html><body style="font-family:monospace;background:#060d1a;color:#e2e8f0;padding:24px;max-width:700px">
        <h1 style="color:#f59e0b;margin:0 0 4px">⚡ TITAN TRADER — DAILY REPORT</h1>
        <p style="color:#475569;margin:0 0 20px">{datetime.now().strftime('%A, %B %d, %Y')}</p>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px;display:flex;gap:32px">
            <div><div style="font-size:11px;color:#64748b">PORTFOLIO</div><div style="font-size:22px;font-weight:bold">${float(account.get('portfolio_value',0)):,.2f}</div></div>
            <div><div style="font-size:11px;color:#64748b">TODAY P&L</div><div style="font-size:22px;font-weight:bold;color:{pnl_color}">${pnl:+,.2f}</div></div>
            <div><div style="font-size:11px;color:#64748b">CASH</div><div style="font-size:22px">${float(account.get('cash',0)):,.2f}</div></div>
            <div><div style="font-size:11px;color:#64748b">REGIME</div><div style="font-size:16px;font-weight:bold;color:#f59e0b">{market.get('regime','?')}</div></div>
        </div>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px;margin-bottom:16px">
            <div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:10px">TRADES EXECUTED</div>
            <table style="width:100%;border-collapse:collapse">{buy_rows}{sell_rows}</table>
            {'<p style="color:#475569;font-size:13px">No trades today — no qualifying signals.</p>' if not buy_rows and not sell_rows else ''}
        </div>

        <div style="background:#0f1a2e;border-radius:8px;padding:16px">
            <div style="font-size:11px;color:#64748b;letter-spacing:2px;margin-bottom:10px">TOP SCORED STOCKS</div>
            <table style="width:100%;border-collapse:collapse">{stock_rows}</table>
        </div>
        </body></html>"""

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
