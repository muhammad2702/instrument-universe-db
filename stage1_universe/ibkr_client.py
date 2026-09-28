"""
Thin synchronous wrapper around ibapi for Stage 1 universe construction.

READ-ONLY. Only reqScannerParameters, reqScannerSubscription and
reqContractDetails are used. No order methods are implemented anywhere in
this module -- there is nothing here that *could* place a trade.
"""
import queue
import threading
import time
from dataclasses import dataclass, field

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.scanner import ScannerSubscription
from ibapi.tag_value import TagValue

from allweather.config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID

CONNECT_TIMEOUT_S = 10
SCAN_TIMEOUT_S = 20


@dataclass
class ScanRow:
    rank: int
    conid: int
    symbol: str
    sec_type: str
    exchange: str
    primary_exchange: str
    currency: str
    local_symbol: str
    trading_class: str
    long_name: str = ""
    industry: str = ""
    category: str = ""
    subcategory: str = ""


class _App(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.connected_event = threading.Event()
        self.next_valid_id = None
        self._scan_results: dict[int, list] = {}
        self._scan_done: dict[int, threading.Event] = {}
        self._scan_params_xml: str | None = None
        self._scan_params_event = threading.Event()
        self._errors: list[tuple] = []
        self._cd_results: dict[int, list] = {}
        self._cd_done: dict[int, threading.Event] = {}
        self._hist_results: dict[int, list] = {}
        self._hist_done: dict[int, threading.Event] = {}

    # --- connection lifecycle ---
    def nextValidId(self, orderId: int):
        self.next_valid_id = orderId
        self.connected_event.set()

    def error(self, reqId, errorTime, errorCode, errorString, advancedOrderRejectJson=""):
        # ibapi >=10.19 passes errorTime as 2nd positional arg; keep permissive signature.
        self._errors.append((reqId, errorCode, errorString))
        # 2104/2106/2158 etc are benign "data farm connection is OK" notices.
        if errorCode not in (2104, 2106, 2107, 2108, 2158, 2119):
            print(f"[IBKR error] reqId={reqId} code={errorCode} msg={errorString}")
        if reqId is not None and reqId in self._scan_done:
            self._scan_done[reqId].set()
        if reqId is not None and reqId in self._cd_done:
            self._cd_done[reqId].set()
        if reqId is not None and reqId in self._hist_done:
            self._hist_done[reqId].set()

    # --- historical bars ---
    def historicalData(self, reqId, bar):
        self._hist_results.setdefault(reqId, []).append(bar)

    def historicalDataEnd(self, reqId, start: str, end: str):
        if reqId in self._hist_done:
            self._hist_done[reqId].set()

    # --- contract details ---
    def contractDetails(self, reqId, contractDetails):
        self._cd_results.setdefault(reqId, []).append(contractDetails)

    def contractDetailsEnd(self, reqId):
        if reqId in self._cd_done:
            self._cd_done[reqId].set()

    # --- scanner parameters ---
    def scannerParameters(self, xml: str):
        self._scan_params_xml = xml
        self._scan_params_event.set()

    # --- scanner subscription ---
    def scannerData(self, reqId, rank, contractDetails, distance, benchmark, projection, legsStr):
        c = contractDetails.contract
        row = ScanRow(
            rank=rank,
            conid=c.conId,
            symbol=c.symbol,
            sec_type=c.secType,
            exchange=c.exchange,
            primary_exchange=c.primaryExchange,
            currency=c.currency,
            local_symbol=c.localSymbol,
            trading_class=c.tradingClass,
            long_name=contractDetails.longName or "",
            industry=contractDetails.industry or "",
            category=contractDetails.category or "",
            subcategory=contractDetails.subcategory or "",
        )
        self._scan_results.setdefault(reqId, []).append(row)

    def scannerDataEnd(self, reqId):
        if reqId in self._scan_done:
            self._scan_done[reqId].set()


class IBKRUniverseClient:
    """Synchronous facade. One instance = one connection, used serially."""

    def __init__(self, host=IBKR_HOST, port=IBKR_PORT, client_id=IBKR_CLIENT_ID):
        self.app = _App()
        self._thread = None
        self.host, self.port, self.client_id = host, port, client_id

    def connect(self):
        self.app.connect(self.host, self.port, self.client_id)
        self._thread = threading.Thread(target=self.app.run, daemon=True)
        self._thread.start()
        if not self.app.connected_event.wait(CONNECT_TIMEOUT_S):
            raise ConnectionError(
                f"Timed out connecting to IB Gateway at {self.host}:{self.port}. "
                "Is Gateway running and API access enabled for this client id?"
            )
        print(f"Connected to IB Gateway {self.host}:{self.port} "
              f"(nextValidId={self.app.next_valid_id})")

    def disconnect(self):
        self.app.disconnect()
        if self._thread:
            self._thread.join(timeout=5)

    def get_scanner_parameters_xml(self) -> str:
        self.app._scan_params_event.clear()
        self.app.reqScannerParameters()
        if not self.app._scan_params_event.wait(CONNECT_TIMEOUT_S):
            raise TimeoutError("reqScannerParameters timed out")
        return self.app._scan_params_xml

    def run_scan(self, instrument: str, location_code: str, scan_code: str,
                 num_rows: int = 50, filters: list[TagValue] | None = None,
                 above_price: float | None = None,
                 below_price: float | None = None,
                 above_avg_volume: int | None = None,
                 market_cap_above: float | None = None) -> tuple[list[ScanRow], list[tuple]]:
        """Runs one scanner subscription to completion, returns (rows, errors_seen)."""
        req_id = self.app.next_valid_id
        self.app.next_valid_id += 1

        sub = ScannerSubscription()
        sub.instrument = instrument
        sub.locationCode = location_code
        sub.scanCode = scan_code
        sub.numberOfRows = num_rows
        if above_price is not None:
            sub.abovePrice = above_price
        if below_price is not None:
            sub.belowPrice = below_price
        if above_avg_volume is not None:
            sub.aboveVolume = above_avg_volume
        if market_cap_above is not None:
            sub.marketCapAbove = market_cap_above

        self.app._scan_results[req_id] = []
        done = threading.Event()
        self.app._scan_done[req_id] = done
        errors_before = len(self.app._errors)

        self.app.reqScannerSubscription(req_id, sub, [], filters or [])
        finished = done.wait(SCAN_TIMEOUT_S)
        self.app.cancelScannerSubscription(req_id)

        rows = self.app._scan_results.get(req_id, [])
        errs = [e for e in self.app._errors[errors_before:] if e[0] == req_id]
        if not finished:
            errs.append((req_id, -1, "client-side timeout waiting for scannerDataEnd"))
        return rows, errs

    def get_contract_details(self, conid: int, timeout: float = 5.0):
        """Returns a list of ContractDetails for the given conid (read-only)."""
        req_id = self.app.next_valid_id
        self.app.next_valid_id += 1

        contract = Contract()
        contract.conId = conid
        contract.exchange = "SMART"

        self.app._cd_results[req_id] = []
        done = threading.Event()
        self.app._cd_done[req_id] = done

        self.app.reqContractDetails(req_id, contract)
        done.wait(timeout)
        return self.app._cd_results.get(req_id, [])

    def get_historical_bars(self, conid: int, exchange: str = "SMART", currency: str = "USD",
                             sec_type: str = "STK", duration: str = "20 Y",
                             bar_size: str = "1 day", what_to_show: str = "ADJUSTED_LAST",
                             timeout: float = 30.0):
        """Returns list of ibapi BarData for the given conid (read-only historical bars)."""
        req_id = self.app.next_valid_id
        self.app.next_valid_id += 1

        contract = Contract()
        contract.conId = conid
        contract.exchange = exchange
        contract.currency = currency
        contract.secType = sec_type

        self.app._hist_results[req_id] = []
        done = threading.Event()
        self.app._hist_done[req_id] = done
        errors_before = len(self.app._errors)

        self.app.reqHistoricalData(req_id, contract, "", duration, bar_size,
                                    what_to_show, 1, 1, False, [])
        finished = done.wait(timeout)
        bars = self.app._hist_results.get(req_id, [])
        errs = [e for e in self.app._errors[errors_before:] if e[0] == req_id]
        if not finished:
            errs.append((req_id, -1, "client-side timeout waiting for historicalDataEnd"))
        return bars, errs
