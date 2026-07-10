"""yfinanceでユニバース全銘柄の調整済み日足終値を取得する。

data/universe.csv の銘柄コード(4桁)に .T を付けて取得し、
data/prices.csv(long形式: date,symbol,close)と data/prices_meta.json を出力する。
欠損銘柄は補完せず failed として記録する。
"""

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parents[1]


def fetch_chunk(tickers: list[str], start: str, tries: int = 3) -> pd.DataFrame | None:
    for attempt in range(tries):
        try:
            df = yf.download(
                tickers=tickers,
                start=start,
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
            if df is not None and not df.empty:
                return df
        except Exception as exc:  # ネットワーク/レート制限
            print(f"  取得失敗 (attempt {attempt + 1}): {exc}")
        time.sleep(10 * (attempt + 1))
    return None


def extract_closes(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.Series]:
    closes: dict[str, pd.Series] = {}
    for t in tickers:
        try:
            if isinstance(df.columns, pd.MultiIndex):
                s = df[t]["Close"]
            else:  # チャンクに1銘柄しかない場合
                s = df["Close"]
        except KeyError:
            continue
        s = s.dropna()
        if not s.empty:
            closes[t] = s
    return closes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universe", default=str(REPO / "data" / "universe.csv"))
    ap.add_argument("--out", default=str(REPO / "data" / "prices.csv"))
    ap.add_argument("--days", type=int, default=430, help="取得日数(13ヶ月+バッファ)")
    ap.add_argument("--chunk-size", type=int, default=100)
    args = ap.parse_args()

    uni = pd.read_csv(args.universe, dtype={"symbol": str})
    symbols = sorted(uni["symbol"].unique())
    start = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()

    rows: list[pd.DataFrame] = []
    ok: set[str] = set()

    def fetch_pass(targets: list[str], label: str) -> None:
        for i in range(0, len(targets), args.chunk_size):
            chunk = targets[i : i + args.chunk_size]
            tickers = [f"{s}.T" for s in chunk]
            print(f"{label} chunk {i // args.chunk_size + 1}: {len(tickers)}銘柄")
            df = fetch_chunk(tickers, start)
            if df is None:
                print("  チャンク全体が取得不能。次へ")
                continue
            for ticker, close in extract_closes(df, tickers).items():
                symbol = ticker.removesuffix(".T")
                part = close.reset_index()
                part.columns = ["date", "close"]
                part["symbol"] = symbol
                rows.append(part[["date", "symbol", "close"]])
                ok.add(symbol)
            time.sleep(2)

    fetch_pass(symbols, "1st")
    # yfinanceのキャッシュロック等による散発的失敗を小チャンクで再試行
    leftovers = sorted(set(symbols) - ok)
    if leftovers:
        time.sleep(10)
        fetch_pass(leftovers, "retry")

    if not rows:
        raise SystemExit("株価が1銘柄も取得できなかった")

    prices = pd.concat(rows, ignore_index=True)
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    prices.to_csv(args.out, index=False)

    failed = sorted(set(symbols) - ok)
    meta = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n_requested": len(symbols),
        "n_ok": len(ok),
        "n_failed": len(failed),
        "failed": failed,
        "max_date": str(prices["date"].max()),
    }
    meta_path = Path(args.out).with_name("prices_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"取得: {len(ok)}/{len(symbols)}銘柄, 最新日付 {meta['max_date']}")
    if failed:
        print(f"欠損({len(failed)}): {', '.join(failed[:20])}{' ...' if len(failed) > 20 else ''}")


if __name__ == "__main__":
    main()
