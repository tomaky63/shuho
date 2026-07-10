"""Yahoo Finance(yfinance)からファンダメンタル指標を取得する。

スコア対象になりうる銘柄(流動性フィルター通過・非金融)のみ取得し、
data/yahoo_fundamentals.csv に保存する。値の整合性はYahoo側で担保されており、
EDINETスナップショットは検算用の第二ソースとして使う(docs/design.md参照)。
"""

import argparse
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parents[1]

FIELDS = [
    "trailingEps",
    "forwardEps",
    "bookValue",
    "dividendYield",
    "operatingCashflow",
    "marketCap",
    "sharesOutstanding",
    "shortName",
]


def fetch_one(symbol: str, tries: int = 3) -> dict | None:
    for attempt in range(tries):
        try:
            info = yf.Ticker(f"{symbol}.T").info
            if info and info.get("marketCap") is not None:
                row = {"symbol": symbol}
                for f in FIELDS:
                    row[f] = info.get(f)
                return row
        except Exception as exc:
            print(f"  {symbol} 失敗 (attempt {attempt + 1}): {exc}")
        time.sleep(3 * (attempt + 1))
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universe", default=str(REPO / "data" / "universe.csv"))
    ap.add_argument("--out", default=str(REPO / "data" / "yahoo_fundamentals.csv"))
    ap.add_argument("--interval", type=float, default=0.4, help="リクエスト間隔(秒)")
    args = ap.parse_args()

    uni = pd.read_csv(args.universe, dtype={"symbol": str})
    uni = uni[
        (uni["include_for_collection"] == 1)
        & (uni["low_liquidity_candidate"] == 0)
        & (uni["is_financial"] == 0)
    ]
    symbols = sorted(uni["symbol"].unique())
    print(f"対象: {len(symbols)}銘柄")

    rows = []
    failed = []
    for i, s in enumerate(symbols, 1):
        row = fetch_one(s)
        if row is None:
            failed.append(s)
        else:
            rows.append(row)
        if i % 50 == 0:
            print(f"  {i}/{len(symbols)} 取得済み(失敗{len(failed)})")
        time.sleep(args.interval)

    if not rows:
        raise SystemExit("ファンダメンタルが1銘柄も取得できなかった")

    df = pd.DataFrame(rows)
    df["fetched_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"取得: {len(df)}/{len(symbols)}銘柄 → {args.out}")
    if failed:
        print(f"欠損({len(failed)}): {', '.join(failed[:20])}{' ...' if len(failed) > 20 else ''}")


if __name__ == "__main__":
    main()
