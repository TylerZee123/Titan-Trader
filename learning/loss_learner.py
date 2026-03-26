"""
LossLearningEngine — Supabase Primary, JSON Backup
=====================================================
Supabase is primary store. Local JSON is backup.
This ensures lessons survive GitHub Actions container resets.
"""

import logging
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List
import anthropic
import requests

logger = logging.getLogger("titan_trader")

LESSONS_FILE    = os.path.join(os.path.dirname(__file__), "lessons.json")
WEIGHT_ADJ_FILE = os.path.join(os.path.dirname(__file__), "weight_adjustments.json")
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")

AUTOPSY_PROMPT = """You are a master risk analyst performing a post-mortem on a losing stock trade.
Identify WHY the trade lost money and extract actionable lessons.
Return ONLY JSON:
{
  "failure_mode": string,
  "contributing_factors": [string],
  "signals_that_warned": [string],
  "signals_that_failed": [string],
  "lesson": string,
  "rule_to_add": string,
  "signal_weight_adjustments": {
    "technical": float, "fundamental": float, "sentiment": float,
    "ai_analysis": float, "management": float, "macro": float
  },
  "avoid_conditions": [string],
  "severity": "MINOR"|"MODERATE"|"SEVERE"
}
failure_mode: "MACRO_SHOCK"|"EARNINGS_MISS"|"SECTOR_ROTATION"|"TECHNICAL_FAILURE"|
"FUNDAMENTAL_DETERIORATION"|"MANAGEMENT_EVENT"|"SENTIMENT_REVERSAL"|
"POSITION_SIZING"|"ENTRY_TIMING"|"STOP_TOO_TIGHT"|"BLACK_SWAN"|"OVERCONFIDENCE"
weight adjustments: -0.05 to +0.05 each. No markdown. Only JSON."""


class LossLearningEngine:

    def __init__(self, config: Dict):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config["anthropic_api_key"])
        self._sb_headers = {
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation",
        }
        self.lessons            = self._load_lessons()
        self.weight_adjustments = self._load_weight_adjustments()

    def analyze_loss(self, trade: Dict, market_context: Dict, news_at_entry: List[str]) -> Dict:
        logger.info(f"\n{'='*50}")
        logger.info(f"LOSS AUTOPSY: {trade['ticker']} — "
                    f"${trade.get('pnl', 0):,.2f} ({trade.get('pnl_pct', 0):.1f}%)")
        logger.info(f"{'='*50}")

        autopsy = self._run_autopsy(trade, market_context, news_at_entry)
        record  = {
            "id":               len(self.lessons) + 1,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "ticker":           trade["ticker"],
            "pnl":              trade.get("pnl", 0),
            "pnl_pct":          trade.get("pnl_pct", 0),
            "entry_score":      trade.get("entry_score", 0),
            "hold_days":        trade.get("hold_days", 0),
            "entry_date":       trade.get("entry_date", ""),
            "exit_date":        trade.get("exit_date", ""),
            "exit_reason":      trade.get("exit_reason", ""),
            "market_regime":    market_context.get("regime", "UNKNOWN"),
            "vix_at_entry":     trade.get("vix_at_entry", 0),
            "autopsy":          autopsy,
            "signals_at_entry": trade.get("signals_at_entry", {}),
        }

        self.lessons.append(record)
        self._save_lessons()
        self._apply_weight_lesson(autopsy)

        logger.info(f"FAILURE MODE: {autopsy.get('failure_mode')}")
        logger.info(f"LESSON: {autopsy.get('lesson')}")
        logger.info(f"RULE: {autopsy.get('rule_to_add')}")
        return record

    def get_adjusted_weights(self, base_weights: Dict) -> Dict:
        if not self.weight_adjustments:
            return base_weights
        adjusted = dict(base_weights)
        for k, adj in self.weight_adjustments.items():
            if k in adjusted:
                adjusted[k] = max(0.01, min(0.30, adjusted[k] + adj))
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}
        return adjusted

    def get_lesson_summary(self) -> Dict:
        if not self.lessons:
            return {"total_losses": 0, "lessons": [], "all_rules": []}
        failure_modes = {}
        total_pnl     = 0.0
        for l in self.lessons:
            mode = l.get("autopsy", {}).get("failure_mode", "UNKNOWN")
            failure_modes[mode] = failure_modes.get(mode, 0) + 1
            total_pnl += float(l.get("pnl") or 0)
        return {
            "total_losses":        len(self.lessons),
            "total_pnl_lost":      round(total_pnl, 2),
            "avg_loss":            round(total_pnl / len(self.lessons), 2),
            "failure_modes":       failure_modes,
            "most_common_failure": max(failure_modes, key=failure_modes.get) if failure_modes else None,
            "weight_adjustments":  self.weight_adjustments,
            "recent_lessons": [
                {"ticker": l["ticker"], "pnl": l.get("pnl", 0),
                 "lesson": l.get("autopsy", {}).get("lesson", ""), "date": l["timestamp"][:10]}
                for l in self.lessons[-5:]
            ],
            "all_rules": [
                l.get("autopsy", {}).get("rule_to_add", "")
                for l in self.lessons if l.get("autopsy", {}).get("rule_to_add")
            ],
        }

    def _run_autopsy(self, trade: Dict, market_context: Dict, news: List[str]) -> Dict:
        signals   = trade.get("signals_at_entry", {})
        news_text = "\n".join(f"  - {h}" for h in news[:8]) or "  Not recorded"
        prompt    = f"""LOSING TRADE: {trade['ticker']}
P&L: ${trade.get('pnl',0):,.2f} ({trade.get('pnl_pct',0):.1f}%)
Exit reason: {trade.get('exit_reason','Unknown')}
Hold: {trade.get('hold_days',0)} days | Entry score: {trade.get('entry_score',0):.1f}
Regime: {market_context.get('regime')} | VIX: {trade.get('vix_at_entry','?')}
Signals at entry: {json.dumps({k: round(float(v),3) for k,v in signals.items() if v})}
News: {news_text}
Prior rules: {self._get_existing_rules_text()}
Analyze and return JSON."""
        try:
            resp = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=700,
                system=AUTOPSY_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = re.sub(r"```json|```", "", resp.content[0].text).strip()
            return json.loads(raw)
        except Exception as e:
            logger.error(f"Autopsy failed: {e}")
            return {"failure_mode": "UNKNOWN", "lesson": "Autopsy unavailable",
                    "rule_to_add": "", "signal_weight_adjustments": {},
                    "avoid_conditions": [], "severity": "MODERATE"}

    def _apply_weight_lesson(self, autopsy: Dict):
        for signal, adj in autopsy.get("signal_weight_adjustments", {}).items():
            current = self.weight_adjustments.get(signal, 0.0)
            self.weight_adjustments[signal] = round(max(-0.05, min(0.05, current * 0.93 + adj * 0.07)), 4)
        self._save_weight_adjustments()

    def _get_existing_rules_text(self) -> str:
        rules = [l.get("autopsy", {}).get("rule_to_add", "") for l in self.lessons[-10:]
                 if l.get("autopsy", {}).get("rule_to_add")]
        return "\n".join(f"  - {r}" for r in rules) if rules else "  (No prior rules)"

    def _load_lessons(self) -> List[Dict]:
        # Try Supabase first
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                resp = requests.get(
                    f"{SUPABASE_URL}/rest/v1/lessons?order=created_at.asc&limit=500",
                    headers=self._sb_headers, timeout=10,
                )
                if resp.ok and resp.json():
                    rows = resp.json()
                    logger.info(f"Loaded {len(rows)} lessons from Supabase")
                    return [{"id": r.get("id"), "timestamp": r.get("created_at",""),
                             "ticker": r.get("ticker",""), "pnl": r.get("pnl",0),
                             "pnl_pct": r.get("pnl_pct",0), "entry_score": 0, "hold_days": 0,
                             "autopsy": {"failure_mode": r.get("failure_mode",""),
                                         "lesson": r.get("lesson",""),
                                         "rule_to_add": r.get("rule_added",""),
                                         "severity": r.get("severity","")}} for r in rows]
            except Exception as e:
                logger.warning(f"Supabase lesson load failed: {e}")
        # Fall back to local JSON
        if os.path.exists(LESSONS_FILE):
            try:
                with open(LESSONS_FILE, "r") as f:
                    data = json.load(f)
                    logger.info(f"Loaded {len(data)} lessons from local JSON")
                    return data
            except Exception as e:
                logger.warning(f"Local lesson load failed: {e}")
        return []

    def _save_lessons(self):
        try:
            os.makedirs(os.path.dirname(LESSONS_FILE), exist_ok=True)
            with open(LESSONS_FILE, "w") as f:
                json.dump(self.lessons, f, indent=2)
        except Exception as e:
            logger.error(f"Local lesson save failed: {e}")

    def _load_weight_adjustments(self) -> Dict:
        if os.path.exists(WEIGHT_ADJ_FILE):
            try:
                with open(WEIGHT_ADJ_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_weight_adjustments(self):
        try:
            os.makedirs(os.path.dirname(WEIGHT_ADJ_FILE), exist_ok=True)
            with open(WEIGHT_ADJ_FILE, "w") as f:
                json.dump(self.weight_adjustments, f, indent=2)
        except Exception as e:
            logger.error(f"Weight save failed: {e}")
