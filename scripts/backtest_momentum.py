"""12-1モメンタム戦略(上位N銘柄・月次リバランス)のバックテスト。

設計書v1.0 第1の矢の検証。現ユニバースでの検証のため生存者バイアスがあり、
結果は楽観方向に偏る。合格基準(設計書Phase 0): コスト2倍でも年率プラス / 最大DD<25%。

出力: docs/backtest/momentum_stats.md, docs/backtest/equity_curve.csv
"""

import argparse
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "data" / "backtest_prices.csv"


def download_prices(symbols: list[str], start: str, chunk_size: int = 100) -> pd.DataFrame:
    frames = []
    for i in range(0, len(symbols), chunk_size):
        chunk = [f"{s}.T" for s in symbols[i : i + chunk_size]]
        print(f"chunk {i // chunk_size + 1}/{-(-len(symbols) // chunk_size)}")
        for attempt in range(3):
            try:
                df = yf.download(tickers=chunk, start=start, auto_adjust=True,
                                 progress=False, group_by="ticker", threads=True)
                if df is not None and not df.empty:
                    break
            except Exception as exc:
                print(f"  retry {attempt + 1}: {exc}")
            time.sleep(15 * (attempt + 1))
        else:
            continue
        for t in chunk:
            try:
                s = df[t]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
            except KeyError:
                continue
            s = s.dropna()
            if len(s) > 0:
                frames.append(s.rename(t.removesuffix(".T")))
        time.sleep(2)
    return pd.concat(frames, axis=1)


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min())


def run_strategy(monthly: pd.DataFrame, top: int, cost_per_side: float) -> tuple[pd.Series, float]:
    """月次リバランスの12-1モメンタム。月末判定・翌月保有。"""
    rets = []
    dates = []
    prev: set[str] = set()
    turnover_sum = 0.0
    n_rebalance = 0
    for m in range(12, len(monthly) - 1):
        # 判定(m月末): score = P(1ヶ月前) / P(12ヶ月前) − 1
        valid = monthly.iloc[m].notna() & monthly.iloc[m - 1].notna() & monthly.iloc[m - 12].notna()
        scores = (monthly.iloc[m - 1] / monthly.iloc[m - 12] - 1)[valid].dropna()
        if len(scores) < top:
            continue
        selected = set(scores.nlargest(top).index)
        # 保有(m+1月): 等金額
        month_ret = (monthly.iloc[m + 1] / monthly.iloc[m] - 1)[list(selected)].mean()
        changed = len(selected - prev) / top if prev else 1.0
        cost = cost_per_side * 2 * changed if prev else cost_per_side  # 初回は買いのみ
        rets.append(month_ret - cost)
        dates.append(monthly.index[m + 1])
        turnover_sum += changed
        n_rebalance += 1
        prev = selected
    series = pd.Series(rets, index=pd.DatetimeIndex(dates))
    return series, turnover_sum / max(n_rebalance, 1)


def stats(monthly_rets: pd.Series) -> dict:
    equity = (1 + monthly_rets).cumprod()
    years = len(monthly_rets) / 12
    return {
        "CAGR": equity.iloc[-1] ** (1 / years) - 1,
        "年率ボラ": monthly_rets.std() * (12 ** 0.5),
        "シャープ": (monthly_rets.mean() * 12) / (monthly_rets.std() * (12 ** 0.5)),
        "最大DD": max_drawdown(equity),
        "月次勝率": (monthly_rets > 0).mean(),
        "月数": len(monthly_rets),
    }


def fmt(v: float, pct: bool = True) -> str:
    return f"{v * 100:.1f}%" if pct else f"{v:.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", type=int, default=11)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--cost", type=float, default=0.002, help="片道コスト")
    ap.add_argument("--benchmark", default="^N225",
                    help="ベンチマーク(1306.TはYahooデータに異常値があるため日経平均を既定に)")
    ap.add_argument("--refresh", action="store_true", help="価格キャッシュを再取得")
    args = ap.parse_args()

    uni = pd.read_csv(REPO / "data" / "universe.csv", dtype={"symbol": str})
    uni = uni[(uni["include_for_collection"] == 1) & (uni["low_liquidity_candidate"] == 0)]
    symbols = sorted(uni["symbol"].unique())
    start = (dt.date.today() - dt.timedelta(days=int(args.years * 365.25))).isoformat()

    if CACHE.exists() and not args.refresh:
        print(f"キャッシュ使用: {CACHE}")
        closes = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    else:
        closes = download_prices(symbols, start)
        closes.to_csv(CACHE)
    print(f"価格データ: {closes.shape[1]}銘柄 × {closes.shape[0]}日")

    monthly = closes.resample("ME").last()

    base, avg_turnover = run_strategy(monthly, args.top, args.cost)
    double, _ = run_strategy(monthly, args.top, args.cost * 2)
    top10, _ = run_strategy(monthly, 10, args.cost)  # 本番の保有数に近い分散版

    bench_raw = yf.download(args.benchmark, start=str(base.index[0].date()),
                            auto_adjust=True, progress=False)["Close"]
    if isinstance(bench_raw, pd.DataFrame):
        bench_raw = bench_raw.iloc[:, 0]
    bench = bench_raw.resample("ME").last().pct_change().dropna()
    bench = bench[bench.index.isin(base.index)]

    rows = []
    for label, s in [(f"上位{args.top}銘柄(コスト0.2%片道)", base),
                     (f"上位{args.top}銘柄(コスト2倍=0.4%片道)", double),
                     ("上位10銘柄(コスト0.2%片道)", top10),
                     (f"ベンチマーク {args.benchmark}", bench)]:
        st = stats(s)
        rows.append(f"| {label} | {fmt(st['CAGR'])} | {fmt(st['年率ボラ'])} | "
                    f"{fmt(st['シャープ'], pct=False)} | {fmt(st['最大DD'])} | "
                    f"{fmt(st['月次勝率'])} | {st['月数']} |")

    out_dir = REPO / "docs" / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    passed_cagr = stats(double)["CAGR"] > 0
    passed_dd = stats(base)["最大DD"] > -0.25
    dd10 = stats(top10)["最大DD"]
    md = f"""# 12-1モメンタム バックテスト結果

- 実行日: {dt.date.today().isoformat()}
- ユニバース: {len(symbols)}銘柄(現行ユニバース → **生存者バイアスあり・結果は楽観方向**)
- ルール: 月末判定 score=P(1ヶ月前)/P(12ヶ月前)−1、上位{args.top}銘柄等金額、月次入替
- 平均入替率: {avg_turnover * 100:.0f}%/月

| 系列 | CAGR | 年率ボラ | シャープ | 最大DD | 月次勝率 | 月数 |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## 設計書Phase 0 合格基準(上位{args.top}銘柄構成)

- コスト2倍でも年率プラス: {"✅" if passed_cagr else "❌"}
- 最大DD < 25%: {"✅" if passed_dd else "❌"}
- (参考)上位10銘柄構成の最大DD: {dd10 * 100:.1f}%

注: 本番の週報はモメンタム単独ではなく「バリュー複合×モメンタムフィルター・上位10銘柄」。
バリュー要素のヒストリカル検証はポイントインタイム財務が無料入手不可のため未実施であり、
本結果はモメンタム部品の傾向確認に留まる。生存者バイアス込みのCAGRは割り引いて読むこと。
"""
    (out_dir / "momentum_stats.md").write_text(md, encoding="utf-8")
    equity = (1 + base).cumprod().rename("strategy").to_frame()
    equity["benchmark"] = (1 + bench).cumprod()
    equity.to_csv(out_dir / "equity_curve.csv")
    print(md)
    print(f"出力: {out_dir}")


if __name__ == "__main__":
    main()
