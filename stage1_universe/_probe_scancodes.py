"""One-off: test a broad sample of scanCodes across STK.US / ETF.EQ.US / ETF.FI.US
to see which are actually usable on this account. Not part of the pipeline."""
from allweather.stage1_universe.ibkr_client import IBKRUniverseClient

CANDIDATES = [
    # technical / trade-based (expect these to work, same tier as MOST_ACTIVE)
    "MOST_ACTIVE_USD", "TOP_PERC_GAIN", "TOP_PERC_LOSE", "TOP_TRADE_COUNT",
    "TOP_TRADE_RATE", "HOT_BY_VOLUME", "HOT_BY_PRICE", "TOP_VOLUME_RATE",
    "HIGH_VS_52W_HL", "LOW_VS_52W_HL", "HIGH_VS_26W_HL", "LOW_VS_26W_HL",
    "TOP_OPEN_PERC_GAIN", "TOP_OPEN_PERC_LOSE", "HIGH_OPEN_GAP", "LOW_OPEN_GAP",
    "HIGH_STVOLUME_5MIN",
    # IB-native fundamentals-ish (uncertain)
    "HIGH_DIVIDEND_YIELD_IB",
    # Refinitiv fundamentals (expect blocked, like marketCapAbove)
    "HIGH_PE_RATIO", "HIGH_GROWTH_RATE", "HIGH_RETURN_ON_EQUITY",
    # Lipper fund analytics (ETF only, expect maybe blocked)
    "SCAN_lipperTotalNetAssets_DESC",
]


def main():
    client = IBKRUniverseClient()
    client.connect()
    try:
        for instrument, loc in [("STK", "STK.US"), ("ETF.EQ.US", "ETF.EQ.US"), ("ETF.FI.US", "ETF.FI.US")]:
            print(f"\n=== instrument={instrument} location={loc} ===")
            for code in CANDIDATES:
                rows, errs = client.run_scan(instrument, loc, code, num_rows=10)
                real_errs = [e for e in errs if e[1] != 162]
                status = "OK" if rows else ("BLOCKED:" + str(real_errs) if real_errs else "EMPTY")
                print(f"  {code:<35} rows={len(rows):<3} {status}")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
