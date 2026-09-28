"""
Concrete scanner subscriptions for Stage 1, chosen only from instrument /
locationCode / scanCode / filter combinations confirmed valid against the
live reqScannerParameters() response (allweather/data/scanner_parameters.xml)
AND empirically tested against this account (see _probe_scancodes.py output).
Nothing here is guessed.

Empirical entitlement map for this account (tested 2026-09-11):
  WORKS  -- pure trade-activity scanCodes (volume/price/trade-count based):
            MOST_ACTIVE, MOST_ACTIVE_USD, TOP_PERC_GAIN, TOP_PERC_LOSE,
            TOP_TRADE_COUNT, TOP_TRADE_RATE, HOT_BY_VOLUME, HOT_BY_PRICE,
            TOP_VOLUME_RATE, HIGH_STVOLUME_5MIN -- across STK.US, ETF.EQ.US,
            ETF.FI.US.
  BLOCKED -- anything needing a historical-bar/reference snapshot (52w/26w
            hi-lo, open-gap) -> error 165 "no items retrieved"; anything
            fundamentals-based, IB-native or Refinitiv (dividend yield, P/E,
            growth rate, ROE) -> error 165 or "Scan code X is not allowed";
            Lipper fund analytics (ETF) -> error 165; all FUT.US scans and
            BOND.GOVT.US -> error 492 / 165 (see BLOCKED_SCAN_DEFS below).
            These are account entitlement gaps, not code bugs.

To widen the equity universe past a mega-cap-only "most active" slice (per
explicit instruction: "opportunities exist" outside the most liquid names,
"fetch everything"), STK.US is additionally sliced by price band on the
three scan codes most likely to surface names outside the top-of-book
mega-caps (MOST_ACTIVE, TOP_PERC_GAIN, TOP_PERC_LOSE) -- a $0-5 penny/micro
band, $5-20, $20-100, and $100+ each get their own top-50 ranking, so a
volatile $8 stock and a quiet $400 mega-cap aren't competing for the same
50 slots.

asset_class is a coarse macro-asset-class bucket, NOT a quadrant placement.
True growth/inflation quadrant placement is a regression against realized
regime returns (Stage 5) and is intentionally left null here -- tagging it
now from the instrument's name/category would be exactly the kind of
unvalidated label the ROLE instructions warn against.
"""
from dataclasses import dataclass
from ibapi.tag_value import TagValue


@dataclass
class ScanDef:
    instrument: str
    location_code: str
    asset_class: str
    scan_code: str = "MOST_ACTIVE"
    num_rows: int = 50
    filters: list[TagValue] | None = None
    above_price: float | None = None
    below_price: float | None = None
    above_avg_volume: int | None = None
    market_cap_above: float | None = None


# Confirmed working on this account across STK.US / ETF.EQ.US / ETF.FI.US.
WORKING_SCAN_CODES = [
    "MOST_ACTIVE", "MOST_ACTIVE_USD", "TOP_PERC_GAIN", "TOP_PERC_LOSE",
    "TOP_TRADE_COUNT", "TOP_TRADE_RATE", "HOT_BY_VOLUME", "HOT_BY_PRICE",
    "TOP_VOLUME_RATE", "HIGH_STVOLUME_5MIN",
]

BUCKETS = [
    ("STK", "STK.US", "equity_stock"),
    ("ETF.EQ.US", "ETF.EQ.US", "equity_etf"),
    ("ETF.FI.US", "ETF.FI.US", "fixed_income_etf"),
]

PRICE_BANDS = [
    (None, 5.0),
    (5.0, 20.0),
    (20.0, 100.0),
    (100.0, None),
]
PRICE_BAND_SCAN_CODES = ["MOST_ACTIVE", "TOP_PERC_GAIN", "TOP_PERC_LOSE"]

SCAN_DEFS: list[ScanDef] = []

for instrument, location, asset_class in BUCKETS:
    for code in WORKING_SCAN_CODES:
        SCAN_DEFS.append(ScanDef(instrument, location, asset_class, scan_code=code))

for above, below in PRICE_BANDS:
    for code in PRICE_BAND_SCAN_CODES:
        SCAN_DEFS.append(ScanDef("STK", "STK.US", "equity_stock", scan_code=code,
                                  above_price=above, below_price=below))

# --- BLOCKED on this account: confirmed via direct API testing, not a code bug ---
# FUT.US (and every per-exchange location tried: GLOBEX, ECBOT, NYMEX, CFE,
# CMECRYPTO) -> error 492, missing real-time futures market data
# entitlements. BOND.GOVT/BOND.GOVT.US -> error 165 "no items retrieved",
# same pattern as marketCapAbove, likely missing a bond-data entitlement.
# Re-enable once the account has the relevant IBKR market data subscriptions;
# do not silently drop this -- it is why FUT.US instruments are absent
# from `instruments` in this run's report.
BLOCKED_SCAN_DEFS: list[ScanDef] = [
    ScanDef("BOND.GOVT", "BOND.GOVT.US", "govt_bond"),
    ScanDef("FUT.US", "FUT.US", "equity_index_future",
            filters=[TagValue("prodCatIs", "Equity Index")]),
    ScanDef("FUT.US", "FUT.US", "rates_future",
            filters=[TagValue("prodCatIs", "Fixed Income")]),
    ScanDef("FUT.US", "FUT.US", "metals_future",
            filters=[TagValue("prodCatIs", "Metal")]),
    ScanDef("FUT.US", "FUT.US", "energy_future",
            filters=[TagValue("prodCatIs", "Energy")]),
    ScanDef("FUT.US", "FUT.US", "agriculture_future",
            filters=[TagValue("prodCatIs", "Agriculture")]),
    ScanDef("FUT.US", "FUT.US", "commodity_index_future",
            filters=[TagValue("prodCatIs", "Commodity Index")]),
    ScanDef("FUT.US", "FUT.US", "fx_future",
            filters=[TagValue("prodCatIs", "Foreign Exchange")]),
]

ASSET_CLASS_MACRO_HINT = {
    "equity_stock": "growth (broad cross-section, sub-sector unclassified; "
                     "includes leveraged/inverse ETPs like TQQQ/SOXL which "
                     "IBKR classifies as secType=STK -- these are structurally "
                     "unsuitable for a geometric-mean/Kelly objective due to "
                     "daily-reset volatility decay and MUST be explicitly "
                     "flagged or excluded in Stage 6, not silently included)",
    "equity_etf": "growth / mixed -- includes commodity & gold trust ETFs "
                  "(GLD, SLV, DBC, ... are secType=STK here, not FUT)",
    "fixed_income_etf": "rates / duration / credit (mixed -- see category field)",
    "govt_bond": "rates / duration",
    "equity_index_future": "growth",
    "rates_future": "rates / duration",
    "metals_future": "inflation / commodity (includes gold+industrial metals, "
                      "not yet split)",
    "energy_future": "inflation / commodity",
    "agriculture_future": "inflation / commodity",
    "commodity_index_future": "inflation / commodity (broad basket)",
    "fx_future": "fx / dollar",
}
