from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from dq import cli, ingest, lint

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = [ROOT / "config" / "contract.yaml", ROOT / "examples" / "online_retail" / "contract.yaml"]


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.parent.name)
def test_both_shipped_contracts_lint_clean(path):
    assert lint.lint(ingest.load_contract(path)) == []


def test_lint_catches_the_mistakes_that_would_otherwise_fail_late_or_silently():
    c = copy.deepcopy(ingest.load_contract(CONTRACTS[1]))
    c["rules"].append({"id": "C1", "severity": "fatal", "check": "x", "columns": ["nope"], "description": "d",
                       "sql": "quantity >", "extra": 1})  # fmt: skip
    c["snapshot_key"].append("missing_col")
    msgs = " | ".join(lint.lint(c))
    for expected in ("used twice", "severity must be", "columns not in the contract", "SQL does not compile",
                     "unexpected keys", "snapshot_key: missing_col"):  # fmt: skip
        assert expected in msgs


def test_lint_flag_exits_nonzero_on_a_bad_contract(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.yaml"
    c = copy.deepcopy(ingest.load_contract(CONTRACTS[1]))
    c["rules"][0]["sql"] = "invoice IS NOT"
    import yaml

    bad.write_text(yaml.safe_dump(c))
    monkeypatch.setattr(sys, "argv", ["dq", "--lint", "--contract", str(bad)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1 and "does not compile" in capsys.readouterr().out


def test_quality_gate_exits_with_code_2_when_the_quarantine_rate_is_too_high(tmp_path, monkeypatch, capsys):
    fixtures = ROOT / "tests" / "fixtures"
    args = ["dq", "--current", str(fixtures / "current_asof_260630.csv"), "--out", str(tmp_path),
            "--max-quarantine-rate", "0.01"]  # fmt: skip
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2 and "QUALITY GATE FAILED" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", args[:-1] + ["0.9"])
    cli.main()  # passes quietly under a generous limit
