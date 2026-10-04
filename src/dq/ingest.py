"""Read a raw snapshot, check its schema against the contract, and normalize it into one typed table.

Everything runs in DuckDB. Raw values are read as text, so nothing is silently coerced by a CSV reader;
every normalization (date formats, boolean codes, status spellings, ZIP padding) is counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import yaml


def load_contract(path: str | Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def normalize_header(name: str) -> str:
    """Match DuckDB's normalize_names: lowercase, non-alphanumerics to underscores, reserved words prefixed."""
    n = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip().strip('"')).strip("_").lower()
    return "_" + n if n in {"program", "order", "select", "from", "group"} else n


@dataclass
class SchemaReport:
    files: list[str]
    rows: int
    header_style: list[str]
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)


def read_raw(con: duckdb.DuckDBPyConnection, files: list[str], table: str) -> SchemaReport:
    """Load raw CSV files as text into `table` and compare their headers with nothing assumed."""
    styles = []
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            header = fh.readline()
        quoted = header.startswith('"')
        lower = header.replace('"', "").split(",")[0] == header.replace('"', "").split(",")[0].lower()
        styles.append(
            f"{Path(f).name}: {'quoted' if quoted else 'unquoted'}, {'lowercase' if lower else 'CamelCase'}"
        )
    file_list = ", ".join(f"'{f}'" for f in files)
    # Each row gets a stable id at load time; every later step joins on it.
    con.execute(f"""CREATE OR REPLACE TABLE {table} AS
        SELECT row_number() OVER () AS __row, * FROM read_csv([{file_list}], header=true, all_varchar=true,
                                                             union_by_name=true, normalize_names=true)""")
    rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return SchemaReport(files=[Path(f).name for f in files], rows=rows, header_style=styles)


def check_schema(con, table: str, contract: dict, report: SchemaReport) -> SchemaReport:
    present = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()} - {"__row"}
    expected = {c["source"]: name for name, c in contract["columns"].items()}
    for src, name in expected.items():
        if src not in present:
            (
                report.missing_required
                if contract["columns"][name].get("required")
                else report.missing_optional
            ).append(name)
    report.unexpected = sorted(present - set(expected))
    return report


def _date(expr: str, formats: list[str]) -> str:
    fmts = ", ".join(f"'{f}'" for f in formats)
    return f"TRY_STRPTIME(NULLIF(TRIM({expr}), ''), [{fmts}])::DATE"


def normalize(con, raw: str, out: str, contract: dict) -> dict:
    """Build the typed table `out` from `raw`. Returns counts of each normalization applied."""
    present = {r[0] for r in con.execute(f"DESCRIBE {raw}").fetchall()}
    norm = contract["normalize"]
    trues = ", ".join(f"'{v}'" for v in norm["boolean_true"])
    falses = ", ".join(f"'{v}'" for v in norm["boolean_false"])
    status_map = " ".join(f"WHEN TRIM({{c}}) = '{k}' THEN '{v}'" for k, v in norm["loan_status"].items())
    select, counts_sql = ["__row AS row_id"], []
    for name, col in contract["columns"].items():
        src = col["source"]
        if src not in present:
            select.append(f"NULL AS {name}")
            continue
        c = f'"{src}"'
        t = col["type"]
        if t == "date":
            expr = _date(c, norm["date_formats"])
            counts_sql.append(
                f"sum((regexp_matches({c}, '^[0-9]{{1,2}}/[0-9]{{1,2}}/[0-9]{{4}}$'))::int) "
                f"AS {name}__us_date_format"
            )
        elif t == "number":
            expr = f"TRY_CAST(NULLIF(TRIM({c}), '') AS DOUBLE)"
        elif t == "integer":
            expr = f"TRY_CAST(TRY_CAST(NULLIF(TRIM({c}), '') AS DOUBLE) AS INTEGER)"
        elif t == "boolean":
            expr = (
                f"CASE WHEN UPPER(TRIM({c})) IN ({trues}) THEN TRUE "
                f"WHEN UPPER(TRIM({c})) IN ({falses}) THEN FALSE END"
            )
            counts_sql.append(
                f"sum((UPPER(TRIM({c})) IN ('TRUE','FALSE'))::int) AS {name}__true_false_coding"
            )
        elif t == "zip":
            expr = (
                f"CASE WHEN regexp_matches(TRIM({c}), '^[0-9]{{3,4}}$') THEN lpad(TRIM({c}), 5, '0') "
                f"ELSE NULLIF(LEFT(TRIM({c}), 5), '') END"
            )
            counts_sql.append(
                f"sum((regexp_matches(TRIM({c}), '^[0-9]{{3,4}}$'))::int) AS {name}__lost_leading_zero"
            )
        elif t == "status":
            expr = f"CASE {status_map.format(c=c)} ELSE NULLIF(TRIM({c}), '') END"
            counts_sql.append(
                f"sum((TRIM({c}) IN ({', '.join(repr(k) for k in norm['loan_status'])}))::int) "
                f"AS {name}__respelled"
            )
        else:
            expr = f"NULLIF(TRIM({c}), '')"
        select.append(f"{expr} AS {name}")
    con.execute(f"CREATE OR REPLACE TABLE {out} AS SELECT {', '.join(select)} FROM {raw}")
    counts = {}
    if counts_sql:
        row = con.execute(f"SELECT {', '.join(counts_sql)} FROM {raw}").fetchdf().iloc[0]
        counts = {k: int(v) for k, v in row.items() if v and int(v) > 0}
    # Values that failed to parse (present in raw, NULL after typing) are a validity problem in their own right.
    parse_fail = []
    for name, col in contract["columns"].items():
        if col["source"] in present and col["type"] in ("date", "number", "integer", "boolean"):
            parse_fail.append(
                f"sum((NULLIF(TRIM(r.\"{col['source']}\"), '') IS NOT NULL AND n.{name} IS NULL)::int)"
                f" AS {name}"
            )
    if parse_fail:
        row = (
            con.execute(f"SELECT {', '.join(parse_fail)} FROM {raw} r JOIN {out} n ON r.__row = n.row_id")
            .fetchdf()
            .iloc[0]
        )
        counts.update({f"{k}__unparseable": int(v) for k, v in row.items() if v and int(v) > 0})
    return counts
