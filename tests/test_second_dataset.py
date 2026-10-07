"""The same pipeline, driven by a different contract (examples/online_retail/contract.yaml) on a tiny retail extract."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from dq import cli

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "examples" / "online_retail" / "contract.yaml"
COLS = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]

PREVIOUS = [
    ["500001", "85123A", "HEART HOLDER", "6", "2010-12-01 09:00:00", "2.55", "12345", "United Kingdom"],
    ["500002", "22423", "CAKE STAND", "2", "2010-12-02 10:00:00", "12.75", "", "United Kingdom"],  # no customer ID
    ["500003", "POST", "POSTAGE", "1", "2010-12-02 11:00:00", "18.00", "12345", "United Kingdom"],  # fee code
]
CURRENT = [
    ["500002", "22423", "CAKE STAND", "2", "2010-12-02 10:00:00", "12.75", "", "United Kingdom"],  # repeated line
    [
        "C500004",
        "85123A",
        "HEART HOLDER",
        "3",
        "2011-01-05 09:00:00",
        "2.55",
        "12345",
        "France",
    ],  # cancel, positive qty
    ["500005", "21212", "PACK OF CARDS", "-4", "2011-01-06 09:00:00", "0.00", "", "France"],  # write-off
    ["500006", "21213", "BAD DEBT", "1", "2011-01-07 09:00:00", "-100.00", "99999", "France"],  # negative price
    ["50007", "21214", "SHORT NUMBER", "1", "2011-01-08 09:00:00", "1.00", "99999", "France"],  # bad invoice format
    ["500008", "21215", "", "1", "2011-01-09 09:00:00", "1.00", "99999", "France"],  # no description
    ["500009", "21216", "DUPLICATE", "1", "2011-01-10 09:00:00", "1.00", "99999", "France"],
    ["500009", "21216", "DUPLICATE", "1", "2011-01-10 09:00:00", "1.00", "99999", "France"],  # exact duplicate line
    ["500010", "21217", "FREEBIE", "1", "2011-01-11 09:00:00", "0.00", "99999", "France"],  # free item
]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("retail")
    pd.DataFrame(PREVIOUS, columns=COLS).to_csv(d / "prev.csv", index=False)
    pd.DataFrame(CURRENT, columns=COLS).to_csv(d / "curr.csv", index=False)
    result = cli.run(str(d / "curr.csv"), str(d / "prev.csv"), d / "out", CONTRACT)
    return result, d / "out"


def failed(result) -> dict:
    return {r["rule_id"]: r["failed"] for r in result["current"]["rules"] if r["failed"]}


def test_rules_from_the_second_contract_find_what_was_planted(run):
    result, _ = run
    assert failed(result) == {"C2": 2, "C3": 1, "V2": 1, "V3": 1, "V4": 1, "X1": 1, "X2": 1, "U1": 2}


def test_errors_are_quarantined_and_warnings_flagged(run):
    result, out = run
    assert result["current"]["split"]["quarantined"] == 1  # the negative price
    q = pd.read_parquet(out / "quarantine.parquet")
    assert q["error_rules"].tolist() == ["V2"]


def test_diff_matches_the_repeated_line_without_any_loan_specific_fields(run):
    result, _ = run
    d = result["diff"]
    assert d["matching"]["matched"] == 1 and d["matching"]["only_previous"] == 2
    assert d["changed_fields"] == {"description": 0, "country": 0}
    assert "resolved_reopened" not in d and "status_transitions" not in d  # this contract has no status field


def test_report_uses_the_contract_wording_and_strict_json(run):
    _, out = run
    html = (out / "quality_report.html").read_text()
    assert "invoice lines" in html and "loans" not in html.lower()
    json.loads((out / "quality_report.json").read_text(), parse_constant=lambda c: pytest.fail(c))
