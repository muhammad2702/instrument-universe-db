"""
Stage 1 orchestrator: universe construction via IBKR scanners + SEC EDGAR
delisted-filing sourcing, persisted to SQLite, with a diagnostic report.

Run: python -m allweather.stage1_universe.run_stage1
"""
import json
from datetime import datetime, timedelta, timezone

from allweather.config import DB_PATH, REPORTS_DIR
from allweather.db.schema import get_connection, init_db
from allweather.stage1_universe.ibkr_client import IBKRUniverseClient
from allweather.stage1_universe.scan_definitions import SCAN_DEFS, ASSET_CLASS_MACRO_HINT
from allweather.stage1_universe.delisted_sec import fetch_delisted_filings
from allweather.stage1_universe.enrich import enrich_all

DELISTED_LOOKBACK_YEARS = 10


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_instrument(conn, row, asset_class: str, ts: str):
    conn.execute("""
        INSERT INTO instruments (conid, symbol, sec_type, exchange, primary_exchange,
            currency, local_symbol, trading_class, long_name, industry, category,
            subcategory, asset_class, quadrant_tag, status, first_seen_utc, last_seen_utc, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,'active',?,?,'ibkr_scanner')
        ON CONFLICT(conid) DO UPDATE SET
            last_seen_utc=excluded.last_seen_utc,
            symbol=excluded.symbol,
            long_name=CASE WHEN excluded.long_name != '' THEN excluded.long_name ELSE instruments.long_name END
    """, (row.conid, row.symbol, row.sec_type, row.exchange, row.primary_exchange,
          row.currency, row.local_symbol, row.trading_class, row.long_name,
          row.industry, row.category, row.subcategory, asset_class, ts, ts))


def run_scans(conn, client: IBKRUniverseClient) -> list[dict]:
    scan_summaries = []
    for sd in SCAN_DEFS:
        ts = now_utc()
        rows, errs = client.run_scan(
            instrument=sd.instrument, location_code=sd.location_code,
            scan_code=sd.scan_code, num_rows=sd.num_rows, filters=sd.filters,
            above_price=sd.above_price, below_price=sd.below_price,
            above_avg_volume=sd.above_avg_volume, market_cap_above=sd.market_cap_above,
        )
        filter_desc = [(t.tag, t.value) for t in (sd.filters or [])]
        if sd.above_price is not None or sd.below_price is not None:
            filter_desc.append(("priceBand", f"{sd.above_price}-{sd.below_price}"))
        filters_json = json.dumps(filter_desc)
        cur = conn.execute("""
            INSERT INTO scan_runs (run_timestamp_utc, instrument, location_code, scan_code,
                filters_json, asset_class, quadrant_tag, result_count, error)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (ts, sd.instrument, sd.location_code, sd.scan_code, filters_json,
              sd.asset_class, ASSET_CLASS_MACRO_HINT.get(sd.asset_class, ""),
              len(rows), "; ".join(f"{c}:{m}" for _, c, m in errs) if errs and not rows else None))
        scan_run_id = cur.lastrowid

        for row in rows:
            upsert_instrument(conn, row, sd.asset_class, ts)
            conn.execute("""
                INSERT OR REPLACE INTO scan_results (scan_run_id, conid, rank)
                VALUES (?,?,?)
            """, (scan_run_id, row.conid, row.rank))

        conn.commit()
        status = "OK" if rows else ("EMPTY/" + "; ".join(f"{c}" for _, c, m in errs))
        print(f"  scan[{sd.asset_class:<22}] {sd.instrument:<10} {sd.location_code:<10} "
              f"scanCode={sd.scan_code:<12} filters={filters_json:<30} -> {len(rows):>3} rows  {status}")
        scan_summaries.append({
            "asset_class": sd.asset_class, "instrument": sd.instrument,
            "location_code": sd.location_code, "n_rows": len(rows),
            "errors": [(c, m) for _, c, m in errs],
        })
    return scan_summaries


def run_delisted_sourcing(conn) -> dict:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=365 * DELISTED_LOOKBACK_YEARS)
    print(f"\nFetching SEC EDGAR Form 25 / 25-NSE filings {start} -> {end} ...")
    records, stats = fetch_delisted_filings(start, end)
    ts = now_utc()
    inserted = 0
    for r in records:
        cur = conn.execute("""
            INSERT OR IGNORE INTO delisted_instruments
                (company_name, cik, form_type, filing_date, source_url, ingested_utc, notes)
            VALUES (?,?,?,?,?,?,?)
        """, (r["company_name"], r["cik"], r["form_type"], r["filing_date"],
              r["source_url"], ts,
              f"ticker_guess={r['ticker_guess']}" if r["ticker_guess"] else "ticker_guess=NONE"))
        inserted += cur.rowcount
    conn.commit()
    print(f"  EDGAR stats: {stats}")
    print(f"  Inserted {inserted} new delisted_instruments rows "
          f"({len(records)} fetched, dedup on cik+form+date).")
    return {"stats": stats, "fetched": len(records), "inserted": inserted,
            "lookback_start": start.isoformat(), "lookback_end": end.isoformat()}


def build_report(conn, scan_summaries, delisted_info, enrich_stats) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("STAGE 1 -- UNIVERSE CONSTRUCTION DIAGNOSTIC REPORT")
    lines.append(f"Generated: {now_utc()}")
    lines.append(f"DB: {DB_PATH}")
    lines.append("=" * 78)

    lines.append("\n--- Scanner runs ---")
    for s in scan_summaries:
        err_str = f"  ERRORS: {s['errors']}" if s["errors"] else ""
        lines.append(f"  {s['asset_class']:<24} {s['instrument']:<10} {s['location_code']:<10} "
                      f"n={s['n_rows']:<4}{err_str}")

    lines.append("\n--- Instruments by asset_class (distinct conid) ---")
    for ac, n in conn.execute("""
            SELECT asset_class, COUNT(*) AS n FROM instruments
            GROUP BY asset_class ORDER BY n DESC
        """):
        lines.append(f"  {ac:<24} {n}")

    total_instruments = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    lines.append(f"\nTotal distinct instruments in universe: {total_instruments}")
    lines.append("  NOTE: asset_class is scan-order-dependent, not type-dependent -- a conid "
                  "keeps the asset_class of whichever scan found it FIRST (ON CONFLICT does not "
                  "overwrite it). E.g. SLV/TQQQ/SOXL surface in both the STK.US and ETF.EQ.US "
                  "scans and land under 'equity_stock' simply because that scan ran first. This "
                  "is a placeholder bucketing for Stage 1 diagnostics only -- real asset-class / "
                  "quadrant classification belongs to Stage 3 (feature matrix) and Stage 5 "
                  "(regime beta), not a scan-order label. Do not treat this column as authoritative.")

    lines.append("\n--- Sample rows (5 per asset_class) ---")
    for ac, in conn.execute("SELECT DISTINCT asset_class FROM instruments ORDER BY asset_class"):
        lines.append(f"\n  [{ac}]  ({ASSET_CLASS_MACRO_HINT.get(ac, '')})")
        for row in conn.execute("""
                SELECT symbol, currency, exchange, long_name, is_leveraged_or_inverse
                FROM instruments WHERE asset_class = ? ORDER BY conid LIMIT 5
            """, (ac,)):
            lev = " [LEV/INV]" if row[4] else ""
            lines.append(f"    {row[0]:<10} {row[1]:<5} {row[2]:<8} {row[3][:40]}{lev}")

    lines.append("\n--- Contract-details enrichment (reqContractDetails) ---")
    lines.append(f"  {enrich_stats}")
    n_lev = conn.execute("SELECT COUNT(*) FROM instruments WHERE is_leveraged_or_inverse=1").fetchone()[0]
    total_instr = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    lines.append(f"\n  Leveraged/inverse-flagged instruments: {n_lev} / {total_instr} "
                 f"({100 * n_lev / total_instr:.0f}%) -- detected via a keyword match "
                 "(2X/3X, Ultra(Short/Pro), Direxion Daily, ...) on the enriched long_name. "
                 "NOT excluded from the universe -- Stage 1 sources, it does not curate. "
                 "Flagged because the pipeline's stated objective is geometric-mean/Kelly, "
                 "and daily-reset leveraged ETPs have path-dependent volatility decay that "
                 "makes their long-run geometric mean structurally different from a simple "
                 "multiple of the underlying. Stage 6 must treat this flag as an explicit "
                 "constraint decision (include/exclude/cap), not ignore it.")
    lines.append("  By asset_class:")
    for ac, n, lev in conn.execute("""
            SELECT asset_class, COUNT(*), SUM(is_leveraged_or_inverse)
            FROM instruments GROUP BY asset_class ORDER BY COUNT(*) DESC
        """):
        lines.append(f"    {ac:<24} n={n:<5} leveraged/inverse={lev}")

    lines.append("\n--- Delisted instruments (SEC EDGAR Form 25 / 25-NSE) ---")
    lines.append(f"  Lookback window: {delisted_info['lookback_start']} -> {delisted_info['lookback_end']}")
    lines.append(f"  EDGAR-reported availability vs pulled: {delisted_info['stats']}")
    lines.append(f"  Rows inserted this run: {delisted_info['inserted']}")
    n_delisted = conn.execute("SELECT COUNT(*) FROM delisted_instruments").fetchone()[0]
    n_with_ticker = conn.execute(
        "SELECT COUNT(*) FROM delisted_instruments WHERE notes LIKE 'ticker_guess=%' "
        "AND notes NOT LIKE '%NONE'").fetchone()[0]
    lines.append(f"  Total delisted_instruments rows in DB: {n_delisted} "
                  f"({n_with_ticker} with a parsed ticker guess, {n_delisted - n_with_ticker} name/CIK only)")

    lines.append("\n" + "!" * 78)
    lines.append("SURVIVORSHIP BIAS -- EXPLICIT STATEMENT (per pipeline requirement)")
    lines.append("!" * 78)
    lines.append("""
  The instruments table above is sourced ENTIRELY from live IBKR scanners and
  therefore contains ONLY currently-listed, currently-tradeable contracts.
  This is a liquidity-and-listedness-filtered subset of "all IBKR-tradeable
  instruments," not the full universe -- there is no IBKR endpoint that
  enumerates the full universe, and this pipeline does not pretend otherwise.

  Any backtest run only against the `instruments` table inherits full
  survivorship bias: names that went bankrupt, were acquired, delisted, or
  had their ETF/fund liquidated are absent, which mechanically inflates
  historical mean return and Sharpe versus what a real-time investor would
  have experienced.

  Partial mitigation implemented: the `delisted_instruments` table sources
  company name + CIK + filing date for US equities that filed SEC Form 25 /
  25-NSE (delisting notification) going back to """ +
                 f"{DELISTED_LOOKBACK_YEARS} years" + """. This is NAME
  sourcing only -- it does not, and cannot via this method, supply PRICE
  HISTORY for those names (IBKR will not serve bars for dead conids, and no
  price vendor is wired in yet), so it does NOT by itself remove survivorship
  bias from any return-based statistic downstream. It also does not cover:
    - delistings before 2001 (EDGAR full text search coverage start)
    - futures contract expiries/rolls
    - closed/liquidated ETFs that didn't file Form 25
    - non-US-listed delistings
  Treat every downstream backtest as survivorship-biased until a priced
  delisted-history vendor is integrated, and say so in any output that
  quotes a historical return statistic.
""")

    report = "\n".join(lines)
    out_path = REPORTS_DIR / f"stage1_report_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.txt"
    out_path.write_text(report, encoding="utf-8")
    latest_path = REPORTS_DIR / "stage1_report_latest.txt"
    latest_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {out_path}")
    return report


def main():
    init_db()
    conn = get_connection()
    client = IBKRUniverseClient()
    client.connect()
    try:
        print("\nRunning scanner subscriptions...")
        scan_summaries = run_scans(conn, client)
        print("\nEnriching instruments via reqContractDetails...")
        enrich_stats = enrich_all(conn, client)
    finally:
        client.disconnect()

    delisted_info = run_delisted_sourcing(conn)
    report = build_report(conn, scan_summaries, delisted_info, enrich_stats)
    conn.close()
    print("\n" + report[-2000:])  # tail so the bias statement always shows in terminal


if __name__ == "__main__":
    main()
