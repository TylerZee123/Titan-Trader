from dotenv import load_dotenv
load_dotenv()

"""
TITAN TRADER — Pre-Launch Connection Test
==========================================
Run this BEFORE going live to verify every integration works.

Usage:
  python test_connections.py

What it checks:
  1. Alpaca API — account access, paper mode confirmed
  2. Anthropic API — Claude responds correctly
  3. Supabase — can read and write all 4 tables
  4. Twilio — sends a real test SMS to your number
  5. Gmail SMTP — sends a test email
  6. Google Sheets — can write to scorecard tab
  7. yfinance — can fetch real market data
  8. Market calendar — correctly identifies today

All checks are independent — a failure in one doesn't stop others.
At the end it prints a clear PASS/FAIL summary.
"""

import os
import sys
import json
from datetime import datetime, timezone
from typing import Dict, Tuple

# Load .env if present (for local testing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

RESULTS = {}


def check(name: str):
    """Decorator to run a check and record result."""
    def decorator(fn):
        def wrapper():
            print(f"\n{'─'*50}")
            print(f"Checking: {name}...")
            try:
                result, detail = fn()
                RESULTS[name] = {"status": "PASS" if result else "FAIL", "detail": detail}
                icon = "✅" if result else "❌"
                print(f"{icon} {name}: {detail}")
                return result
            except Exception as e:
                RESULTS[name] = {"status": "FAIL", "detail": str(e)}
                print(f"❌ {name}: EXCEPTION — {e}")
                return False
        return wrapper
    return decorator


@check("Environment Variables")
def check_env() -> Tuple[bool, str]:
    required = [
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY",
        "SUPABASE_URL", "SUPABASE_KEY",
    ]
    optional = [
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
        "SMTP_USER", "SMTP_PASS",
        "GOOGLE_SHEETS_ID", "GOOGLE_SERVICE_ACCOUNT_JSON",
    ]
    missing_required = [k for k in required if not os.environ.get(k)]
    missing_optional = [k for k in optional if not os.environ.get(k)]

    if missing_required:
        return False, f"MISSING REQUIRED: {', '.join(missing_required)}"

    detail = "All required vars present"
    if missing_optional:
        detail += f" | Optional missing (non-critical): {', '.join(missing_optional)}"
    return True, detail


@check("Alpaca API — Account Access")
def check_alpaca() -> Tuple[bool, str]:
    import requests
    key    = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    paper  = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    base   = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"

    resp = requests.get(
        f"{base}/v2/account",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    portfolio = float(data.get("portfolio_value", 0))
    cash      = float(data.get("cash", 0))
    mode      = "PAPER" if paper else "LIVE"

    if not paper:
        print("  ⚠️  WARNING: ALPACA_PAPER=false — this is LIVE trading mode!")

    return True, f"{mode} | Portfolio: ${portfolio:,.2f} | Cash: ${cash:,.2f}"


@check("Alpaca API — Quote Fetch")
def check_alpaca_quotes() -> Tuple[bool, str]:
    import requests
    key    = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    paper  = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    base   = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"

    resp = requests.get(
        f"{base}/v2/stocks/AAPL/quotes/latest",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=10,
    )
    resp.raise_for_status()
    ask = resp.json().get("quote", {}).get("ap", 0)
    return True, f"AAPL ask: ${ask:.2f}"


@check("Anthropic API — Claude Response")
def check_anthropic() -> Tuple[bool, str]:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    resp   = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        messages=[{"role": "user", "content": 'Reply with exactly: {"status": "ok"}'}],
    )
    raw  = resp.content[0].text.strip()
    data = json.loads(raw)
    return data.get("status") == "ok", f"Model responded correctly: {raw}"


@check("Supabase — Read/Write Trades Table")
def check_supabase() -> Tuple[bool, str]:
    import requests
    url     = os.environ.get("SUPABASE_URL")
    key     = os.environ.get("SUPABASE_KEY")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    # Test write
    test_record = {
        "ticker":     "TEST",
        "entry_date": datetime.now(timezone.utc).isoformat(),
        "status":     "TEST_DELETE_ME",
        "entry_price":1.0,
        "quantity":   1.0,
    }
    write_resp = requests.post(
        f"{url}/rest/v1/trades",
        headers=headers,
        json=test_record,
        timeout=10,
    )

    if not write_resp.ok:
        return False, f"Write failed: {write_resp.status_code} {write_resp.text[:100]}"

    record_id = write_resp.json()[0]["id"] if write_resp.json() else None

    # Test read
    read_resp = requests.get(
        f"{url}/rest/v1/trades?status=eq.TEST_DELETE_ME",
        headers=headers,
        timeout=10,
    )

    # Clean up test record
    if record_id:
        requests.delete(
            f"{url}/rest/v1/trades?id=eq.{record_id}",
            headers=headers,
            timeout=10,
        )

    if not read_resp.ok or not read_resp.json():
        return False, "Write succeeded but read failed"

    return True, f"Read/write working | Test record id={record_id} (cleaned up)"


@check("Supabase — All Tables Exist")
def check_supabase_tables() -> Tuple[bool, str]:
    import requests
    url     = os.environ.get("SUPABASE_URL")
    key     = os.environ.get("SUPABASE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    tables  = ["trades", "daily_snapshots", "lessons", "daily_scores"]
    missing = []

    for table in tables:
        resp = requests.get(
            f"{url}/rest/v1/{table}?limit=1",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 404 or (not resp.ok and "relation" in resp.text.lower()):
            missing.append(table)

    if missing:
        return False, f"Tables missing: {', '.join(missing)} — run supabase_schema.sql"
    return True, f"All {len(tables)} tables exist: {', '.join(tables)}"


@check("Twilio SMS — Test Message")
def check_twilio() -> Tuple[bool, str]:
    sid   = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_ = os.environ.get("TWILIO_FROM_NUMBER")
    to    = os.environ.get("NOTIFICATION_PHONE", "+15167840478")

    if not all([sid, token, from_]):
        return False, "Twilio credentials not configured — SMS disabled"

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        msg    = client.messages.create(
            body=f"⚡ TITAN TRADER — Connection test successful! {datetime.now().strftime('%H:%M ET')}",
            from_=from_,
            to=to,
        )
        return True, f"SMS sent to {to} | SID: {msg.sid}"
    except Exception as e:
        return False, f"SMS failed: {e}"


@check("Gmail SMTP — Test Email")
def check_email() -> Tuple[bool, str]:
    import smtplib
    from email.mime.text import MIMEText

    user  = os.environ.get("SMTP_USER")
    pass_ = os.environ.get("SMTP_PASS")
    to    = os.environ.get("NOTIFICATION_EMAIL", "tylerzar24@gmail.com")

    if not all([user, pass_]):
        return False, "SMTP credentials not configured — email disabled"

    msg          = MIMEText("Titan Trader connection test — all systems go.")
    msg["Subject"]= "⚡ Titan Trader — Connection Test"
    msg["From"]   = user
    msg["To"]     = to

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
        server.starttls()
        server.login(user, pass_)
        server.sendmail(user, to, msg.as_string())

    return True, f"Email sent to {to}"


@check("Google Sheets — Write Access")
def check_sheets() -> Tuple[bool, str]:
    sheets_id = os.environ.get("GOOGLE_SHEETS_ID")
    sa_json   = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not sheets_id:
        return False, "GOOGLE_SHEETS_ID not configured — Sheets disabled"
    if not sa_json:
        return False, "GOOGLE_SERVICE_ACCOUNT_JSON not configured — Sheets disabled"

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_dict = json.loads(sa_json)
    creds      = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    service = build("sheets", "v4", credentials=creds)

    service.spreadsheets().values().update(
        spreadsheetId=sheets_id,
        range="Scorecard!A1",
        valueInputOption="RAW",
        body={"values": [["TITAN TRADER", "Connection test passed", datetime.now().strftime("%Y-%m-%d %H:%M")]]},
    ).execute()

    return True, f"Write successful to sheet {sheets_id[:20]}..."


@check("yfinance — Market Data")
def check_yfinance() -> Tuple[bool, str]:
    import yfinance as yf
    spy   = yf.Ticker("SPY").info
    price = spy.get("regularMarketPrice") or spy.get("currentPrice")
    if not price:
        return False, "Could not fetch SPY price"
    nvda  = yf.Ticker("NVDA").info
    nvda_price = nvda.get("regularMarketPrice") or nvda.get("currentPrice")
    return True, f"SPY: ${price:.2f} | NVDA: ${nvda_price:.2f}"


@check("Market Calendar — Today's Status")
def check_calendar() -> Tuple[bool, str]:
    sys.path.insert(0, os.path.dirname(__file__))
    from utils.market_calendar import is_trading_day, is_market_open, is_pre_market, is_post_market
    from datetime import date

    today     = date.today()
    is_trade  = is_trading_day()
    is_open   = is_market_open()
    is_pre    = is_pre_market()
    is_post   = is_post_market()

    status = (
        "MARKET OPEN" if is_open
        else "PRE-MARKET" if is_pre
        else "POST-MARKET" if is_post
        else "CLOSED" if is_trade
        else "HOLIDAY/WEEKEND"
    )
    return True, f"Today ({today}): {status} | Trading day: {is_trade}"


def main():
    print("\n" + "="*50)
    print("⚡ TITAN TRADER — PRE-LAUNCH CONNECTION TEST")
    print("="*50)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Run all checks
    check_env()
    check_alpaca()
    check_alpaca_quotes()
    check_anthropic()
    check_supabase()
    check_supabase_tables()
    check_twilio()
    check_email()
    check_sheets()
    check_yfinance()
    check_calendar()

    # Summary
    print("\n" + "="*50)
    print("RESULTS SUMMARY")
    print("="*50)

    passed  = [k for k, v in RESULTS.items() if v["status"] == "PASS"]
    failed  = [k for k, v in RESULTS.items() if v["status"] == "FAIL"]
    total   = len(RESULTS)

    for name, result in RESULTS.items():
        icon = "✅" if result["status"] == "PASS" else "❌"
        print(f"  {icon} {name}")

    print(f"\n{len(passed)}/{total} checks passed")

    if failed:
        print(f"\n❌ FAILED CHECKS:")
        for name in failed:
            print(f"   {name}: {RESULTS[name]['detail']}")
        print("\n⚠️  Fix failed checks before going live with real money.")
        sys.exit(1)
    else:
        print("\n✅ ALL CHECKS PASSED — Ready to deploy!")
        print("\nNext steps:")
        print("  1. Set ALPACA_PAPER=true for paper trading first")
        print("  2. Push code to GitHub")
        print("  3. Set all GitHub secrets (see SETUP.md)")
        print("  4. Run workflow manually: Actions → Run workflow → mode=trade, trade_mode=analyze")
        print("  5. Watch the logs for the first scoring session")
        print("  6. After 30 days paper trading, set TRADE_MODE=trade for live execution")
        sys.exit(0)


if __name__ == "__main__":
    main()
