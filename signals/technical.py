"""
TechnicalSignals
==================
Full technical analysis suite covering:
- Trend (moving averages, EMAs)
- Momentum (RSI, MACD, Stochastic)
- Volatility (Bollinger Bands, ATR)
- Volume (OBV, volume ratio, institutional accumulation)
- Chart Patterns (golden cross, death cross, breakouts, consolidation)
- Price relative to 52-week range
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional

logger = logging.getLogger("titan_trader")


class TechnicalSignals:

    def analyze(self, price_data: Optional[Dict]) -> Dict:
        """
        Master technical analysis function.
        Returns score (0–1) and detailed component breakdown.
        """
        if not price_data or price_data.get("df") is None:
            return {"score": 0.5, "components": {}, "signals": []}

        df = price_data["df"].copy()

        if len(df) < 50:
            return {"score": 0.5, "components": {}, "signals": ["Insufficient data"]}

        signals  = []
        scores   = []

        # ── 1. Trend Analysis ──────────────────────────────────────────────
        trend = self._trend_analysis(df)
        scores.append(trend["score"] * 0.25)
        signals.extend(trend["signals"])

        # ── 2. Momentum ────────────────────────────────────────────────────
        momentum = self._momentum_analysis(df)
        scores.append(momentum["score"] * 0.25)
        signals.extend(momentum["signals"])

        # ── 3. Volatility ──────────────────────────────────────────────────
        volatility = self._volatility_analysis(df)
        scores.append(volatility["score"] * 0.20)
        signals.extend(volatility["signals"])

        # ── 4. Volume ──────────────────────────────────────────────────────
        volume = self._volume_analysis_detail(df)
        scores.append(volume["score"] * 0.15)
        signals.extend(volume["signals"])

        # ── 5. Chart Patterns ──────────────────────────────────────────────
        patterns = self._pattern_analysis(df, price_data)
        scores.append(patterns["score"] * 0.15)
        signals.extend(patterns["signals"])

        total_score = sum(scores)

        return {
            "score":      round(total_score, 3),
            "components": {
                "trend":      trend["score"],
                "momentum":   momentum["score"],
                "volatility": volatility["score"],
                "volume":     volume["score"],
                "patterns":   patterns["score"],
            },
            "signals": signals[:10],
            "rsi":     momentum.get("rsi", 50),
            "macd_bullish": momentum.get("macd_bullish", False),
        }

    # ── Trend ──────────────────────────────────────────────────────────────

    def _trend_analysis(self, df: pd.DataFrame) -> Dict:
        close = df["Close"]
        signals = []
        score = 0.5

        ma20  = close.rolling(20).mean().iloc[-1]
        ma50  = close.rolling(50).mean().iloc[-1]
        ma200 = close.rolling(200).mean().iloc[-1] if len(df) >= 200 else ma50
        ema12 = close.ewm(span=12).mean().iloc[-1]
        ema26 = close.ewm(span=26).mean().iloc[-1]
        price = close.iloc[-1]

        pts = 0
        max_pts = 5

        if price > ma20:   pts += 1; signals.append("Price above 20MA ✓")
        else: signals.append("Price below 20MA ✗")

        if price > ma50:   pts += 1; signals.append("Price above 50MA ✓")
        else: signals.append("Price below 50MA ✗")

        if price > ma200:  pts += 1; signals.append("Price above 200MA ✓")
        else: signals.append("Price below 200MA ✗")

        # Golden cross / death cross
        if len(df) >= 200:
            prev_ma50  = close.rolling(50).mean().iloc[-2]
            prev_ma200 = close.rolling(200).mean().iloc[-2]
            if prev_ma50 <= prev_ma200 and float(ma50) > float(ma200):
                pts += 1; signals.append("GOLDEN CROSS — 50MA crossed above 200MA 🔥")
            elif prev_ma50 >= prev_ma200 and float(ma50) < float(ma200):
                pts -= 1; signals.append("DEATH CROSS — 50MA crossed below 200MA ⚠️")
            elif float(ma50) > float(ma200):
                pts += 1; signals.append("50MA above 200MA (uptrend) ✓")

        if ema12 > ema26: pts += 1; signals.append("EMA12 > EMA26 (short-term bullish) ✓")

        score = min(max(pts / max_pts, 0), 1.0)
        return {"score": round(score, 3), "signals": signals[:4]}

    # ── Momentum ───────────────────────────────────────────────────────────

    def _momentum_analysis(self, df: pd.DataFrame) -> Dict:
        close = df["Close"]
        signals = []
        pts = 0
        max_pts = 4
        macd_bullish = False

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, 1e-9)
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

        if 40 <= rsi <= 70:
            pts += 1; signals.append(f"RSI {rsi:.0f} — healthy range ✓")
        elif rsi < 35:
            pts += 0.5; signals.append(f"RSI {rsi:.0f} — oversold (potential reversal)")
        elif rsi > 75:
            pts -= 0.5; signals.append(f"RSI {rsi:.0f} — overbought ⚠️")

        # MACD
        ema12   = close.ewm(span=12).mean()
        ema26   = close.ewm(span=26).mean()
        macd    = ema12 - ema26
        signal  = macd.ewm(span=9).mean()
        hist    = macd - signal

        if float(macd.iloc[-1]) > float(signal.iloc[-1]):
            pts += 1; signals.append("MACD above signal line ✓")
            macd_bullish = True
        else:
            signals.append("MACD below signal line ✗")

        if float(hist.iloc[-1]) > float(hist.iloc[-2]):
            pts += 0.5; signals.append("MACD histogram expanding ✓")

        # Stochastic
        low14  = df["Low"].rolling(14).min()
        high14 = df["High"].rolling(14).max()
        stoch_k = float(((close - low14) / (high14 - low14 + 1e-9) * 100).iloc[-1])

        if 20 <= stoch_k <= 80:
            pts += 0.5; signals.append(f"Stochastic {stoch_k:.0f} — normal range ✓")
        elif stoch_k < 20:
            pts += 1;   signals.append(f"Stochastic {stoch_k:.0f} — oversold ✓")

        # Rate of change (momentum)
        roc = float(((close.iloc[-1] / close.iloc[-20]) - 1) * 100)
        if roc > 5:
            pts += 0.5; signals.append(f"20-day ROC: +{roc:.1f}% ✓")
        elif roc < -5:
            signals.append(f"20-day ROC: {roc:.1f}% ✗")

        score = min(max(pts / max_pts, 0), 1.0)
        return {"score": round(score, 3), "signals": signals, "rsi": round(rsi, 1), "macd_bullish": macd_bullish}

    # ── Volatility ─────────────────────────────────────────────────────────

    def _volatility_analysis(self, df: pd.DataFrame) -> Dict:
        close = df["Close"]
        signals = []
        pts = 0

        # Bollinger Bands
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        price = float(close.iloc[-1])
        bb_pct = float((close - lower) / (upper - lower + 1e-9)).iloc[-1]  # 0=lower, 1=upper

        if 0.2 <= bb_pct <= 0.8:
            pts += 1; signals.append(f"BB position: {bb_pct:.0%} (mid-range) ✓")
        elif bb_pct < 0.1:
            pts += 1; signals.append("Near lower Bollinger Band — potential bounce")
        elif bb_pct > 0.95:
            pts -= 0.5; signals.append("Near upper Bollinger Band — extended ⚠️")

        # Bollinger Band squeeze (low volatility = potential big move coming)
        bb_width = float(((upper - lower) / ma20).iloc[-1])
        bb_width_avg = float(((upper - lower) / ma20).rolling(50).mean().iloc[-1])
        if bb_width < bb_width_avg * 0.7:
            pts += 1; signals.append("BB squeeze detected — volatility breakout likely")

        # ATR (normalized)
        atr = self._calc_atr(df, 14)
        atr_pct = atr / price * 100
        if atr_pct < 2.0:
            pts += 0.5; signals.append(f"Low ATR ({atr_pct:.1f}%) — stable ✓")
        elif atr_pct > 5.0:
            pts -= 0.5; signals.append(f"High ATR ({atr_pct:.1f}%) — high volatility ⚠️")

        score = min(max(pts / 2.5, 0), 1.0)
        return {"score": round(score, 3), "signals": signals}

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high = df["High"]
        low  = df["Low"]
        close = df["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    # ── Volume ─────────────────────────────────────────────────────────────

    def volume_analysis(self, price_data: Optional[Dict]) -> float:
        """Public interface used by engine."""
        if not price_data:
            return 0.5
        result = self._volume_analysis_detail(price_data["df"])
        return result["score"]

    def _volume_analysis_detail(self, df: pd.DataFrame) -> Dict:
        volume = df["Volume"]
        close  = df["Close"]
        signals = []
        pts = 0

        avg_vol_50  = float(volume.rolling(50).mean().iloc[-1])
        avg_vol_20  = float(volume.rolling(20).mean().iloc[-1])
        today_vol   = float(volume.iloc[-1])
        vol_ratio   = today_vol / avg_vol_50 if avg_vol_50 > 0 else 1.0

        if vol_ratio > 1.5:
            pts += 1; signals.append(f"Volume {vol_ratio:.1f}x above 50-day avg — strong conviction ✓")
        elif vol_ratio > 1.0:
            pts += 0.5; signals.append(f"Volume {vol_ratio:.1f}x avg — above average ✓")
        else:
            signals.append(f"Volume {vol_ratio:.1f}x avg — below average")

        # OBV trend (on-balance volume)
        obv = (volume * close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
        obv_ma = obv.rolling(20).mean()
        if float(obv.iloc[-1]) > float(obv_ma.iloc[-1]):
            pts += 1; signals.append("OBV above 20MA — accumulation trend ✓")
        else:
            signals.append("OBV below 20MA — distribution trend ✗")

        # Price up on high volume = institutional buying
        if float(close.iloc[-1]) > float(close.iloc[-2]) and vol_ratio > 1.3:
            pts += 1; signals.append("Price up on above-avg volume — institutional buying signal ✓")

        score = min(max(pts / 3, 0), 1.0)
        return {"score": round(score, 3), "signals": signals}

    # ── Chart Patterns ─────────────────────────────────────────────────────

    def _pattern_analysis(self, df: pd.DataFrame, price_data: Dict) -> Dict:
        close = df["Close"]
        signals = []
        pts = 0.5  # neutral start

        # 52-week range position
        high_52 = float(close.max())
        low_52  = float(close.min())
        current = float(close.iloc[-1])
        pct_from_high = (current - high_52) / high_52
        range_pct = (current - low_52) / (high_52 - low_52) if (high_52 - low_52) > 0 else 0.5

        if range_pct > 0.8:
            pts += 0.5; signals.append(f"Near 52-week high ({range_pct:.0%}) — strength ✓")
        elif range_pct < 0.3:
            pts += 0.3; signals.append(f"Near 52-week low ({range_pct:.0%}) — potential value")

        # Consolidation detection (tight range = energy building)
        last_20_range = (close.tail(20).max() - close.tail(20).min()) / close.tail(20).mean()
        if last_20_range < 0.05:
            pts += 0.3; signals.append("Tight consolidation — potential breakout setup")

        # Recent breakout detection
        prev_high = float(close.tail(60).iloc[:-5].max())
        if current > prev_high * 1.02:
            pts += 0.5; signals.append("Fresh 60-day breakout — bullish continuation signal 🔥")

        score = min(max(pts / 2.0, 0), 1.0)
        return {"score": round(score, 3), "signals": signals}
