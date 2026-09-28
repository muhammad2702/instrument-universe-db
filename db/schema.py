"""SQLite schema for the universe database (Stage 1)."""
import sqlite3
from allweather.config import DB_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scan_runs (
    scan_run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp_utc TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    location_code   TEXT NOT NULL,
    scan_code       TEXT NOT NULL,
    filters_json    TEXT,
    asset_class     TEXT NOT NULL,
    quadrant_tag    TEXT NOT NULL,
    result_count    INTEGER,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS instruments (
    conid           INTEGER PRIMARY KEY,
    symbol          TEXT,
    sec_type        TEXT,
    exchange        TEXT,
    primary_exchange TEXT,
    currency        TEXT,
    local_symbol    TEXT,
    trading_class   TEXT,
    long_name       TEXT,
    industry        TEXT,
    category        TEXT,
    subcategory     TEXT,
    asset_class     TEXT,
    quadrant_tag    TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    first_seen_utc  TEXT NOT NULL,
    last_seen_utc   TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'ibkr_scanner',
    is_leveraged_or_inverse INTEGER NOT NULL DEFAULT 0,
    enriched_utc    TEXT
);

CREATE TABLE IF NOT EXISTS scan_results (
    scan_run_id     INTEGER NOT NULL,
    conid           INTEGER NOT NULL,
    rank            INTEGER,
    PRIMARY KEY (scan_run_id, conid),
    FOREIGN KEY (scan_run_id) REFERENCES scan_runs(scan_run_id),
    FOREIGN KEY (conid) REFERENCES instruments(conid)
);

CREATE TABLE IF NOT EXISTS daily_bars (
    conid           INTEGER NOT NULL,
    bar_date        TEXT NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          REAL,
    wap             REAL,
    bar_count       INTEGER,
    what_to_show    TEXT NOT NULL,
    PRIMARY KEY (conid, bar_date, what_to_show),
    FOREIGN KEY (conid) REFERENCES instruments(conid)
);

CREATE TABLE IF NOT EXISTS delisted_instruments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name    TEXT,
    cik             TEXT,
    form_type       TEXT,
    filing_date     TEXT,
    source_url      TEXT,
    ingested_utc    TEXT NOT NULL,
    notes           TEXT,
    UNIQUE(cik, form_type, filing_date)
);

CREATE INDEX IF NOT EXISTS idx_instruments_asset_class ON instruments(asset_class);
CREATE INDEX IF NOT EXISTS idx_instruments_quadrant ON instruments(quadrant_tag);
CREATE INDEX IF NOT EXISTS idx_scan_results_conid ON scan_results(conid);
CREATE INDEX IF NOT EXISTS idx_daily_bars_conid_date ON daily_bars(conid, bar_date);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MIGRATIONS = [
    ("instruments", "is_leveraged_or_inverse", "INTEGER NOT NULL DEFAULT 0"),
    ("instruments", "enriched_utc", "TEXT"),
    ("instruments", "history_fetched_utc", "TEXT"),
    ("instruments", "history_bar_count", "INTEGER"),
    ("instruments", "history_error", "TEXT"),
]


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        for table, col, decl in MIGRATIONS:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized schema at {DB_PATH}")
