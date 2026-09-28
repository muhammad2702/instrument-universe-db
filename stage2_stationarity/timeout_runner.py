"""
Hard wall-clock timeout wrapper around run_battery, via a persistent
single-worker process pool.

Motivation: a live run on the full 733-instrument universe stalled for
~4.5 hours on a single pathological series (near-constant/thinly-traded
prices driving arch's lag-selection into a pathological slow path) before
eventually finishing that one instrument and continuing. Statistical
routines here are synchronous CPU-bound numpy/scipy code with no
cooperative cancellation point, so the only reliable way to bound one
instrument's runtime is OS-level: run it in a subprocess and kill the
subprocess if it overruns.

The pool is recreated after any timeout or worker crash, since a killed
worker leaves the pool unusable for further submissions.
"""
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError

from allweather.stage2_stationarity.battery import run_battery


def _battery_worker(log_returns):
    return run_battery(log_returns)


class TimeoutBatteryRunner:
    def __init__(self, timeout_s: float = 45.0):
        self.timeout_s = timeout_s
        self._executor = ProcessPoolExecutor(max_workers=1)

    def _restart(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._executor = ProcessPoolExecutor(max_workers=1)

    def run(self, log_returns) -> dict:
        n_obs = len(log_returns)
        future = self._executor.submit(_battery_worker, log_returns)
        try:
            return future.result(timeout=self.timeout_s)
        except FuturesTimeoutError:
            self._restart()
            return {"n_obs": n_obs, "status": f"timeout (>{self.timeout_s:.0f}s, worker killed)"}
        except Exception as e:
            self._restart()
            return {"n_obs": n_obs, "status": f"worker_crashed: {type(e).__name__}: {e}"}

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
