"""Command-line entry point.

python -m dq.cli --current 'data/raw/*asof_260630.csv' --previous 'data/raw/*asof_260331.csv' --out output/
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import duckdb

from . import diff, ingest, report, validate

ROOT = Path(__file__).resolve().parents[2]

NOTES = [
    "Term (months) is not a reliable approval-time field: SBA rewrites it for some loans after approval. Rule K1 "
    "counts non-standard terms, and section 3 shows terms changing between quarters.",
    "Interest rates are missing for loans approved before FY2009 (rule C4). That is a property of the source, "
    "so it is counted as info, not as an error.",
    "Active loans have status EXEMPT because SBA withholds their status under FOIA exemption 4.",
]


def process(con, pattern: str, name: str, contract: dict) -> dict:
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f"no files match {pattern}")
    sr = ingest.read_raw(con, files, f"raw_{name}")
    sr = ingest.check_schema(con, f"raw_{name}", contract, sr)
    if sr.missing_required:
        raise SystemExit(f"required columns missing in {name}: {sr.missing_required}")
    counts = ingest.normalize(con, f"raw_{name}", name, contract)
    validate.add_snapshot_key(con, name, contract)
    as_of = con.execute(f"SELECT max(as_of_date) FROM {name}").fetchone()[0]
    return {"files": sr.files, "as_of": str(as_of), "schema": sr.__dict__, "normalizations": counts}


def run(current: str, previous: str | None, out: Path, contract_path: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    contract = ingest.load_contract(contract_path)
    con = duckdb.connect()
    result = {"dataset": contract["dataset"], "notes": NOTES}
    if previous:
        result["previous"] = process(con, previous, "prev", contract)
    result["current"] = process(con, current, "curr", contract)
    rules = validate.run_rules(con, "curr", contract)
    result["current"]["rules"] = rules.astype(object).where(rules.notna(), None).to_dict(orient="records")
    result["current"]["split"] = validate.split(con, "curr", contract, out)
    if previous:
        result["diff"] = diff.compare(con, "prev", "curr", contract)
        nd = diff.null_rate_drift(con, "prev", "curr", contract)
        big = nd[nd["change"].abs() > 0.01]
        result["schema_drift"] = {
            "null_rate_shifts": ", ".join(
                f"{r.column} ({100 * r.null_previous:.1f}% to {100 * r.null_current:.1f}%)" for r in big.itertuples()
            ),
            "null_rates": nd.to_dict(orient="records"),
        }
    report.render(result, out / "quality_report.html", out / "quality_report.json")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--current", required=True, help="glob for the snapshot to validate")
    p.add_argument("--previous", help="glob for the earlier snapshot to compare against")
    p.add_argument("--out", default=str(ROOT / "output"))
    p.add_argument("--contract", default=str(ROOT / "config" / "contract.yaml"))
    a = p.parse_args()
    r = run(a.current, a.previous, Path(a.out), Path(a.contract))
    s = r["current"]["split"]
    print(f"{s['rows']:,} rows: {s['clean']:,} clean, {s['quarantined']:,} quarantined, {s['flagged']:,} flagged")


if __name__ == "__main__":
    main()
