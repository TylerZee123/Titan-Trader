# ⚡ TITAN TRADER v2 — Complete Setup Guide

## What's new in v2
- **67 stocks** across 8 buckets (core, momentum, crypto, high-growth, fallen angels, dividend, hedge, meme)
- **Dual strategy** — LONG weights for core positions, SWING weights for momentum plays
- **SMS alerts** via Twilio to 516-784-0478 (morning brief, trade confirmations, EOD summary)
- **Performance tracker** — every trade logged to Supabase, synced to Google Sheets
- **Earnings calendar** — awareness built in, never blindsided by binary risk
- **Congressional trades** — free alpha from public STOCK Act filings
- **Fallen angel scanner** — beaten-down asymmetric opportunities
- **Fractional shares** enabled — $5K budget works for all price points
- **PDT-aware** — structured for overnight swing trades, not day trading

---

## GitHub Secrets to Set

Go to: Repo → Settings → Secrets and variables → Actions → New repository secret

### Required
| Secret | Where to get it |
|---|---|
| `ALPACA_API_KEY` | alpaca.markets → Paper Trading → API Keys |
| `ALPACA_SECRET_KEY` | Same as above |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASS` | Gmail → Settings → Security → App Passwords → generate one |

### SMS (Twilio)
| Secret | Where to get it |
|---|---|
| `TWILIO_ACCOUNT_SID` | twilio.com → Console → Account SID |
| `TWILIO_AUTH_TOKEN` | twilio.com → Console → Auth Token |
| `TWILIO_FROM_NUMBER` | twilio.com → Phone Numbers → get a free number |

### Performance Tracking
| Secret | Where to get it |
|---|---|
| `SUPABASE_URL` | supabase.com → Project → Settings → API → Project URL |
| `SUPABASE_KEY` | supabase.com → Project → Settings → API → anon/public key |
| `GOOGLE_SHEETS_ID` | From your Google Sheet URL: docs.google.com/spreadsheets/d/**THIS_PART**/edit |
| `GOOGLE_SHEETS_API_KEY` | console.cloud.google.com → APIs → Sheets API → Credentials |

---

## Supabase Tables to Create

Run these SQL commands in your Supabase SQL editor:

```sql
-- Trade log
CREATE TABLE trades (
  id                  BIGSERIAL PRIMARY KEY,
  ticker              TEXT NOT NULL,
  entry_date          TIMESTAMPTZ,
  entry_price         NUMERIC,
  quantity            NUMERIC,
  dollars_invested    NUMERIC,
  allocation_pct      NUMERIC,
  tier                TEXT,
  bucket              TEXT,
  strategy            TEXT,
  entry_score         NUMERIC,
  signal              TEXT,
  stop_loss_pct       NUMERIC,
  take_profit_pct     NUMERIC,
  stop_price          NUMERIC,
  target_price        NUMERIC,
  ai_reasoning        TEXT,
  market_regime       TEXT,
  vix_at_entry        NUMERIC,
  sig_technical       NUMERIC,
  sig_fundamental     NUMERIC,
  sig_moat            NUMERIC,
  sig_sentiment       NUMERIC,
  sig_growth          NUMERIC,
  sig_management      NUMERIC,
  sig_ai_analysis     NUMERIC,
  status              TEXT DEFAULT 'OPEN',
  exit_date           TIMESTAMPTZ,
  exit_price          NUMERIC,
  exit_reason         TEXT,
  pnl                 NUMERIC,
  pnl_pct             NUMERIC,
  hold_days           INTEGER,
  won                 BOOLEAN,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Daily snapshots
CREATE TABLE daily_snapshots (
  id               BIGSERIAL PRIMARY KEY,
  date             DATE UNIQUE,
  portfolio_value  NUMERIC,
  cash             NUMERIC,
  pnl_today        NUMERIC,
  pnl_today_pct    NUMERIC,
  spy_price        NUMERIC,
  vix              NUMERIC,
  regime           TEXT,
  open_positions   INTEGER,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Loss lessons
CREATE TABLE lessons (
  id              BIGSERIAL PRIMARY KEY,
  ticker          TEXT,
  pnl             NUMERIC,
  pnl_pct         NUMERIC,
  failure_mode    TEXT,
  lesson          TEXT,
  rule_added      TEXT,
  severity        TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Google Sheets Setup

1. Create a new Google Sheet at sheets.google.com
2. Create 3 tabs: `Scorecard`, `Trades`, `Daily P&L`
3. Copy the Sheet ID from the URL
4. Enable the Google Sheets API at console.cloud.google.com
5. Create an API key and add it as `GOOGLE_SHEETS_API_KEY` secret

---

## Twilio Setup (5 min)

1. Go to twilio.com → sign up free
2. Get a free phone number (US)
3. Verify your personal number (516-784-0478) in the console
4. Copy Account SID, Auth Token, and your Twilio number
5. Add all three as GitHub secrets

---

## File Structure

```
titan-trader-v2/
├── main.py
├── requirements.txt
├── core/
│   ├── engine.py              ← orchestrates everything
│   ├── scorer.py
│   └── executor.py            ← Alpaca bracket orders
├── data/
│   ├── universe.py            ← 67 stocks across 8 buckets
│   ├── market_data.py
│   ├── fundamental_data.py
│   ├── news_scanner.py        ← pre/post market intelligence
│   ├── news_sentiment.py
│   ├── earnings_calendar.py   ← never blindsided by earnings
│   ├── congressional_trades.py ← free alpha
│   └── fallen_angel_scanner.py ← asymmetric recovery plays
├── signals/
│   ├── technical.py
│   ├── fundamental.py
│   └── ai_signal.py           ← Claude holistic analysis
├── risk/
│   ├── risk_manager.py
│   └── position_allocator.py  ← confidence tiers + bucket caps
├── learning/
│   └── loss_learner.py        ← trade autopsy + weight adjustment
├── performance/
│   └── tracker.py             ← Supabase + Google Sheets
├── utils/
│   ├── logger.py
│   └── notifier.py            ← SMS (Twilio) + email
└── github/
    └── workflows/
        └── titan_trader.yml   ← 3 daily jobs
```

---

## Portfolio Allocation Logic

| Bucket | Max % | Strategy | Min Score |
|---|---|---|---|
| Core compounders | 40% | LONG (months-years) | 62 |
| Momentum/growth | 25% | SWING (days-weeks) | 65 |
| Crypto-adjacent | 8% | SWING | 72 |
| High-growth mid-cap | 10% | SWING | 68 |
| Fallen angels | 8% | LONG/SWING | 58 |
| Dividend/ballast | 15% | LONG | 60 |
| Macro hedges | 5% | LONG | 55 |
| Meme wildcards | 2% | SWING | 80 |

---

## What You'll Receive Daily

**8:00 AM SMS** — Market sentiment, trading bias, top buy/sell signals
**9:35 AM SMS** — Exact trades executed with sizes and stop/target levels
**9:35 AM Email** — Full report with all scored stocks and AI reasoning
**5:00 PM SMS** — Day's P&L, portfolio value
**5:00 PM Email** — Post-market intelligence, loss lessons, performance scorecard

---

## Performance Benchmark

The bot is benchmarked against S&P 500 (~10% annually).
Goal: beat the market on both short-term alpha AND long-term compounding.
Google Sheets scorecard shows real-time:
  - Total return vs S&P 500
  - Win rate (target >55%)
  - Profit factor (target >1.5x)
  - Sharpe ratio (target >1.0)
  - Max drawdown (target <15%)
