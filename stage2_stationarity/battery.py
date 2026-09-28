"""
The Stage 2 stationarity battery, run per-instrument on log returns.

Per the pipeline spec: returns are almost always level-stationary, so the
real question is whether the *variance and dependence structure* is stable
(that's what breaks the covariance estimate downstream). The battery mixes
unit-root tests (on the level of returns, which should reject -- a sanity
check more than an interesting result), a random-walk test on the *price*
level via variance ratios, a structural-break test, an ARCH-effect test, and
long-memory (Hurst) estimators.

Every test is wrapped individually -- one failing test (e.g. KPSS erroring
on a degenerate series) must not lose the other six results for that asset.
"""
import warnings
import numpy as np

from arch.unitroot import DFGLS, PhillipsPerron, VarianceRatio
from statsmodels.tsa.stattools import kpss, zivot_andrews
from statsmodels.stats.diagnostic import het_arch

from allweather.stage2_stationarity.hurst import hurst_rs, hurst_dfa

MIN_OBS = 100

_ALL_FIELDS = [
    "dfgls_stat", "dfgls_pvalue", "dfgls_error",
    "kpss_stat", "kpss_pvalue", "kpss_error",
    "pp_stat", "pp_pvalue", "pp_error",
    "za_stat", "za_pvalue", "za_break_idx", "za_error",
    "vr2_stat", "vr2_pvalue", "vr2_error",
    "arch_lm_stat", "arch_lm_pvalue", "arch_lm_error",
    "hurst_rs", "hurst_rs_r2", "hurst_rs_error",
    "hurst_dfa", "hurst_dfa_r2", "hurst_dfa_error",
]


def _safe(fn, *args, **kwargs):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def run_battery(log_returns: np.ndarray) -> dict:
    """Runs the full battery on one log-return series. Returns a flat dict
    of stat/pvalue/error fields, always with the same keys regardless of
    which tests succeeded, so the results can be assembled into one table."""
    n = len(log_returns)
    out = {"n_obs": n, "status": "ok"}

    if n < MIN_OBS:
        out["status"] = f"insufficient_data (n={n} < MIN_OBS={MIN_OBS})"
        out.update({f: (None if f.endswith("_error") or f == "za_break_idx" else np.nan)
                     for f in _ALL_FIELDS})
        return out

    x = np.asarray(log_returns, dtype=float)

    # --- ADF-GLS (Elliott-Rothenberg-Stock), H0: has unit root ---
    # NOTE: arch.unitroot classes are lazily evaluated -- .stat/.pvalue access
    # is what actually runs the regression and can raise. Must happen INSIDE
    # the _safe() call or exceptions escape uncaught (this crashed a full run).
    res, err = _safe(lambda: (lambda r: (r.stat, r.pvalue))(DFGLS(x)))
    out["dfgls_stat"] = float(res[0]) if res else np.nan
    out["dfgls_pvalue"] = float(res[1]) if res else np.nan
    out["dfgls_error"] = err

    # --- KPSS, H0: is stationary ---
    res, err = _safe(kpss, x, nlags="auto")
    out["kpss_stat"] = float(res[0]) if res else np.nan
    out["kpss_pvalue"] = float(res[1]) if res else np.nan
    out["kpss_error"] = err

    # --- Phillips-Perron, H0: has unit root ---
    res, err = _safe(lambda: (lambda r: (r.stat, r.pvalue))(PhillipsPerron(x)))
    out["pp_stat"] = float(res[0]) if res else np.nan
    out["pp_pvalue"] = float(res[1]) if res else np.nan
    out["pp_error"] = err

    # --- Zivot-Andrews, H0: has unit root with a structural break ---
    res, err = _safe(zivot_andrews, x)
    out["za_stat"] = float(res[0]) if res else np.nan
    out["za_pvalue"] = float(res[1]) if res else np.nan
    out["za_break_idx"] = int(res[4]) if res else None
    out["za_error"] = err

    # --- Variance ratio (Lo-MacKinlay) at lag 2, H0: random walk ---
    res, err = _safe(lambda: (lambda r: (r.stat, r.pvalue))(VarianceRatio(x, lags=2)))
    out["vr2_stat"] = float(res[0]) if res else np.nan
    out["vr2_pvalue"] = float(res[1]) if res else np.nan
    out["vr2_error"] = err

    # --- ARCH-LM (Engle), H0: no ARCH effect ---
    res, err = _safe(het_arch, x)
    out["arch_lm_stat"] = float(res[0]) if res else np.nan
    out["arch_lm_pvalue"] = float(res[1]) if res else np.nan
    out["arch_lm_error"] = err

    # --- Hurst exponent (R/S and DFA) ---
    res, err = _safe(hurst_rs, x)
    out["hurst_rs"] = res[0] if res else np.nan
    out["hurst_rs_r2"] = res[1] if res else np.nan
    out["hurst_rs_error"] = err

    res, err = _safe(hurst_dfa, x)
    out["hurst_dfa"] = res[0] if res else np.nan
    out["hurst_dfa_r2"] = res[1] if res else np.nan
    out["hurst_dfa_error"] = err

    return out
