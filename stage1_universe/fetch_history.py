"""
Historical daily bar ingestion for every instrument in the universe.

Uses whatToShow=ADJUSTED_LAST (split + dividend adjusted), validated against
raw TRADES on AAPL/SPY/TLT/GLD before this was wired in (see
_probe_history.py) -- unadjusted closes would silently bias every mean/vol
statistic downstream for any dividend-paying name.

Idempotent/resumable: only fetches instruments with history_fetched_utc IS
NULL, so a killed run can just be restarted.
"""
import time
from datetime import datetime, timezone

from allweather.db.schema import get_connection
from allweather.stage1_universe.ibkr_client import IBKRUniverseClient

DURATION = "20 Y"
BAR_SIZE = "1 day"
WHAT_TO_SHOW = "ADJUSTED_LAST"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_all(conn, client: IBKRUniverseClient, only_missing: bool = True) -> dict:
    where = "WHERE history_fetched_utc IS NULL" if only_missing else ""
    rows = conn.execute(f"""
        SELECT conid, symbol, sec_type, exchange, currency FROM instruments {where}
    """).fetchall()
    print(f"Fetching {DURATION} of {BAR_SIZE} bars ({WHAT_TO_SHOW}) for {len(rows)} instruments...")

    n_ok, n_empty, n_err = 0, 0, 0
    for i, (conid, symbol, sec_type, exchange, currency) in enumerate(rows):
        bars, errs = client.get_historical_bars(
            conid, exchange="SMART", currency=currency or "USD",
            sec_type=sec_type or "STK", duration=DURATION, bar_size=BAR_SIZE,
            what_to_show=WHAT_TO_SHOW, timeout=60,
        )
        ts = now_utc()
        real_errs = [e for e in errs if e[1] != 162]
        if bars:
            for b in bars:
                conn.execute("""
                    INSERT OR REPLACE INTO daily_bars
                        (conid, bar_date, open, high, low, close, volume, wap, bar_count, what_to_show)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (conid, b.date, float(b.open), float(b.high), float(b.low), float(b.close),
                      float(b.volume), float(b.wap), int(b.barCount), WHAT_TO_SHOW))
            conn.execute("""
                UPDATE instruments SET history_fetched_utc=?, history_bar_count=?, history_error=NULL
                WHERE conid=?
            """, (ts, len(bars), conid))
            n_ok += 1
        else:
            err_str = "; ".join(f"{c}:{m}" for _, c, m in real_errs) if real_errs else "no bars, no error"
            conn.execute("""
                UPDATE instruments SET history_fetched_utc=?, history_bar_count=0, history_error=?
                WHERE conid=?
            """, (ts, err_str, conid))
            n_empty += 1
            print(f"  [{i}] {symbol} ({conid}): EMPTY -- {err_str}")
        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"  {i + 1}/{len(rows)}...")

    conn.commit()
    stats = {"total": len(rows), "ok": n_ok, "empty_or_error": n_empty}
    print(f"History fetch done: {stats}")
    return stats


def main():
    conn = get_connection()
    client = IBKRUniverseClient()
    client.connect()
    try:
        fetch_all(conn, client)
    finally:
        client.disconnect()
        conn.close()


if __name__ == "__main__":
    main()
