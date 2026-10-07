"""Check a data contract before it is used: structure, names, and that every rule's SQL compiles against the schema.

A typo in a contract should fail in seconds with a clear message, not hours into a run on a large file or, worse,
silently count zero failures.
"""

from __future__ import annotations

import duckdb

SQL_TYPES = {"date": "DATE", "number": "DOUBLE", "integer": "INTEGER", "boolean": "BOOLEAN"}
SEVERITIES = {"error", "warning", "info"}
RULE_KEYS = {"id", "severity", "check", "columns", "description", "sql"}
DERIVED = {"snapshot_key", "key_count", "row_id"}


def lint(contract: dict) -> list[str]:
    problems: list[str] = []
    cols = contract.get("columns", {})
    if not cols:
        return ["contract has no columns"]
    for name, c in cols.items():
        if "source" not in c or "type" not in c:
            problems.append(f"column {name}: needs `source` and `type`")
    known = set(cols) | DERIVED

    ids = [r.get("id") for r in contract.get("rules", [])]
    problems += [f"rule id {i} is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    for r in contract.get("rules", []):
        rid = r.get("id", "?")
        extra = set(r) - RULE_KEYS
        if extra:
            problems.append(f"rule {rid}: unexpected keys {sorted(extra)} (an unquoted comma in YAML splits a value)")
        if r.get("severity") not in SEVERITIES:
            problems.append(f"rule {rid}: severity must be one of {sorted(SEVERITIES)}")
        missing = [c for c in r.get("columns", []) if c not in known]
        if missing:
            problems.append(f"rule {rid}: columns not in the contract: {missing}")

    for field in contract.get("snapshot_key", []):
        if field not in cols:
            problems.append(f"snapshot_key: {field} is not a contract column")
    loose = contract.get("loose_key", [])
    if loose and not set(loose) <= set(contract.get("snapshot_key", [])):
        problems.append("loose_key must be a subset of snapshot_key")
    cmp = contract.get("compare", {})
    for field in cmp.get("tracked_fields", []) + [cmp.get("status_field"), cmp.get("group_field")]:
        if field and field not in cols:
            problems.append(f"compare: {field} is not a contract column")
    asof = contract.get("as_of", {}).get("column")
    if asof and asof not in cols:
        problems.append(f"as_of: {asof} is not a contract column")

    # Compile each rule's SQL against an empty table with the contract's schema.
    con = duckdb.connect()
    schema = ", ".join(f"{n} {SQL_TYPES.get(c.get('type'), 'VARCHAR')}" for n, c in cols.items())
    con.execute(f"CREATE TABLE t (row_id BIGINT, snapshot_key VARCHAR, key_count BIGINT, {schema})")
    for r in contract.get("rules", []):
        sql = r.get("sql", "")
        for name, values in contract.get("allowed", {}).items():
            sql = sql.replace("{" + name + "}", ", ".join(f"'{v}'" for v in values))
        try:
            con.execute(f"EXPLAIN SELECT row_id FROM t WHERE COALESCE(({sql}), FALSE)")
        except duckdb.Error as e:
            problems.append(f"rule {r.get('id', '?')}: SQL does not compile: {str(e).splitlines()[0]}")
    return problems
