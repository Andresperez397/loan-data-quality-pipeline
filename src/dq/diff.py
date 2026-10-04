"""Compare two normalized snapshots: schema and format drift, row counts, and changes to existing loans.

Loans are matched on a key built from fields fixed at approval (no names or addresses). Keys shared by
more than one loan in either snapshot are reported as unmatchable instead of guessed.
"""

from __future__ import annotations

import pandas as pd


def compare(con, prev: str, curr: str, contract: dict) -> dict:
    out: dict = {}
    pc, cc = (con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in (prev, curr))
    out["rows"] = {"previous": int(pc), "current": int(cc), "change": int(cc - pc)}
    for t, name in ((prev, "p"), (curr, "c")):
        con.execute(f"CREATE OR REPLACE TEMP TABLE u_{name} AS SELECT * FROM {t} WHERE key_count = 1")
    m = con.execute(
        """SELECT
        (SELECT count(*) FROM u_p JOIN u_c USING (snapshot_key)) AS matched,
        (SELECT count(*) FROM u_p ANTI JOIN u_c USING (snapshot_key)) AS only_previous,
        (SELECT count(*) FROM u_c ANTI JOIN u_p USING (snapshot_key)) AS only_current,
        (SELECT count(*) FROM {p} WHERE key_count > 1) AS ambiguous_previous,
        (SELECT count(*) FROM {c} WHERE key_count > 1) AS ambiguous_current""".format(p=prev, c=curr)
    ).fetchdf()
    out["matching"] = {k: int(v) for k, v in m.iloc[0].items()}
    new_by_fy = con.execute(
        "SELECT approval_fy, count(*) n FROM u_c ANTI JOIN u_p USING (snapshot_key) GROUP BY 1 ORDER BY 1 DESC"
    ).fetchdf()
    out["new_loans_by_fy"] = {
        int(a): int(b) for a, b in zip(new_by_fy["approval_fy"], new_by_fy["n"], strict=True) if pd.notna(a)
    }
    fields = contract["approval_time_fields"] + ["loan_status", "charge_off_date", "paid_in_full_date"]
    sums = ", ".join(f"sum((p.{f} IS DISTINCT FROM c.{f})::int) AS {f}" for f in fields)
    ch = con.execute(f"SELECT {sums} FROM u_p p JOIN u_c c USING (snapshot_key)").fetchdf().iloc[0]
    out["changed_fields"] = {k: int(v) for k, v in ch.items()}
    tr = con.execute("""SELECT p.loan_status AS previous, c.loan_status AS current, count(*) AS loans
                        FROM u_p p JOIN u_c c USING (snapshot_key)
                        WHERE p.loan_status IS DISTINCT FROM c.loan_status
                        GROUP BY ALL ORDER BY loans DESC, previous, current""").fetchdf()
    out["status_transitions"] = tr.to_dict(orient="records")
    term = con.execute("""SELECT c.loan_status AS status_now, count(*) AS loans
                          FROM u_p p JOIN u_c c USING (snapshot_key)
                          WHERE p.term_months IS DISTINCT FROM c.term_months GROUP BY 1 ORDER BY 2 DESC, 1""").fetchdf()
    out["term_changes_by_status"] = term.to_dict(orient="records")
    # Changes that should not happen to an approval-time field, for example a loan that moved backwards.
    back = con.execute("""SELECT count(*) FROM u_p p JOIN u_c c USING (snapshot_key)
                          WHERE p.loan_status IN ('PIF', 'CHGOFF') AND c.loan_status IN ('EXEMPT', 'COMMIT')""")
    out["resolved_loans_reopened"] = int(back.fetchone()[0])
    out["key_fields"] = contract["snapshot_key"]
    return out


def null_rate_drift(con, prev: str, curr: str, contract: dict) -> pd.DataFrame:
    rows = []
    for name in contract["columns"]:
        if contract["columns"][name].get("pii"):
            continue
        a, b = (con.execute(f"SELECT avg(({name} IS NULL)::int) FROM {t}").fetchone()[0] for t in (prev, curr))
        rows.append({"column": name, "null_previous": a, "null_current": b, "change": b - a})
    return pd.DataFrame(rows)
