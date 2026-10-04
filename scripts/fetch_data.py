"""Download the two quarterly SBA 7(a) snapshots used for the sample report and verify their checksums.

    python scripts/fetch_data.py

About 1.4 GB in total. SBA replaces the files each quarter and may remove older snapshots; the checksums
below identify the exact files behind reports/quality_report.html. A mismatch means the publisher changed
a file, which the pipeline can still validate, but the counts in the sample report will differ.
"""

from __future__ import annotations

import hashlib
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

import certifi

BASE = "https://data.sba.gov/sites/default/files/uploaded_resources"
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
FILES = {
    "FOIA_7a_FY2000_FY2009_asof_260331.csv": "d2b74f26c206ef72ea0c574c6b55965c1a4602b4239f310db7e08988b195d1a0",
    "FOIA_7a_FY2010_FY2019_asof_260331.csv": "442cc3dbecb008010499cb3ac7a763f24220e0b43f7771ea1ac7cca869e419d9",
    "FOIA_7a_FY2020_Present_asof_260331.csv": "e98d2034e11e4df824b06a556090879817ce503356c77fa20bc8fa83c2897037",
    "FOIA_7a_FY2000_FY2009_asof_260630.csv": "66674e18a700fbba0378c25118c291ac2759784a550c643cab5747eced763d6a",
    "FOIA_7a_FY2010_FY2019_asof_260630.csv": "01a3e2c7988a6f4052e53f218a309feb2ec2fe42887bebdc0fa94ac8b1024ade",
    "FOIA_7a_FY2020_Present_asof_260630.csv": "6c1e9132b5141a19f82bdc8ccafb86c9a01662461cad41ddb36a3cf409d8a4fe",
}


def download(url: str, path: Path) -> None:
    # certifi's CA bundle: python.org's macOS builds ship without root certificates.
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, context=ctx) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    mismatched = []
    for name, expected in FILES.items():
        path = RAW / name
        if not path.exists():
            print(f"downloading {name}")
            download(f"{BASE}/{name}", path)
        ok = sha256(path) == expected
        print(f"{'ok      ' if ok else 'CHANGED '} {name}")
        if not ok:
            mismatched.append(name)
    if mismatched:
        print(f"{len(mismatched)} file(s) differ from the ones behind the sample report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
