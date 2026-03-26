-- ============================================================
-- TITAN TRADER — Complete Supabase Schema
-- Run all of these in your Supabase SQL editor
-- ============================================================

-- Trades table — every entry and exit
CREATE TABLE IF NOT EXISTS trades (
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
  trail_pct           NUMERIC,
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

-- Daily portfolio snapshots
CREATE TABLE IF NOT EXISTS daily_snapshots (
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
CREATE TABLE IF NOT EXISTS lessons (
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

-- Daily scores — used to detect review triggers day-over-day
CREATE TABLE IF NOT EXISTS daily_scores (
  id           BIGSERIAL PRIMARY KEY,
  ticker       TEXT NOT NULL,
  date         DATE NOT NULL,
  total_score  NUMERIC,
  signal       TEXT,
  components   JSONB,
  ai_reasoning TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(ticker, date)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_trades_ticker_status ON trades(ticker, status);
CREATE INDEX IF NOT EXISTS idx_trades_entry_date ON trades(entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date ON daily_snapshots(date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_scores_ticker_date ON daily_scores(ticker, date DESC);
CREATE INDEX IF NOT EXISTS idx_lessons_created ON lessons(created_at DESC);
