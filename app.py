"""
XAUUSD 3-Candle Breakout — Paper Trading Dashboard.

Deploys to Railway. Checks H1 signals every 15 min via yfinance GC=F.
No real broker / MT5 required.

Strategy params: 3 candles, 1.5 R:R, 2% risk, H1
Optimised via 10yr D1 + 2yr H1 backtests (grid search, DD < 30%).
"""

import logging
import os
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import notify
import strategy
import trader

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

REFRESH_INTERVAL = 15 * 60   # 15 minutes between signal checks
BOT_NAME         = "XAUUSD 3-Candle Paper Bot"


# ── UI helpers ────────────────────────────────────────────────────────────────

def _delta_color(val: float) -> str:
    return "normal" if val >= 0 else "inverse"


def _render_header(equity: float, stats: dict):
    st.title(BOT_NAME)
    st.caption("Paper trading | XAUUSD H1 | 3 consecutive green candles breakout")

    pnl     = equity - trader.STARTING_CAPITAL
    pnl_pct = pnl / trader.STARTING_CAPITAL * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity", f"${equity:,.2f}",
              f"{pnl:+.2f} ({pnl_pct:+.1f}%)")
    c2.metric("Trades", stats["total"],
              f"{stats['wins']}W / {stats['losses']}L")
    c3.metric("Win Rate", f"{stats['win_rate']:.1f}%")
    c4.metric("Profit Factor",
              f"{stats['profit_factor']:.3f}" if stats['profit_factor'] != float('inf') else "inf")
    c5.metric("Expectancy", f"${stats['expectancy']:+.2f}")


def _render_open_position():
    trade = trader.get_open_trade()
    if not trade:
        st.info("No open position — watching for signal")
        return

    st.subheader("Open Position")
    price = strategy.get_latest_price()
    d     = trade["direction"]

    if price:
        if d == "buy":
            unrealised_r = (price - trade["entry"]) / (trade["entry"] - trade["sl"])
        else:
            unrealised_r = (trade["entry"] - price) / (trade["sl"] - trade["entry"])
        unrealised_pnl = trade["risk_amount"] * unrealised_r
    else:
        unrealised_pnl = None

    label = "BUY" if d == "buy" else "SELL"
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Direction", label)
    c2.metric("Entry",     f"${trade['entry']:,.2f}")
    c3.metric("SL",        f"${trade['sl']:,.2f}")
    c4.metric("TP",        f"${trade['tp']:,.2f}")
    c5.metric("Risk",      f"${trade['risk_amount']:.2f}")

    if unrealised_pnl is not None and price:
        st.metric("Unrealised P&L",
                  f"${unrealised_pnl:+.2f}",
                  f"Current: ${price:,.2f}")

    opened = trade.get("open_time", "")
    st.caption(f"Opened: {opened[:19].replace('T',' ')} UTC")


def _render_equity_chart():
    history = trader.get_equity_history()
    if len(history) < 2:
        return
    times  = [h["ts"][:19].replace("T", " ") for h in history]
    values = [h["equity"] for h in history]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=times, y=values,
        mode="lines+markers",
        line=dict(color="#2196F3", width=2),
        marker=dict(size=4),
        name="Equity",
    ))
    fig.add_hline(y=trader.STARTING_CAPITAL,
                  line_dash="dash", line_color="gray",
                  annotation_text=f"Start ${trader.STARTING_CAPITAL:,.0f}")
    fig.update_layout(
        title="Paper Equity Curve",
        yaxis_title="Equity ($)",
        xaxis_title="",
        height=320,
        margin=dict(l=0, r=0, t=40, b=0),
        plot_bgcolor="#0E1117",
        paper_bgcolor="#0E1117",
        font=dict(color="white"),
        yaxis=dict(gridcolor="#333"),
        xaxis=dict(gridcolor="#333"),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_trade_history():
    trades = trader.get_all_trades()
    if not trades:
        st.info("No closed trades yet.")
        return

    rows = []
    for t in trades:
        pnl = t.get("pnl") or 0
        rows.append({
            "Time":      (t["open_time"] or "")[:16].replace("T", " "),
            "Dir":       t["direction"].upper(),
            "Entry":     f"${t['entry']:,.2f}",
            "Close":     f"${t['close_price']:,.2f}" if t.get("close_price") else "-",
            "P&L":       f"${pnl:+.2f}",
            "Result":    "WIN" if t["status"] == "won" else "LOSS",
        })

    df = pd.DataFrame(rows)

    def colour(row):
        green = "background-color: #1a3a1a"
        red   = "background-color: #3a1a1a"
        return [green if row["Result"] == "WIN" else red] * len(row)

    styled = df.style.apply(colour, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_config():
    with st.expander("Strategy Config"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Risk/trade",   f"{trader.RISK_PCT}%")
        c2.metric("R:R",          f"1:{trader.RR_RATIO}")
        c3.metric("Candles",      strategy.CANDLE_COUNT)
        c4.metric("Check every",  "15 min")

        st.caption(
            "Optimised: 5yr Dukascopy H1 grid search | "
            "5% risk + 1.5 R:R → +239%/yr, 30.6% max DD (703 trades/5yr) | "
            "~2-3 trades/week | Session: 07:00-21:00 UTC"
        )


def _render_last_check():
    """Show next/last check time."""
    now = datetime.now(timezone.utc)
    mins_to_next = REFRESH_INTERVAL // 60 - (now.minute % (REFRESH_INTERVAL // 60))
    st.caption(f"Next signal check in ~{mins_to_next} min | UTC: {now.strftime('%H:%M:%S')}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title=BOT_NAME,
        page_icon="gold",
        layout="wide",
    )

    equity = trader.get_equity()
    stats  = trader.get_stats()

    _render_header(equity, stats)
    st.divider()

    col_left, col_right = st.columns([2, 1])

    with col_left:
        _render_equity_chart()
        st.subheader("Trade History")
        _render_trade_history()

    with col_right:
        _render_open_position()
        st.divider()
        _render_config()
        _render_last_check()

    # Auto-refresh every 2 minutes
    time.sleep(0.1)
    st.markdown(
        '<meta http-equiv="refresh" content="120">',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
