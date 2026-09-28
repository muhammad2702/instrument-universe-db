"""
Stage 1 enrichment pass: reqContractDetails for every conid pulled by the
scanners, to fill in long_name/industry/category/subcategory (scanner rows
carry these only sporadically) and flag leveraged/inverse ETPs.

Leveraged/inverse flagging matters specifically because the pipeline's stated
objective is geometric-mean / Kelly, not mean-variance -- daily-reset
leveraged ETPs have a structurally different (and usually worse) long-run
geometric mean than their stated multiple of the underlying due to volatility
decay/beta-slippage. They are NOT excluded here (Stage 1 sources the
universe, it does not curate it) -- they are flagged so Stage 6 can make
that constraint decision explicitly and auditably instead of by accident.
"""
import re
import time
from datetime import datetime, timezone

from allweather.db.schema import get_connection
from allweather.stage1_universe.ibkr_client import IBKRUniverseClient

LEVERAGED_INVERSE_RE = re.compile(
    r"\b(\d(\.\d)?X|ULTRA(SHORT|PRO)?|LEVERAGED|INVERSE|DAILY BULL|DAILY BEAR|"
    r"BULL 3X|BEAR 3X|DIREXION DAILY|MICROSECTORS|VELOCITYSHARES)\b",
    re.IGNORECASE,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def enrich_all(conn, client: IBKRUniverseClient, only_missing: bool = True) -> dict:
    where = "WHERE enriched_utc IS NULL" if only_missing else ""
    conids = [r[0] for r in conn.execute(f"SELECT conid FROM instruments {where}").fetchall()]
    print(f"Enriching {len(conids)} instruments via reqContractDetails...")

    n_ok, n_empty, n_leveraged = 0, 0, 0
    for i, conid in enumerate(conids):
        cds = client.get_contract_details(conid)
        ts = now_utc()
        if not cds:
            n_empty += 1
            conn.execute("UPDATE instruments SET enriched_utc=? WHERE conid=?", (ts, conid))
            continue
        cd = cds[0]
        long_name = cd.longName or ""
        is_lev = bool(LEVERAGED_INVERSE_RE.search(long_name))
        if is_lev:
            n_leveraged += 1
        conn.execute("""
            UPDATE instruments SET
                long_name=?, industry=?, category=?, subcategory=?,
                is_leveraged_or_inverse=?, enriched_utc=?
            WHERE conid=?
        """, (long_name, cd.industry or "", cd.category or "", cd.subcategory or "",
              1 if is_lev else 0, ts, conid))
        n_ok += 1
        if (i + 1) % 100 == 0:
            conn.commit()
            print(f"  {i + 1}/{len(conids)}...")
    conn.commit()
    stats = {"total": len(conids), "enriched": n_ok, "empty": n_empty, "leveraged_or_inverse": n_leveraged}
    print(f"Enrichment done: {stats}")
    return stats


def main():
    conn = get_connection()
    client = IBKRUniverseClient()
    client.connect()
    try:
        enrich_all(conn, client)
    finally:
        client.disconnect()
        conn.close()


if __name__ == "__main__":
    main()
