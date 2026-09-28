"""
Global configuration for the all-weather quadrant pipeline.

Stage 1 (universe construction) only. Nothing here places orders.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
DB_PATH = PROJECT_ROOT / "db" / "allweather.sqlite"

DATA_DIR.mkdir(exist_ok=True, parents=True)
REPORTS_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH.parent.mkdir(exist_ok=True, parents=True)

# IB Gateway connection. Port 4001 = LIVE gateway, confirmed listening and
# approved by user for READ-ONLY use in Stage 1 (scanners + contract details
# + historical data only -- no order-placing code exists in this package).
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4001
IBKR_CLIENT_ID = 9101  # arbitrary, unlikely to collide with other sessions

# SEC EDGAR full text search (free, public, no key required).
# Used to source delisted US equities via Form 25 / 25-NSE filings
# ("Notification of Removal from Listing"), per user instruction to either
# source delisted instruments or explicitly document the survivorship bias.
SEC_EDGAR_FTS_URL = "https://efts.sec.gov/LATEST/search-index?q=%22{form}%22&forms={form}"
# SEC requires a descriptive User-Agent with a contact email on every request.
# Set SEC_CONTACT_EMAIL in your environment; the placeholder below will make
# EDGAR reject the request, which is better than silently scraping as someone else.
import os
SEC_CONTACT_EMAIL = os.getenv("SEC_CONTACT_EMAIL", "set-SEC_CONTACT_EMAIL@example.com")
SEC_EDGAR_HEADERS = {
    "User-Agent": f"instrument-universe-db {SEC_CONTACT_EMAIL}",
}

QUADRANT_LABELS = {
    "reflation": "growth up / inflation up",
    "stagflation": "growth down / inflation up",
    "disinflationary_growth": "growth up / inflation down",
    "deflationary_bust": "growth down / inflation down",
}
