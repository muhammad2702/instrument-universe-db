# Instrument Universe Database

**A SQLite-backed ingestion pipeline that builds a queryable universe of tradeable instruments from
the Interactive Brokers API — and is explicit about what the data cannot tell you.**

The interesting part of this project is not the fetching. It is the schema, the idempotency, and the
refusal to pretend the universe is complete when it is not.

---

## The database

Five tables, normalised, with the constraints actually enforced rather than assumed:

| Table | Holds | Key design |
|---|---|---|
| `instruments` | one row per contract (conid) | natural primary key on IBKR's `conid`; indexed on `asset_class` and `quadrant_tag` |
| `daily_bars` | OHLCV history | **composite** PK `(conid, bar_date, what_to_show)` — the same day can legitimately exist as TRADES and as MIDPOINT; FK to `instruments` |
| `scan_runs` | one row per scanner invocation | audit trail: what was asked for, when, and what came back — including the error |
| `scan_results` | which instruments a run returned | composite PK `(scan_run_id, conid)`, FK to both parents |
| `delisted_instruments` | names sourced from SEC Form 25 filings | `UNIQUE(cik, form_type, filing_date)` for natural deduplication |

```sql
CREATE TABLE IF NOT EXISTS daily_bars (
    conid           INTEGER NOT NULL,
    bar_date        TEXT NOT NULL,
    open, high, low, close, volume, wap  REAL,
    bar_count       INTEGER,
    what_to_show    TEXT NOT NULL,
    PRIMARY KEY (conid, bar_date, what_to_show),
    FOREIGN KEY (conid) REFERENCES instruments(conid)
);

CREATE INDEX IF NOT EXISTS idx_daily_bars_conid_date ON daily_bars(conid, bar_date);
```

Foreign keys are switched on per connection (`PRAGMA foreign_keys = ON`) — SQLite does not enforce
them by default, so declaring them is not enough.

### Idempotent ingestion

Re-running any stage is safe. Writes are upserts, not blind inserts:

```sql
INSERT INTO instruments (conid, symbol, sec_type, ...) VALUES (?, ?, ?, ...)
ON CONFLICT(conid) DO UPDATE SET
    last_seen_utc = excluded.last_seen_utc,
    ...
```

Every query is parameterised. Nothing is built by string concatenation.

### Migrations without dropping data

The schema evolved while a populated database was already in use, so `schema.py` carries a small
migration runner: it introspects the live table with `PRAGMA table_info`, compares against a declared
column list, and issues `ALTER TABLE ... ADD COLUMN` only for what is missing.

```python
MIGRATIONS = [
    ("instruments", "is_leveraged_or_inverse", "INTEGER NOT NULL DEFAULT 0"),
    ("instruments", "enriched_utc",            "TEXT"),
    ("instruments", "history_fetched_utc",     "TEXT"),
    ...
]
```

`init_db()` is therefore safe to call against an empty database or a populated one.

---

## What it currently holds

From the most recent Stage 1 run:

- **733 instruments**, all enriched with contract details — 534 equity stocks, 142 equity ETFs,
  57 fixed-income ETFs
- **154 of 733 (21%)** flagged as leveraged or inverse. They are *flagged, not dropped* — Stage 1
  sources the universe, it does not curate it. Daily-reset leveraged ETPs have path-dependent
  volatility decay, so a later stage has to make an explicit include / exclude / cap decision rather
  than inherit a silent one.
- **1,800 delisted records** sourced from SEC EDGAR Form 25 / 25-NSE filings over a 10-year window

## Survivorship bias, stated rather than buried

The `instruments` table comes entirely from *live* IBKR scanners, so it contains only
currently-listed, currently-tradeable contracts. Any statistic computed from it alone is
survivorship-biased: names that went bankrupt, were acquired, or were liquidated are simply absent,
which mechanically inflates historical mean return and Sharpe.

The `delisted_instruments` table partially mitigates this by sourcing dead names from SEC filings —
but **name sourcing is not price history**. IBKR will not serve bars for dead conids, so this does
not by itself remove the bias. The pipeline's own diagnostic report says so in as many words, and
lists what the mitigation still does not cover (pre-2001 delistings, futures expiries, liquidated
ETFs that never filed a Form 25, non-US listings).

A data platform that quietly hands you a biased universe is worse than one that refuses to.

---

## Stage 2: stationarity battery

Runs six tests per instrument (DF-GLS, KPSS, Phillips-Perron, Zivot-Andrews, variance ratio,
ARCH-LM) plus a DFA Hurst exponent, across 685 instruments with sufficient history.

Results are reported **both raw and Benjamini-Hochberg FDR-adjusted**, because running six tests
across 685 assets at α = 0.05 produces roughly 34 false rejections per test by construction. The
report prints both columns side by side so the inflation is visible rather than inferred.

---

## Layout

```
config.py                     paths, IBKR connection, SEC headers
db/schema.py                  DDL, connection factory, migration runner
stage1_universe/
    ibkr_client.py            IBKR API wrapper
    scan_definitions.py       scanner definitions per asset class
    run_stage1.py             scan -> instruments -> scan_results
    enrich.py                 reqContractDetails -> industry/category/leveraged flag
    fetch_history.py          daily bars -> daily_bars
    delisted_sec.py           SEC EDGAR Form 25 -> delisted_instruments
stage2_stationarity/
    battery.py                six stationarity tests
    hurst.py                  DFA Hurst exponent
    run_stage2.py             batch runner -> parquet + report
reports/                      timestamped diagnostic reports
```

## Running it

Requires a running IB Gateway or TWS with the API enabled. Stage 1 is **read-only**: it calls
scanners, contract details and historical data. There is no order-placing code in this package.

```bash
export SEC_CONTACT_EMAIL="you@example.com"   # SEC requires a contact address in the User-Agent
python -m allweather.db.schema               # create / migrate the database
python -m allweather.stage1_universe.run_stage1
python -m allweather.stage2_stationarity.run_stage2
```

The database itself is not committed — it is ~272 MB and rebuildable by running the pipeline.

## Licence

MIT.
