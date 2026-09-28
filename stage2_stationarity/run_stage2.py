"""
Stage 2 orchestrator: stationarity battery on log returns for every
instrument with sufficient history, with Benjamini-Hochberg FDR correction
per test family and the ADF/KPSS four-way classification.

Run: python -m allweather.stage2_stationarity.run_stage2

Emits:
  allweather/data/stationarity_results.parquet -- every statistic/p-value
    (raw and BH-adjusted) per asset
  allweather/reports/stage2_report_latest.txt -- diagnostic summary
"""
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

from allweather.config import DATA_DIR, REPORTS_DIR
from allweather.db.schema import get_connection
from allweather.stage2_stationarity.battery import MIN_OBS, _ALL_FIELDS
from allweather.stage2_stationarity.timeout_runner import TimeoutBatteryRunner

PVALUE_TEST_FAMILIES = ["dfgls_pvalue", "kpss_pvalue", "pp_pvalue", "za_pvalue",
                         "vr2_pvalue", "arch_lm_pvalue"]
ALPHA = 0.05


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_log_returns(conn, conid: int) -> np.ndarray:
    closes = [r[0] for r in conn.execute(
        "SELECT close FROM daily_bars WHERE conid=? ORDER BY bar_date", (conid,)
    ).fetchall()]
    closes = np.asarray(closes, dtype=float)
    if len(closes) < 2:
        return np.array([])
    return np.diff(np.log(closes))


def run_all(conn) -> pd.DataFrame:
    instruments = conn.execute(
        "SELECT conid, symbol, asset_class FROM instruments ORDER BY conid"
    ).fetchall()
    print(f"Running stationarity battery on {len(instruments)} instruments...")

    records = []
    t_start = time.time()
    runner = TimeoutBatteryRunner(timeout_s=45.0)
    try:
        for i, (conid, symbol, asset_class) in enumerate(instruments):
            log_ret = load_log_returns(conn, conid)
            result = runner.run(log_ret)
            if result["status"] != "ok" and any(f not in result for f in _ALL_FIELDS):
                result.update({f: (None if f.endswith("_error") or f == "za_break_idx" else np.nan)
                                for f in _ALL_FIELDS})
            result["conid"] = conid
            result["symbol"] = symbol
            result["asset_class"] = asset_class
            records.append(result)
            if result["status"] not in ("ok",) and not result["status"].startswith("insufficient_data"):
                print(f"  [{i}] {symbol} ({conid}): {result['status']}")
            if (i + 1) % 100 == 0:
                elapsed = time.time() - t_start
                print(f"  {i + 1}/{len(instruments)}  ({elapsed:.0f}s elapsed, "
                      f"~{elapsed / (i + 1) * (len(instruments) - i - 1):.0f}s remaining)")
    finally:
        runner.close()

    df = pd.DataFrame.from_records(records)
    print(f"Battery complete: {len(df)} rows in {time.time() - t_start:.0f}s")
    return df


def apply_fdr(df: pd.DataFrame) -> pd.DataFrame:
    """BH-FDR correction per test family, applied only to rows with a valid
    (non-NaN) raw p-value. Adjusted p-values for skipped/failed tests stay NaN."""
    for pcol in PVALUE_TEST_FAMILIES:
        adj_col = pcol.replace("_pvalue", "_pvalue_bh")
        if pcol not in df.columns:
            df[pcol] = np.nan
        df[adj_col] = np.nan
        valid = df[pcol].notna()
        if valid.sum() > 0:
            _, pvals_adj, _, _ = multipletests(df.loc[valid, pcol], alpha=ALPHA, method="fdr_bh")
            df.loc[valid, adj_col] = pvals_adj
    return df


def classify_adf_kpss(df: pd.DataFrame, use_adjusted: bool) -> pd.Series:
    """Four-way ADF(DFGLS)/KPSS classification. ADF H0=unit root,
    KPSS H0=stationary."""
    suffix = "_bh" if use_adjusted else ""
    adf_p = df[f"dfgls_pvalue{suffix}"]
    kpss_p = df[f"kpss_pvalue{suffix}"]

    adf_reject = adf_p < ALPHA        # rejects unit root -> looks stationary
    kpss_reject = kpss_p < ALPHA      # rejects stationarity -> looks unit-root

    out = pd.Series(index=df.index, dtype=object)
    out[:] = "uninformative_missing_data"
    both_valid = adf_p.notna() & kpss_p.notna()

    out[both_valid & adf_reject & ~kpss_reject] = "stationary_clean"
    out[both_valid & ~adf_reject & kpss_reject] = "unit_root_clean"
    out[both_valid & adf_reject & kpss_reject] = "conflicting_or_frac_integrated"
    out[both_valid & ~adf_reject & ~kpss_reject] = "uninformative_low_power"
    return out


def build_report(df: pd.DataFrame) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("STAGE 2 -- STATIONARITY BATTERY DIAGNOSTIC REPORT")
    lines.append(f"Generated: {now_utc()}")
    lines.append("=" * 78)

    n_total = len(df)
    n_ok = (df["status"] == "ok").sum()
    n_insufficient = n_total - n_ok
    lines.append(f"\nInstruments: {n_total} total, {n_ok} with sufficient data "
                 f"(n_obs >= MIN_OBS={MIN_OBS}), {n_insufficient} flagged insufficient_data.")

    lines.append("\n--- Per-test success rate (non-NaN p-value / instruments with sufficient data) ---")
    for pcol in PVALUE_TEST_FAMILIES:
        n_valid = df.loc[df["status"] == "ok", pcol].notna().sum()
        lines.append(f"  {pcol:<20} {n_valid}/{n_ok}")

    lines.append("\n--- ADF(DFGLS)/KPSS four-way classification (RAW p-values, alpha=0.05) ---")
    raw_class = classify_adf_kpss(df, use_adjusted=False)
    for cls, n in raw_class.value_counts().items():
        lines.append(f"  {cls:<32} {n}")

    lines.append("\n--- ADF(DFGLS)/KPSS four-way classification (BH-FDR-ADJUSTED p-values, alpha=0.05) ---")
    adj_class = classify_adf_kpss(df, use_adjusted=True)
    for cls, n in adj_class.value_counts().items():
        lines.append(f"  {cls:<32} {n}")

    lines.append("\n  NOTE: raw vs adjusted classification counts differing materially means the")
    lines.append("  raw-p-value crosstab above was meaningfully inflated by multiple-testing false")
    lines.append(f"  positives (expected: ~{ALPHA * n_ok:.0f} false rejections at alpha={ALPHA} "
                 f"across {n_ok} assets per test, by construction, before correction).")

    lines.append("\n--- ARCH effect (conditional heteroskedasticity) ---")
    ok = df[df["status"] == "ok"]
    n_arch = (ok["arch_lm_pvalue_bh"] < ALPHA).sum()
    lines.append(f"  {n_arch}/{len(ok)} instruments reject 'no ARCH effect' at BH-adjusted alpha={ALPHA}")
    lines.append("  This is the number that actually matters for covariance estimation: a large")
    lines.append("  fraction here means volatility clustering is pervasive and a single static")
    lines.append("  covariance matrix will be stale for most of the universe -- motivates the")
    lines.append("  regime-conditional covariance work in Stage 5, not a reason to drop assets now.")

    lines.append("\n--- Hurst exponent distribution (DFA, more robust than R/S at short lengths) ---")
    h = ok["hurst_dfa"].dropna()
    if len(h):
        lines.append(f"  n={len(h)}  mean={h.mean():.3f}  median={h.median():.3f}  "
                     f"std={h.std():.3f}  min={h.min():.3f}  max={h.max():.3f}")
        lines.append(f"  H < 0.45 (mean-reverting-ish): {(h < 0.45).sum()}")
        lines.append(f"  0.45 <= H <= 0.55 (~random walk): {((h >= 0.45) & (h <= 0.55)).sum()}")
        lines.append(f"  H > 0.55 (trending/long-memory-ish): {(h > 0.55).sum()}")

    lines.append("\n--- Sample flagged rows (conflicting/uninformative under BH-adjusted p-values) ---")
    df["_adj_class"] = adj_class
    flagged = df[df["_adj_class"].isin(["conflicting_or_frac_integrated", "uninformative_low_power"])]
    for _, row in flagged.head(10).iterrows():
        lines.append(f"  {row['symbol']:<10} class={row['_adj_class']:<32} "
                     f"dfgls_p={row['dfgls_pvalue']:.3f} kpss_p={row['kpss_pvalue']:.3f} "
                     f"n_obs={row['n_obs']}")
    if len(flagged) > 10:
        lines.append(f"  ... and {len(flagged) - 10} more (see parquet for full list)")

    lines.append("\n" + "!" * 78)
    lines.append("CRITICAL CAVEAT (per pipeline requirement) -- READ BEFORE USING THIS TABLE")
    lines.append("!" * 78)
    lines.append("""
  Stationarity is a DATA-QUALITY GATE, not an alpha filter. A stationary
  return series is not a profitable one, and an asset flagged
  'conflicting_or_frac_integrated' or 'uninformative_low_power' above is not
  thereby a bad investment -- it means this asset's statistical properties
  (regime-switching variance, a structural break, fractional integration,
  or just too little/too noisy data to tell) make a single static covariance
  estimate on its returns unreliable. That is a signal to downweight or
  special-case the asset in the covariance step (Stage 4), NOT a signal to
  select it in or out of the portfolio on return grounds. Selecting assets
  because they pass this battery would introduce selection bias into every
  downstream stage.

  Also note: ADF/PP/DFGLS overwhelmingly reject the unit-root null on
  returns (as expected -- returns are levels-stationary almost by
  construction; prices are I(1), returns are the differenced series). The
  informative tests in this battery are KPSS (confirmatory pair), ARCH-LM
  (variance stability -- the one that actually matters for Sigma), Zivot-
  Andrews (structural breaks), variance ratio (random-walk-in-PRICE check),
  and Hurst (long memory). Do not over-read the unit-root columns alone.
""")

    report = "\n".join(lines)
    out_path = REPORTS_DIR / f"stage2_report_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.txt"
    out_path.write_text(report, encoding="utf-8")
    latest_path = REPORTS_DIR / "stage2_report_latest.txt"
    latest_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {out_path}")
    return report


def main():
    conn = get_connection()
    df = run_all(conn)
    df = apply_fdr(df)

    out_path = DATA_DIR / "stationarity_results.parquet"
    df.to_parquet(out_path, index=False)
    print(f"Wrote {len(df)} rows to {out_path}")

    report = build_report(df)
    conn.close()
    print("\n" + report[-2500:])


if __name__ == "__main__":
    main()
