"""
Hurst exponent via classical rescaled-range (R/S) and detrended fluctuation
analysis (DFA). No third-party `hurst`/`nolds` package is installed in this
environment, and both estimators are short enough (~30 lines) that a vendored
dependency isn't worth it -- implemented directly against the standard
definitions (Hurst 1951; Peng et al. 1994).

Both return H via log-log OLS regression of a fluctuation measure against
window size across a log-spaced range of window sizes. H < 0.5 ->
mean-reverting, H = 0.5 -> random walk, H > 0.5 -> trending/long-memory.
"""
import numpy as np


def _log_spaced_window_sizes(n: int, min_size: int = 8, n_sizes: int = 20) -> np.ndarray:
    max_size = n // 2
    sizes = np.unique(np.logspace(np.log10(min_size), np.log10(max_size), n_sizes).astype(int))
    return sizes[sizes >= min_size]


def hurst_rs(series: np.ndarray) -> tuple[float, float]:
    """Rescaled-range Hurst exponent. Returns (H, r_squared_of_fit)."""
    x = np.asarray(series, dtype=float)
    n = len(x)
    sizes = _log_spaced_window_sizes(n)
    if len(sizes) < 4:
        return np.nan, np.nan

    rs_values = []
    valid_sizes = []
    for size in sizes:
        n_chunks = n // size
        if n_chunks < 1:
            continue
        chunk_rs = []
        for i in range(n_chunks):
            chunk = x[i * size:(i + 1) * size]
            mean = chunk.mean()
            dev = np.cumsum(chunk - mean)
            R = dev.max() - dev.min()
            S = chunk.std(ddof=0)
            if S > 0:
                chunk_rs.append(R / S)
        if chunk_rs:
            rs_values.append(np.mean(chunk_rs))
            valid_sizes.append(size)

    if len(valid_sizes) < 4:
        return np.nan, np.nan

    log_sizes = np.log(valid_sizes)
    log_rs = np.log(rs_values)
    slope, intercept = np.polyfit(log_sizes, log_rs, 1)
    fitted = slope * log_sizes + intercept
    ss_res = np.sum((log_rs - fitted) ** 2)
    ss_tot = np.sum((log_rs - log_rs.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return float(slope), float(r2)


def hurst_dfa(series: np.ndarray) -> tuple[float, float]:
    """Detrended fluctuation analysis Hurst exponent (order-1 detrending).
    Returns (H, r_squared_of_fit)."""
    x = np.asarray(series, dtype=float)
    n = len(x)
    profile = np.cumsum(x - x.mean())
    sizes = _log_spaced_window_sizes(n)
    if len(sizes) < 4:
        return np.nan, np.nan

    fluct = []
    valid_sizes = []
    for size in sizes:
        n_chunks = n // size
        if n_chunks < 1:
            continue
        rms_list = []
        t = np.arange(size)
        for i in range(n_chunks):
            chunk = profile[i * size:(i + 1) * size]
            coeffs = np.polyfit(t, chunk, 1)
            trend = coeffs[0] * t + coeffs[1]
            rms_list.append(np.sqrt(np.mean((chunk - trend) ** 2)))
        if rms_list:
            fluct.append(np.mean(rms_list))
            valid_sizes.append(size)

    if len(valid_sizes) < 4:
        return np.nan, np.nan

    log_sizes = np.log(valid_sizes)
    log_fluct = np.log(fluct)
    slope, intercept = np.polyfit(log_sizes, log_fluct, 1)
    fitted = slope * log_sizes + intercept
    ss_res = np.sum((log_fluct - fitted) ** 2)
    ss_tot = np.sum((log_fluct - log_fluct.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return float(slope), float(r2)
