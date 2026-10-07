"""Write the two sheets of UCI Online Retail II as two CSV 'releases' the pipeline can compare.

    python examples/online_retail/prepare_data.py

Downloads the 45 MB workbook (CC BY 4.0, Chen 2019) if it is not in data/retail/, then writes
data/retail/release_2009_2010.csv and data/retail/release_2010_2011.csv. The two sheets overlap by nine days, which is
exactly the kind of thing a release-to-release diff should surface.
"""

from __future__ import annotations

import shutil
import ssl
import urllib.request
import zipfile
from pathlib import Path

import certifi
import pandas as pd

URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
OUT = Path(__file__).resolve().parents[2] / "data" / "retail"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    book = OUT / "online_retail_II.xlsx"
    if not book.exists():
        zpath = OUT / "online_retail_ii.zip"
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(URL, context=ctx) as r, open(zpath, "wb") as f:
            shutil.copyfileobj(r, f)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(OUT)
    sheets = pd.read_excel(book, sheet_name=None, dtype=str)
    for name, df in zip(("release_2009_2010", "release_2010_2011"), sheets.values(), strict=True):
        df["Customer ID"] = df["Customer ID"].str.replace(r"\.0$", "", regex=True)  # Excel stored IDs as 13085.0
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"{name}.csv: {len(df):,} rows")


if __name__ == "__main__":
    main()
