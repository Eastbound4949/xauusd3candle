"""
3-Candle Breakout Signal Logic — live data via yfinance GC=F H1.

Signal fires when:
  1. Last 3 completed H1 bars are all green (close > open)
  2. The most recent completed bar closed above pattern HIGH (BUY)
     or below pattern LOW (SELL)
  3. SL distance <= MAX_ATR_SL_MULT × ATR(14)

Returns signal dict or None.
"""

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

log = logging.getLogger(__name__)

# ── Strategy params (re-opt 2026-07-04: 5yr Dukascopy H1, close-confirm model — ──
#    see research-archive/XAUUSD 3-Candle (reopt batch2)/optimize_close_confirm_v2.py)
# candles=2 RR=3.5 sess=NY-only(12-21) atr_mult=4.0 both-dir @ 5% risk:
# Sharpe=1.83 CAGR=267.2% MaxDD=-39.2% WR=32.3% PF=1.67 74.8 tr/yr, 1 neg yr/5
CANDLE_COUNT    = 2
RR_RATIO        = 3.5
ATR_PERIOD      = 14
MAX_ATR_SL_MULT = 4.0
ACTIVE_HOURS    = set(range(12, 21))  # NY session only (UTC)

TICKER   = "GC=F"    # Gold futures — closest free proxy for XAUUSD
INTERVAL = "1h"
N_BARS   = 60        # bars to fetch


def fetch_bars() -> pd.DataFrame | None:
    """Fetch latest H1 Gold bars from yfinance. Returns completed bars only."""
    if yf is None:
        log.error("yfinance not installed")
        return None
    try:
        df = yf.download(TICKER, period="5d", interval=INTERVAL,
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[["open", "high", "low", "close"]].dropna()
        # Drop the last bar (may be incomplete — current forming bar)
        return df.iloc[:-1]
    except Exception as exc:
        log.warning("fetch_bars failed: %s", exc)
        return None


def _compute_atr(df: pd.DataFrame) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return float(tr.rolling(ATR_PERIOD).mean().iloc[-1])


def generate_signal(df: pd.DataFrame | None = None) -> dict | None:
    """
    Check for 3-candle breakout signal on latest H1 data.
    Fetches data if df not provided.
    Returns signal dict or None.
    """
    if df is None:
        df = fetch_bars()
    if df is None or len(df) < CANDLE_COUNT + ATR_PERIOD + 3:
        return None

    # Session filter: only signal during London/NY (UTC)
    last_bar_time = df.index[-1]
    if last_bar_time.hour not in ACTIVE_HOURS:
        log.debug("Outside session (%s UTC)", last_bar_time.hour)
        return None

    # Pattern bars: the 3 bars before the most recently closed bar
    pattern = df.iloc[-(CANDLE_COUNT + 2):-2]
    signal_bar = df.iloc[-2]     # most recently completed bar (the breakout bar)

    if not all(pattern.iloc[i]["close"] > pattern.iloc[i]["open"]
               for i in range(CANDLE_COUNT)):
        return None

    pattern_high = float(pattern["high"].max())
    pattern_low  = float(pattern["low"].min())
    atr_val      = _compute_atr(df.iloc[:-1])

    if atr_val == 0 or np.isnan(atr_val):
        return None

    close = float(signal_bar["close"])

    if close > pattern_high:
        sl_dist = close - pattern_low
        if sl_dist > MAX_ATR_SL_MULT * atr_val:
            return None
        sl = round(pattern_low, 2)
        tp = round(close + RR_RATIO * sl_dist, 2)
        return {
            "direction":    "buy",
            "entry":        round(close, 2),
            "sl":           sl,
            "tp":           tp,
            "pattern_high": round(pattern_high, 2),
            "pattern_low":  round(pattern_low, 2),
            "atr":          round(atr_val, 2),
            "bar_time":     last_bar_time.isoformat(),
        }

    if close < pattern_low:
        sl_dist = pattern_high - close
        if sl_dist > MAX_ATR_SL_MULT * atr_val:
            return None
        sl = round(pattern_high, 2)
        tp = round(close - RR_RATIO * sl_dist, 2)
        return {
            "direction":    "sell",
            "entry":        round(close, 2),
            "sl":           sl,
            "tp":           tp,
            "pattern_high": round(pattern_high, 2),
            "pattern_low":  round(pattern_low, 2),
            "atr":          round(atr_val, 2),
            "bar_time":     last_bar_time.isoformat(),
        }

    return None


def get_latest_price() -> float | None:
    """Return latest Gold mid-price from yfinance."""
    try:
        df = yf.download(TICKER, period="1d", interval="1m",
                         auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        return float(df["close"].iloc[-1])
    except Exception:
        return None
