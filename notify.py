"""Optional Telegram notifications. Set TELEGRAM_TOKEN + TELEGRAM_CHAT_ID in env."""

import logging
import os
import requests

log = logging.getLogger(__name__)
_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _send(msg: str):
    if not (_TOKEN and _CHAT_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            data={"chat_id": _CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as exc:
        log.debug("Telegram failed: %s", exc)


def bot_started(equity: float):
    _send(
        f"[XAUUSD 3-Candle STARTED]\n"
        f"Paper bot live on Railway\n"
        f"Capital: ${equity:,.2f}  Session: 07:00-21:00 UTC  Check: every 15min"
    )


def trade_opened(direction: str, entry: float, sl: float, tp: float, risk: float):
    label = "BUY" if direction == "buy" else "SELL"
    rr    = round(abs(tp - entry) / abs(entry - sl), 2) if abs(entry - sl) > 0 else 0
    _send(
        f"[PAPER {label}] XAUUSD opened\n"
        f"Entry: {entry:.2f}  SL: {sl:.2f}  TP: {tp:.2f}\n"
        f"Risk: ${risk:.2f}  R:R 1:{rr}"
    )


def trade_closed(direction: str, entry: float, close_px: float,
                 pnl: float, equity: float, status: str):
    emoji = "WIN" if status == "won" else "LOSS"
    _send(
        f"[PAPER {emoji}] XAUUSD {direction.upper()} closed\n"
        f"Entry: {entry:.2f}  Close: {close_px:.2f}\n"
        f"P&L: ${pnl:+.2f}  Equity: ${equity:,.2f}"
    )
