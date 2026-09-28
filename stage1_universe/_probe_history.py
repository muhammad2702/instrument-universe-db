"""Validate historical bar fetching (ADJUSTED_LAST vs TRADES) on a few known
instruments before committing to a 733-instrument pull. Not part of the pipeline."""
from allweather.db.schema import get_connection
from allweather.stage1_universe.ibkr_client import IBKRUniverseClient


def main():
    conn = get_connection()
    # pick a few well-known liquid names actually in our universe
    rows = conn.execute("""
        SELECT conid, symbol, sec_type FROM instruments
        WHERE symbol IN ('AAPL','SPY','TLT','GLD') LIMIT 10
    """).fetchall()
    print("Test targets:", rows)

    client = IBKRUniverseClient()
    client.connect()
    try:
        for conid, symbol, sec_type in rows:
            for wts in ("ADJUSTED_LAST", "TRADES"):
                bars, errs = client.get_historical_bars(
                    conid, sec_type=sec_type, duration="1 Y", bar_size="1 day",
                    what_to_show=wts, timeout=20,
                )
                real_errs = [e for e in errs if e[1] != 162]
                print(f"{symbol:<6} {wts:<15} n_bars={len(bars):<5} errs={real_errs}")
                if bars:
                    b0, bN = bars[0], bars[-1]
                    print(f"   first: {b0.date} close={b0.close}   last: {bN.date} close={bN.close}")
    finally:
        client.disconnect()
        conn.close()


if __name__ == "__main__":
    main()
