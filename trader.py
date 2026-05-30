"""
Paper Trade Manager — SQLite-backed journal, equity tracking, position management.

All trades are paper (simulated). No real broker connection.
P&L calculated as: risk_amount × R_multiple
  Win:  +risk × RR_RATIO
  Loss: -risk
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "1000.0"))
RISK_PCT         = float(os.getenv("RISK_PCT", "5.0"))    # optimised: 5yr Dukascopy H1
RR_RATIO         = float(os.getenv("RR_RATIO", "1.5"))    # 1.5 R:R beats 2.0 on this strategy
DB_PATH          = os.getenv("DB_PATH", "/data/trades.db")


@dataclass
class PaperTrade:
    id:          Optional[int]
    direction:   str        # buy / sell
    entry:       float
    sl:          float
    tp:          float
    risk_amount: float      # $ at risk
    open_time:   datetime
    close_time:  Optional[datetime] = None
    close_price: Optional[float]    = None
    pnl:         Optional[float]    = None
    status:      str                = "open"   # open / won / lost


# ── DB setup ─────────────────────────────────────────────────────────────────

def _ensure_db_dir():
    """Create DB directory if it doesn't exist (Railway volume may not pre-create)."""
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _conn():
    _ensure_db_dir()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                direction   TEXT    NOT NULL,
                entry       REAL    NOT NULL,
                sl          REAL    NOT NULL,
                tp          REAL    NOT NULL,
                risk_amount REAL    NOT NULL,
                open_time   TEXT    NOT NULL,
                close_time  TEXT,
                close_price REAL,
                pnl         REAL,
                status      TEXT    DEFAULT 'open'
            );
            CREATE TABLE IF NOT EXISTS equity_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT    NOT NULL,
                equity  REAL    NOT NULL,
                event   TEXT
            );
            CREATE TABLE IF NOT EXISTS signal_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT    NOT NULL,
                direction TEXT,
                entry     REAL,
                sl        REAL,
                tp        REAL,
                action    TEXT    -- 'opened' / 'no_position' / 'skipped'
            );
        """)
    log.info("DB ready at %s", DB_PATH)


# ── Equity ────────────────────────────────────────────────────────────────────

def get_equity() -> float:
    """Current equity = starting capital + sum of all closed P&L."""
    with _conn() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status IN ('won','lost')"
        ).fetchone()
    return round(STARTING_CAPITAL + (row[0] or 0), 2)


def _log_equity(equity: float, event: str = ""):
    with _conn() as con:
        con.execute(
            "INSERT INTO equity_log (ts, equity, event) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), equity, event),
        )


# ── Open / close trades ───────────────────────────────────────────────────────

def has_open_trade() -> bool:
    with _conn() as con:
        n = con.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    return n > 0


def open_trade(signal: dict) -> PaperTrade:
    """Open a paper trade from a signal dict. Returns the new trade."""
    equity      = get_equity()
    risk_amount = round(equity * (RISK_PCT / 100), 4)

    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO trades (direction, entry, sl, tp, risk_amount, open_time, status)
            VALUES (?, ?, ?, ?, ?, ?, 'open')
        """, (signal["direction"], signal["entry"], signal["sl"],
              signal["tp"], risk_amount, now))
        trade_id = cur.lastrowid
        con.execute(
            "INSERT INTO signal_log (ts, direction, entry, sl, tp, action) VALUES (?,?,?,?,?,?)",
            (now, signal["direction"], signal["entry"], signal["sl"],
             signal["tp"], "opened"),
        )

    log.info("Paper %s opened | entry=%.2f SL=%.2f TP=%.2f risk=$%.2f",
             signal["direction"], signal["entry"], signal["sl"],
             signal["tp"], risk_amount)
    return PaperTrade(
        id=trade_id, direction=signal["direction"],
        entry=signal["entry"], sl=signal["sl"], tp=signal["tp"],
        risk_amount=risk_amount,
        open_time=datetime.fromisoformat(now),
    )


def check_and_close_positions(bar_high: float, bar_low: float) -> Optional[dict]:
    """
    Check open positions against bar H/L. Close if SL or TP hit.
    Returns close event dict or None.
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trades WHERE status='open'"
        ).fetchall()

    for row in rows:
        d    = row["direction"]
        sl   = row["sl"]
        tp   = row["tp"]
        risk = row["risk_amount"]

        sl_hit = bar_low  <= sl if d == "buy" else bar_high >= sl
        tp_hit = bar_high >= tp if d == "buy" else bar_low  <= tp

        if sl_hit or tp_hit:
            win       = tp_hit and not sl_hit
            pnl       = round(risk * RR_RATIO if win else -risk, 4)
            close_px  = tp if win else sl
            status    = "won" if win else "lost"
            now       = datetime.now(timezone.utc).isoformat()

            with _conn() as con:
                con.execute("""
                    UPDATE trades
                    SET status=?, close_time=?, close_price=?, pnl=?
                    WHERE id=?
                """, (status, now, close_px, pnl, row["id"]))

            new_equity = get_equity()
            _log_equity(new_equity, f"trade_{status}")

            log.info("Paper %s %s | entry=%.2f close=%.2f pnl=$%+.2f equity=$%.2f",
                     d, status.upper(), row["entry"], close_px, pnl, new_equity)
            return {
                "trade_id": row["id"],
                "direction": d,
                "status": status,
                "entry": row["entry"],
                "close_price": close_px,
                "pnl": pnl,
                "equity": new_equity,
            }
    return None


# ── Queries ───────────────────────────────────────────────────────────────────

def get_open_trade() -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_all_trades() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trades WHERE status IN ('won','lost') ORDER BY open_time DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_equity_history() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT ts, equity FROM equity_log ORDER BY ts"
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    trades = get_all_trades()
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "profit_factor": 0.0,
            "net_pnl": 0.0, "expectancy": 0.0,
        }
    n     = len(trades)
    wins  = sum(1 for t in trades if t["status"] == "won")
    pnls  = [t["pnl"] for t in trades if t["pnl"] is not None]
    gp    = sum(p for p in pnls if p > 0)
    gl    = abs(sum(p for p in pnls if p < 0))
    return {
        "total":         n,
        "wins":          wins,
        "losses":        n - wins,
        "win_rate":      round(wins / n * 100, 1) if n else 0.0,
        "profit_factor": round(gp / gl, 3) if gl else float("inf"),
        "net_pnl":       round(sum(pnls), 2),
        "expectancy":    round(sum(pnls) / n, 2) if n else 0.0,
    }
