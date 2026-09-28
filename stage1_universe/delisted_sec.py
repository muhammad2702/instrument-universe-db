"""
Best-effort delisted-instrument sourcing via SEC EDGAR full text search,
Form 25 / 25-NSE ("Notification of Removal from Listing").

Honest limitations (also printed in the Stage 1 report, not just here):
  - EDGAR full text search only indexes filings from 2001 onward. Anything
    delisted before 2001 is NOT captured by this method.
  - Only covers US-listed equities/ADRs that filed Form 25 or 25-NSE. It does
    NOT cover: expired/rolled futures contracts, closed ETFs (those file
    different forms / no form at all in many cases), foreign-exchange-listed
    delistings, or bankruptcies that skip a formal Form 25.
  - Ticker is parsed best-effort out of the free-text "display_names" field
    and is frequently absent (company had already lost its ticker by filing
    time), so a meaningful fraction of rows will have company_name + CIK but
    no symbol.
  - This sources *names*, not price history. IBKR will not serve historical
    bars for delisted conids, so downstream backtests still cannot include
    these names' returns -- the survivorship bias in any *return-based*
    analysis is reduced only to the extent one later attaches a third-party
    price series to these CIKs. That gap is not solved by this module and
    must stay documented, not silently dropped, per the pipeline's rules.
"""
import re
import time
from datetime import date

import requests

from allweather.config import SEC_EDGAR_HEADERS

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
PAGE_SIZE = 100
RATE_LIMIT_S = 0.25
TICKER_RE = re.compile(r"\(([A-Z][A-Z.\-]{0,5})\)")


def _build_source_url(cik: str, adsh: str) -> str:
    cik_int = str(int(cik))
    adsh_nodash = adsh.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{adsh_nodash}/{adsh}-index.htm"


def fetch_delisted_filings(start_date: date, end_date: date, forms=("25-NSE", "25"),
                            max_records: int = 2000) -> tuple[list[dict], dict]:
    """Returns (records, stats). stats reports what EDGAR says is available
    vs what was actually pulled, so truncation is visible, not silent."""
    all_records = []
    stats = {}
    for form in forms:
        frm = 0
        form_total = None
        while True:
            params = {
                "q": '"-"',  # EDGAR FTS requires a non-empty q; matches ~everything
                "forms": form,
                "dateRange": "custom",
                "startdt": start_date.isoformat(),
                "enddt": end_date.isoformat(),
                "from": frm,
            }
            resp = requests.get(EFTS_URL, params=params, headers=SEC_EDGAR_HEADERS, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
            total = payload["hits"]["total"]["value"]
            if form_total is None:
                form_total = total
            hits = payload["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                src = h["_source"]
                names = src.get("display_names", [])
                company_name = names[0] if names else None
                cik = src.get("ciks", [None])[0]
                m = TICKER_RE.search(names[0]) if names else None
                ticker = m.group(1) if m else None
                all_records.append({
                    "company_name": company_name,
                    "ticker_guess": ticker,
                    "cik": cik,
                    "form_type": src.get("form"),
                    "filing_date": src.get("file_date"),
                    "source_url": _build_source_url(cik, src["adsh"]) if cik and src.get("adsh") else None,
                })
            frm += PAGE_SIZE
            time.sleep(RATE_LIMIT_S)
            if frm >= total or len(all_records) >= max_records:
                break
        stats[form] = {"available_in_edgar": form_total, "pulled": sum(
            1 for r in all_records if r["form_type"] == form)}
        if len(all_records) >= max_records:
            break
    return all_records[:max_records], stats
