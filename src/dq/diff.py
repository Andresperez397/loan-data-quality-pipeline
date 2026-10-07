"""Compare two normalized snapshots: row counts, record matching, changes to existing records and null-rate drift.

Records are matched on a key built from the contract's `snapshot_key` fields (no personal data). Keys shared by
more than one record in either snapshot are reported as unmatchable instead of guessed. What is compared, and which
status changes are suspicious, comes from the contract's `compare` section, so one code path serves any dataset.
"""

from __future__ import annotations

import pandas as pd


def compare(con, prev: str, curr: str, contract: dict) -> dict:
    cfg = contract.get("compare", {})
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

    group = cfg.get("group_field")
    if group:
        new_by = con.execute(
            f"SELECT {group} AS g, count(*) n FROM u_c ANTI JOIN u_p USING (snapshot_key) GROUP BY 1 ORDER BY 1 DESC"
        ).fetchdf()
        out["new_by_group"] = {
            (int(a) if isinstance(a, (int, float)) else str(a)): int(b)
            for a, b in zip(new_by["g"], new_by["n"], strict=True)
            if pd.notna(a)
        }

    fields = cfg.get("tracked_fields", [])
    if fields:
        sums = ", ".join(f"sum((p.{f} IS DISTINCT FROM c.{f})::int) AS {f}" for f in fields)
        ch = con.execute(f"SELECT {sums} FROM u_p p JOIN u_c c USING (snapshot_key)").fetchdf().iloc[0]
        out["changed_fields"] = {k: int(v) for k, v in ch.items()}

    status = cfg.get("status_field")
    if status:
        tr = con.execute(
            f"""SELECT p.{status} AS previous, c.{status} AS current, count(*) AS n
                FROM u_p p JOIN u_c c USING (snapshot_key)
                WHERE p.{status} IS DISTINCT FROM c.{status}
                GROUP BY ALL ORDER BY n DESC, previous, current"""
        ).fetchdf()
        out["status_transitions"] = tr.to_dict(orient="records")
        bd = cfg.get("breakdown")
        if bd:
            by = con.execute(
                f"""SELECT c.{status} AS status_now, count(*) AS n
                    FROM u_p p JOIN u_c c USING (snapshot_key)
                    WHERE p.{bd["field"]} IS DISTINCT FROM c.{bd["field"]} GROUP BY 1 ORDER BY 2 DESC, 1"""
            ).fetchdf()
            out["changes_by_status"] = {"field": bd["field"], "rows": by.to_dict(orient="records")}
        if cfg.get("resolved") and cfg.get("reopened"):
            quote = lambda xs: ", ".join(f"'{x}'" for x in xs)  # noqa: E731
            back = con.execute(
                f"""SELECT count(*) FROM u_p p JOIN u_c c USING (snapshot_key)
                    WHERE p.{status} IN ({quote(cfg["resolved"])}) AND c.{status} IN ({quote(cfg["reopened"])})"""
            )
            out["resolved_reopened"] = int(back.fetchone()[0])

    # Unmatched new records that match an unmatched removed record on a looser key: probably one record whose key
    # field was revised. Counted, not merged.
    if contract.get("loose_key"):
        loose = ", ".join(contract["loose_key"])
        revised = [f for f in contract["snapshot_key"] if f not in contract["loose_key"]]
        if revised:
            con.execute(f"""CREATE OR REPLACE TEMP TABLE probable AS
                SELECT n.*, {", ".join(f"(n.{f} IS DISTINCT FROM g.{f}) AS changed_{f}" for f in revised)}
                FROM (SELECT * FROM u_c ANTI JOIN u_p USING (snapshot_key)) n
                JOIN (SELECT * FROM u_p ANTI JOIN u_c USING (snapshot_key)) g USING ({loose})""")
            # One row per new record, even if it matches more than one removed record.
            per_row = ", ".join(f"bool_or(changed_{f}) AS changed_{f}" for f in revised)
            pr = con.execute(
                f"SELECT count(*), {', '.join(f'sum(changed_{f}::int)' for f in revised)} "
                f"FROM (SELECT row_id, {per_row} FROM probable GROUP BY row_id)"
            ).fetchone()
            out["probable_key_revisions"] = {
                "records": int(pr[0]),
                "loose_key": contract["loose_key"],
                "changed": {f: int(v or 0) for f, v in zip(revised, pr[1:], strict=True)},
            }
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
