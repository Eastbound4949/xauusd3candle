"""
Standalone bot worker for XAUUSD 3-Candle Breakout.
Runs independently of Streamlit — starts at container launch.
Signal check every 15 min via yfinance GC=F H1.
"""

import logging
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("worker")

import notify
import strategy
import trader

REFRESH_INTERVAL = 15 * 60   # 15 minutes


def _check_signals():
    df = strategy.fetch_bars()
    if df is None or df.empty:
        log.warning("No bar data — skipping tick")
        return

    latest_bar = df.iloc[-1]
    bar_high   = float(latest_bar["high"])
    bar_low    = float(latest_bar["low"])

    close_event = trader.check_and_close_positions(bar_high, bar_low)
    if close_event:
        notify.trade_closed(
            direction=close_event["direction"],
            entry=close_event["entry"],
            close_px=close_event["close_price"],
            pnl=close_event["pnl"],
            equity=close_event["equity"],
            status=close_event["status"],
        )

    if not trader.has_open_trade():
        sig = strategy.generate_signal(df)
        if sig:
            trade = trader.open_trade(sig)
            notify.trade_opened(
                direction=trade.direction,
                entry=trade.entry,
                sl=trade.sl,
                tp=trade.tp,
                risk=trade.risk_amount,
            )
            log.info("Signal: %s at %.2f", sig["direction"], sig["entry"])


def main():
    log.info("=" * 50)
    log.info("  XAUUSD 3-Candle Breakout Bot — Worker")
    log.info("  Signal check every 15 min (H1 yfinance)")
    log.info("=" * 50)

    trader.init_db()
    equity = trader.get_equity()
    trader._log_equity(equity, "startup")
    log.info("DB ready. Starting equity: $%.2f", equity)
    notify.bot_started(equity)

    def _shutdown(signum, frame):
        log.info("Worker shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        try:
            _check_signals()
        except Exception as exc:
            log.exception("Bot loop error: %s", exc)
        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
