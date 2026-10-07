"""Run the contract's rules in SQL, then split rows into clean, flagged and quarantined.

Rule results are stored per row and rule, so every quarantined record says exactly why it was held back.
"""

from __future__ import annotations

import pandas as pd


def add_snapshot_key(con, table: str, contract: dict) -> None:
    cols = contract["snapshot_key"]
    parts = ", ".join(f"COALESCE(CAST({c} AS VARCHAR), '')" for c in cols)
    con.execute(f"""CREATE OR REPLACE TABLE {table} AS
        SELECT *, md5(concat_ws('|', {parts})) AS snapshot_key,
               count(*) OVER (PARTITION BY md5(concat_ws('|', {parts}))) AS key_count
        FROM {table}""")


def rule_sql(rule: dict, contract: dict) -> str:
    sql = rule["sql"]
    # {name} placeholders expand to the quoted list in contract["allowed"][name] (for example {states}).
    for name, values in contract.get("allowed", {}).items():
        sql = sql.replace("{" + name + "}", ", ".join(f"'{v}'" for v in values))
    return sql


def run_rules(con, table: str, contract: dict) -> pd.DataFrame:
    """Create table `failures` (row_id, rule_id, severity) and return one summary row per rule."""
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    con.execute("CREATE OR REPLACE TABLE failures (row_id BIGINT, rule_id VARCHAR, severity VARCHAR)")
    rows = []
    for r in contract["rules"]:
        cond = f"COALESCE(({rule_sql(r, contract)}), FALSE)"
        con.execute(f"INSERT INTO failures SELECT row_id, '{r['id']}', '{r['severity']}' FROM {table} WHERE {cond}")
        k = con.execute(f"SELECT count(*) FROM failures WHERE rule_id = '{r['id']}'").fetchone()[0]
        cols = [c for c in r["columns"] if c != "snapshot_key"]
        example = None
        if k and cols:
            shown = ", ".join(f"CAST({c} AS VARCHAR) AS {c}" for c in cols)
            ex = con.execute(f"SELECT {shown} FROM {table} WHERE {cond} ORDER BY row_id LIMIT 1").fetchone()
            example = "; ".join(f"{c}={v if v is not None else 'missing'}" for c, v in zip(cols, ex, strict=True))
        rows.append(
            {
                "rule_id": r["id"],
                "check": r["check"],
                "severity": r["severity"],
                "description": r["description"],
                "failed": int(k),
                "rows": int(n),
                "fail_rate": k / n if n else 0.0,
                "example": example,
            }
        )
    return pd.DataFrame(rows)


def split(con, table: str, contract: dict, out_dir) -> dict:
    """Write clean (no errors, PII dropped), quarantine (rows with an error, with rule ids) and counts."""
    pii = [name for name, c in contract["columns"].items() if c.get("pii")]
    keep = ", ".join(f"t.{c}" for c in _columns(con, table) if c not in pii)
    con.execute("""CREATE OR REPLACE TABLE row_status AS
        SELECT row_id,
               bool_or(severity = 'error') AS has_error,
               bool_or(severity = 'warning') AS has_warning,
               string_agg(CASE WHEN severity = 'error' THEN rule_id END, ',' ORDER BY rule_id) AS error_rules,
               string_agg(CASE WHEN severity = 'warning' THEN rule_id END, ',' ORDER BY rule_id) AS warning_rules
        FROM failures WHERE severity IN ('error', 'warning') GROUP BY row_id""")
    con.execute(f"""COPY (SELECT {keep}, COALESCE(s.has_warning, FALSE) AS flagged, s.warning_rules
                         FROM {table} t LEFT JOIN row_status s USING (row_id)
                         WHERE NOT COALESCE(s.has_error, FALSE) ORDER BY t.row_id)
                   TO '{out_dir}/clean.parquet' (FORMAT PARQUET)""")
    con.execute(f"""COPY (SELECT s.error_rules, s.warning_rules, {keep}
                         FROM {table} t JOIN row_status s USING (row_id)
                         WHERE s.has_error ORDER BY t.row_id)
                   TO '{out_dir}/quarantine.parquet' (FORMAT PARQUET)""")
    c = con.execute(f"""SELECT count(*) total,
                               sum(COALESCE(s.has_error, FALSE)::int) quarantined,
                               sum((NOT COALESCE(s.has_error, FALSE) AND COALESCE(s.has_warning, FALSE))::int) flagged
                        FROM {table} t LEFT JOIN row_status s USING (row_id)""").fetchone()
    return {
        "rows": int(c[0]),
        "quarantined": int(c[1]),
        "flagged": int(c[2]),
        "clean": int(c[0]) - int(c[1]),
        "pii_columns_dropped": pii,
    }


def _columns(con, table: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
