"""Ad-hoc debugging for Stage 1 scan issues. Not part of the pipeline."""
from ibapi.tag_value import TagValue
from allweather.stage1_universe.ibkr_client import IBKRUniverseClient


def main():
    client = IBKRUniverseClient()
    client.connect()
    try:
        # try delayed market data to see if it clears the 492 permission errors
        client.app.reqMarketDataType(3)  # 1=live 2=frozen 3=delayed 4=delayed-frozen

        print("\n--- STK.US MOST_ACTIVE, no filters ---")
        rows, errs = client.run_scan("STK", "STK.US", "MOST_ACTIVE", num_rows=10)
        print(f"rows={len(rows)} errs={errs}")

        print("\n--- STK.US MOST_ACTIVE, aboveVolume only ---")
        rows, errs = client.run_scan("STK", "STK.US", "MOST_ACTIVE", num_rows=10, above_avg_volume=500_000)
        print(f"rows={len(rows)} errs={errs}")

        print("\n--- ETF.EQ.US MOST_ACTIVE, no filters ---")
        rows, errs = client.run_scan("ETF.EQ.US", "ETF.EQ.US", "MOST_ACTIVE", num_rows=15)
        print(f"rows={len(rows)} errs={errs}")
        for r in rows[:15]:
            print(" ", r.symbol, r.currency, r.exchange)

        print("\n--- FUT.US MOST_ACTIVE, Equity Index, delayed data ---")
        rows, errs = client.run_scan("FUT.US", "FUT.US", "MOST_ACTIVE", num_rows=10,
                                      filters=[TagValue("prodCatIs", "Equity Index")])
        print(f"rows={len(rows)} errs={errs}")
        for r in rows[:10]:
            print(" ", r.symbol, r.currency, r.exchange, r.local_symbol)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
