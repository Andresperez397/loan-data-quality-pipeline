"""End-to-end tests on two synthetic snapshots with planted problems (see tests/fixtures/make_fixtures.py)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from make_fixtures import CURRENT, EXPECTED_ERRORS, EXPECTED_WARNINGS, PREVIOUS

from dq import cli, diff, ingest, validate

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
PREV = str(FIX / "previous_asof_260331.csv")
CURR = str(FIX / "current_asof_260630.csv")


@pytest.fixture(scope="module")
def contract():
    return ingest.load_contract(ROOT / "config" / "contract.yaml")


@pytest.fixture(scope="module")
def loaded(contract):
    con = duckdb.connect()
    prev = cli.process(con, PREV, "prev", contract)
    curr = cli.process(con, CURR, "curr", contract)
    rules = validate.run_rules(con, "curr", contract)
    return con, prev, curr, rules


@pytest.fixture(scope="module")
def full_run(tmp_path_factory, contract):
    out = tmp_path_factory.mktemp("out")
    return out, cli.run(CURR, PREV, out, ROOT / "config" / "contract.yaml")


def failed_labels(con, rule_id: str) -> set[str]:
    rows = con.execute(
        "SELECT c.lender_id FROM failures f JOIN curr c USING (row_id) WHERE f.rule_id = ?", [rule_id]
    ).fetchall()
    return {r[0] for r in rows}


def test_both_file_formats_map_to_the_contract(loaded):
    _, prev, curr, _ = loaded
    assert prev["schema"]["missing_required"] == [] and curr["schema"]["missing_required"] == []
    assert prev["schema"]["unexpected"] == ["subprogram"]
    assert curr["schema"]["unexpected"] == []
    assert "unquoted, lowercase" in prev["schema"]["header_style"][0]
    assert "quoted, CamelCase" in curr["schema"]["header_style"][0]


def test_each_format_fix_is_counted(loaded):
    _, prev, curr, _ = loaded
    n = len(PREVIOUS)
    assert prev["normalizations"]["approval_date__us_date_format"] == n
    assert prev["normalizations"]["revolver__true_false_coding"] == n
    assert prev["normalizations"]["borrower_zip__lost_leading_zero"] == 1  # L1, ZIP 02134
    assert curr["normalizations"]["loan_status__respelled"] == sum(r["loan_status"] == "PIF" for r in CURRENT)
    assert curr["normalizations"]["approval_date__unparseable"] == 1  # E3


def test_normalized_values_agree_across_formats(loaded):
    con = loaded[0]
    q = "SELECT borrower_zip, approval_date, gross_approval, revolver, loan_status FROM {} WHERE lender_id = 'L2'"
    assert con.execute(q.format("prev")).fetchone() == con.execute(q.format("curr")).fetchone()
    assert con.execute("SELECT borrower_zip FROM prev WHERE lender_id = 'L1'").fetchone()[0] == "02134"


@pytest.mark.parametrize("label,rule", sorted(EXPECTED_ERRORS.items()) + sorted(EXPECTED_WARNINGS.items()))
def test_planted_problem_is_caught_by_its_rule(loaded, label, rule):
    assert label in failed_labels(loaded[0], rule)


def test_clean_loans_fail_no_error_or_warning_rule(loaded):
    con = loaded[0]
    flagged = con.execute(
        "SELECT DISTINCT c.lender_id FROM failures f JOIN curr c USING (row_id) "
        "WHERE f.severity IN ('error', 'warning')"
    ).fetchall()
    assert {r[0] for r in flagged} == set(EXPECTED_ERRORS) | set(EXPECTED_WARNINGS)


def test_split_quarantines_errors_and_drops_personal_fields(full_run):
    out, result = full_run
    s = result["current"]["split"]
    assert s["quarantined"] == len(EXPECTED_ERRORS)
    assert s["rows"] == len(CURRENT) and s["clean"] == len(CURRENT) - len(EXPECTED_ERRORS)
    q = pd.read_parquet(out / "quarantine.parquet")
    assert dict(zip(q["lender_id"], q["error_rules"], strict=True)) == EXPECTED_ERRORS
    clean = pd.read_parquet(out / "clean.parquet")
    assert not {"borrower_name", "borrower_street"} & set(clean.columns)
    assert set(clean.loc[clean["flagged"], "lender_id"]) == set(EXPECTED_WARNINGS)


def test_snapshot_diff(loaded, contract):
    con = loaded[0]
    d = diff.compare(con, "prev", "curr", contract)
    assert d["matching"] == {
        "matched": 5,  # L1-L5; L1 matches only because the ZIP and date formats were normalized
        "only_previous": 2,  # L8, L9
        "only_current": 11,  # N1, E1-E6, W1-W3, L9 with revised jobs
        "ambiguous_previous": 2,  # L6, L7 share a key
        "ambiguous_current": 2,
    }
    assert d["changed_fields"]["term_months"] == 1  # L5
    assert d["changed_fields"]["loan_status"] == 2  # L3, L4
    assert d["changed_fields"]["naics_code"] == 0
    assert d["resolved_loans_reopened"] == 1  # L4
    assert d["probable_key_revisions"]["loans"] == 1  # L9
    assert d["probable_key_revisions"]["changed"]["jobs_supported"] == 1
    assert not set(d["changed_fields"]) & set(contract["snapshot_key"])


def test_missing_required_column_stops_the_run(contract):
    with pytest.raises(SystemExit, match="gross_approval"):
        cli.process(duckdb.connect(), str(FIX / "missing_column_asof_260630.csv"), "bad", contract)


def test_reports_contain_no_personal_fields(full_run):
    out, _ = full_run
    text = (out / "quality_report.html").read_text() + (out / "quality_report.json").read_text()
    for r in PREVIOUS + CURRENT:
        assert r["borrower_name"] not in text and r["borrower_street"] not in text
    # Strict JSON: NaN is not valid JSON and breaks most parsers outside Python.
    json.loads((out / "quality_report.json").read_text(), parse_constant=lambda c: pytest.fail(f"{c} in JSON"))


def test_contract_rules_name_real_columns(contract):
    known = set(contract["columns"]) | {"snapshot_key"}
    ids = [r["id"] for r in contract["rules"]]
    assert len(ids) == len(set(ids))
    for r in contract["rules"]:
        # An unquoted comma in a YAML flow mapping silently splits a value into a stray key.
        assert set(r) == {"id", "severity", "check", "columns", "description", "sql"}, r["id"]
        assert set(r["columns"]) <= known, r["id"]
        assert r["severity"] in {"error", "warning", "info"}
