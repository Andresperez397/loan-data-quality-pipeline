"""Write two small synthetic snapshots in the two real file formats, with known problems planted.

    python tests/fixtures/make_fixtures.py

Every loan is invented. `lender_id` carries a label (L1, E2, ...) so tests can check which rule caught which
row. The previous snapshot uses the March 2026 format (unquoted lowercase headers, US dates, TRUE/FALSE,
ZIPs that lost their leading zero, an extra `subprogram` column). The current snapshot uses the June 2026
format (quoted CamelCase headers, ISO dates, Y/N, numbers with a decimal point, status spelled "P I F").
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Contract column -> June header, in file order.
HEADERS = {
    "as_of_date": "AsOfDate", "program": "Program", "lender_id": "LocationID", "borrower_name": "BorrName",
    "borrower_street": "BorrStreet", "borrower_city": "BorrCity", "borrower_state": "BorrState",
    "borrower_zip": "BorrZip", "bank_name": "BankName", "bank_fdic_number": "BankFDICNumber",
    "bank_ncua_number": "BankNCUANumber", "bank_street": "BankStreet", "bank_city": "BankCity",
    "bank_state": "BankState", "bank_zip": "BankZip", "gross_approval": "GrossApproval",
    "sba_guaranteed": "SBAGuaranteedApproval", "approval_date": "ApprovalDate", "approval_fy": "ApprovalFY",
    "first_disbursement_date": "FirstDisbursementDate", "processing_method": "ProcessingMethod",
    "interest_rate": "InitialInterestRate", "fixed_or_variable": "FixedorVariableInterestInd",
    "term_months": "TermInMonths", "naics_code": "NaicsCode", "naics_description": "NaicsDescription",
    "franchise_code": "FranchiseCode", "franchise_name": "FranchiseName", "project_county": "ProjectCounty",
    "project_state": "ProjectState", "sba_district_office": "SBADistrictOffice",
    "congressional_district": "CongressionalDistrict", "business_type": "BusinessType",
    "business_age": "BusinessAge", "loan_status": "LoanStatus", "paid_in_full_date": "PaidInFullDate",
    "charge_off_date": "ChargeOffDate", "charge_off_amount": "GrossChargeOffAmount",
    "revolver": "RevolverStatus", "jobs_supported": "JobsSupported", "collateral": "CollateralInd",
    "sold_secondary_market": "SoldSecMrktInd",
}  # fmt: skip
DATES = {"as_of_date", "approval_date", "first_disbursement_date", "paid_in_full_date", "charge_off_date"}
NUMBERS = {"gross_approval", "sba_guaranteed", "interest_rate", "term_months", "charge_off_amount",
           "jobs_supported"}  # fmt: skip
BOOLS = {"revolver", "collateral", "sold_secondary_market"}
ZIPS = {"borrower_zip", "bank_zip"}


def loan(label: str, gross: float, **kw) -> dict:
    row = {
        "program": "7A", "lender_id": label, "borrower_name": f"Example Borrower {label}",
        "borrower_street": f"{gross:.0f} Example Street", "borrower_city": "Springfield", "borrower_state": "IL",
        "borrower_zip": "62701", "bank_name": "Example Community Bank", "bank_fdic_number": "1234",
        "bank_ncua_number": "", "bank_street": "2 Example Avenue", "bank_city": "Springfield",
        "bank_state": "IL", "bank_zip": "62701", "gross_approval": gross, "sba_guaranteed": gross * 0.75,
        "approval_date": date(2020, 3, 16), "approval_fy": 2020, "first_disbursement_date": date(2020, 4, 15),
        "processing_method": "SBA Express Program", "interest_rate": 6.5, "fixed_or_variable": "V",
        "term_months": 120, "naics_code": "722511", "naics_description": "Full-Service Restaurants",
        "franchise_code": "", "franchise_name": "", "project_county": "SANGAMON", "project_state": "IL",
        "sba_district_office": "ILLINOIS DISTRICT OFFICE", "congressional_district": "13",
        "business_type": "CORPORATION", "business_age": "Existing or more than 2 years old",
        "loan_status": "EXEMPT", "paid_in_full_date": None, "charge_off_date": None, "charge_off_amount": 0,
        "revolver": False, "jobs_supported": 5, "collateral": True, "sold_secondary_market": False,
    }  # fmt: skip
    row.update(kw)
    return row


PIF = {"loan_status": "PIF", "paid_in_full_date": date(2023, 1, 31)}
DUP = {"approval_date": date(2021, 6, 1), "approval_fy": 2021}  # L6 and L7 share every key field

PREVIOUS = [
    loan("L1", 100_000, borrower_zip="02134", project_state="MA", borrower_state="MA"),
    loan("L2", 110_000, **PIF),
    loan("L3", 120_000),
    loan("L4", 130_000, **PIF),
    loan("L5", 140_000),
    loan("L6", 150_000, **DUP),
    loan("L7", 150_000, **DUP),
    loan("L8", 160_000),
    loan("L9", 260_000),
]

CURRENT = [
    loan("L1", 100_000, borrower_zip="02134", project_state="MA", borrower_state="MA"),
    loan("L2", 110_000, **PIF),
    loan("L3", 120_000, **PIF),  # status change: EXEMPT -> PIF
    loan("L4", 130_000),  # resolved loan reopened: PIF -> EXEMPT
    loan("L5", 140_000, term_months=117),  # term rewritten; also a non-standard term (K1)
    loan("L6", 150_000, **DUP),
    loan("L7", 150_000, **DUP),
    # L8 is gone from the current snapshot.
    loan("L9", 260_000, jobs_supported=7),  # key field revised: unmatched, but found by the loose key
    loan("N1", 170_000, approval_date=date(2026, 5, 1), approval_fy=2026, first_disbursement_date=None),
    loan("E1", 180_000, sba_guaranteed=200_000),  # X1: guarantee exceeds loan
    loan("E2", 190_000, project_state="ZZ"),  # V2: not a state code
    loan("E3", 200_000, approval_date="2020-13-45"),  # C1: unparseable date
    loan("E4", 210_000, charge_off_date=date(2024, 1, 1)),  # X3: charge-off date without CHGOFF
    loan("E5", 220_000, interest_rate=56.0),  # V5: rate out of range
    loan(
        "E6", 230_000, approval_date=date(2026, 8, 1), approval_fy=2026, first_disbursement_date=None
    ),  # X7: approved after the as-of date  # fmt: skip
    loan("W1", 240_000, interest_rate=1.0),  # V6: rate below 2%
    loan("W2", 250_000, naics_code="72251"),  # V9: NAICS not six digits
    loan(
        "W3",
        270_000,
        approval_date=date(2024, 2, 1),
        approval_fy=2024,
        interest_rate=None,
        first_disbursement_date=None,
    ),  # C6: no rate on a recent loan  # fmt: skip
]

EXPECTED_ERRORS = {"E1": "X1", "E2": "V2", "E3": "C1", "E4": "X3", "E5": "V5", "E6": "X7"}
EXPECTED_WARNINGS = {"W1": "V6", "W2": "V9", "W3": "C6", "L6": "U1", "L7": "U1"}


def _march(name: str, v) -> str:
    if v is None or v == "":
        return ""
    if name in DATES and isinstance(v, date):
        return f"{v.month}/{v.day}/{v.year}"
    if name in BOOLS:
        return "TRUE" if v else "FALSE"
    if name in ZIPS:
        return v.lstrip("0")
    if name in NUMBERS:
        return f"{v:g}"
    if name == "program":
        return " " + v
    return str(v)


def _june(name: str, v) -> str:
    if v is None or v == "":
        return ""
    if name in DATES and isinstance(v, date):
        return v.isoformat()
    if name in BOOLS:
        return "Y" if v else "N"
    if name in NUMBERS:
        return f"{float(v):.1f}"
    if name == "loan_status" and v == "PIF":
        return "P I F"
    if name == "program":
        return " " + v
    return str(v)


def write_march(rows: list[dict], path: Path, as_of: date) -> None:
    names = list(HEADERS)
    header = [HEADERS[n].lower() for n in names]
    header.insert(header.index("processingmethod") + 1, "subprogram")
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            vals = [_march(n, as_of if n == "as_of_date" else r.get(n)) for n in names]
            vals.insert(names.index("processing_method") + 1, "Express")
            w.writerow(vals)


def write_june(rows: list[dict], path: Path, as_of: date, drop: tuple[str, ...] = ()) -> None:
    names = [n for n in HEADERS if n not in drop]
    with open(path, "w", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writerow([HEADERS[n] for n in names])
        for r in rows:
            w.writerow([_june(n, as_of if n == "as_of_date" else r.get(n)) for n in names])


def main() -> None:
    write_march(PREVIOUS, HERE / "previous_asof_260331.csv", date(2026, 3, 31))
    write_june(CURRENT, HERE / "current_asof_260630.csv", date(2026, 6, 30))
    write_june(CURRENT[:3], HERE / "missing_column_asof_260630.csv", date(2026, 6, 30), drop=("gross_approval",))


if __name__ == "__main__":
    main()
