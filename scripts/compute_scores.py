"""バリュー複合×モメンタムのスコアを計算し、週報JSONを出力する。

入力: data/fundamentals.csv, data/universe.csv, data/prices.csv, data/portfolio.json
出力: content/reports/YYYY-MM-DD.json (JST日付・正本)

スコアリング仕様は docs/design.md を参照。
"""

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")

VALUE_METRICS = [
    "earnings_yield",
    "book_yield",
    "cfo_yield",
    "dividend_yield",
    "forecast_earnings_yield",
]
MIN_VALUE_METRICS = 3
MOMENTUM_CUT = 0.20
TOP_N = 20
PORTFOLIO_N = 10
PRICE_STALE_DAYS = 7

DISCLAIMER = (
    "本レポートは個人の学習・記録目的で自動生成されたものであり、"
    "投資勧誘・投資助言ではありません。投資判断は自己責任で行ってください。"
)


def winsorized_rank(s: pd.Series) -> pd.Series:
    lo, hi = s.quantile(0.01), s.quantile(0.99)
    return s.clip(lo, hi).rank(pct=True) * 100


def load_prices(path: Path) -> tuple[pd.Series, pd.Series, pd.Series]:
    """終値long形式から (直近終値, 直近日付, 12-1モメンタム) を銘柄別に返す。"""
    prices = pd.read_csv(path, dtype={"symbol": str}, parse_dates=["date"])
    last_close: dict[str, float] = {}
    last_date: dict[str, pd.Timestamp] = {}
    momentum: dict[str, float] = {}
    for symbol, g in prices.groupby("symbol"):
        c = g.sort_values("date")
        arr = c["close"].to_numpy()
        last_close[symbol] = float(arr[-1])
        last_date[symbol] = c["date"].iloc[-1]
        if len(arr) >= 253 and arr[-253] > 0:
            momentum[symbol] = float(arr[-22] / arr[-253] - 1)
    return pd.Series(last_close), pd.Series(last_date), pd.Series(momentum)


def previous_report(reports_dir: Path, today_id: str) -> dict | None:
    candidates = sorted(p.stem for p in reports_dir.glob("*.json") if p.stem < today_id)
    if not candidates:
        return None
    return json.loads((reports_dir / f"{candidates[-1]}.json").read_text(encoding="utf-8"))


def safe_round(x, digits=2):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(float(x), digits)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force-monthly", action="store_true", help="月次リバランス号として生成")
    ap.add_argument("--date", default=None, help="レポート日付の上書き(YYYY-MM-DD、検証用)")
    args = ap.parse_args()

    now = dt.datetime.now(JST)
    report_date = dt.date.fromisoformat(args.date) if args.date else now.date()
    report_id = report_date.isoformat()
    # 月初の土曜に実行された号 = 月次リバランス号
    is_monthly = args.force_monthly or (report_date.weekday() == 5 and report_date.day <= 7)

    fundamentals = pd.read_csv(REPO / "data" / "fundamentals.csv", dtype={"symbol": str})
    universe = pd.read_csv(REPO / "data" / "universe.csv", dtype={"symbol": str})
    portfolio = json.loads((REPO / "data" / "portfolio.json").read_text(encoding="utf-8"))
    prices_meta = json.loads((REPO / "data" / "prices_meta.json").read_text(encoding="utf-8"))
    last_close, last_date, momentum = load_prices(REPO / "data" / "prices.csv")

    df = universe.merge(fundamentals, on="symbol", how="left")
    df["close"] = df["symbol"].map(last_close)
    df["price_date"] = df["symbol"].map(last_date)
    df["momentum"] = df["symbol"].map(momentum)

    # --- フィルターファネル(各段階の残数を記録) ---
    funnel: list[dict] = [{"stage": "ユニバース", "count": int(len(df))}]

    df = df[df["include_for_collection"] == 1]
    funnel.append({"stage": "収集対象", "count": int(len(df))})

    df = df[df["low_liquidity_candidate"] == 0]
    funnel.append({"stage": "流動性フィルター", "count": int(len(df))})

    df = df[df["is_financial"] == 0]
    funnel.append({"stage": "金融除外", "count": int(len(df))})

    max_price_date = df["price_date"].max()
    fresh = df["close"].notna() & (
        (max_price_date - df["price_date"]).dt.days <= PRICE_STALE_DAYS
    )
    df = df[fresh]
    funnel.append({"stage": "株価あり(7日以内)", "count": int(len(df))})

    # --- 指標計算 ---
    df = df.copy()
    shares_out = df["issued_shares"] - df["treasury_shares"].fillna(0)
    df["mcap_million"] = df["close"] * shares_out / 1e6
    df.loc[df["mcap_million"] <= 0, "mcap_million"] = pd.NA
    df["mcap_million"] = pd.to_numeric(df["mcap_million"], errors="coerce")

    df["earnings_yield"] = df["net_income"] / df["mcap_million"]
    df["book_yield"] = df["equity"] / df["mcap_million"]
    df["cfo_yield"] = df["cash_flow_operating"] / df["mcap_million"]
    dividend = df["forecast_annual_dividend_per_share"].fillna(df["annual_dividend_per_share"])
    df["dividend_yield"] = dividend / df["close"]
    df["forecast_earnings_yield"] = df["forecast_eps"] / df["close"]

    df["n_value_metrics"] = df[VALUE_METRICS].notna().sum(axis=1)
    df = df[df["n_value_metrics"] >= MIN_VALUE_METRICS]
    funnel.append({"stage": f"バリュー指標{MIN_VALUE_METRICS}つ以上", "count": int(len(df))})

    quality = (df["operating_profit"] > 0) & (df["cash_flow_operating"] > 0)
    df = df[quality.fillna(False)]
    funnel.append({"stage": "クオリティゲート(営業黒字・営業CF黒字)", "count": int(len(df))})

    # --- 複合スコア(クオリティ通過集団内でランク付け) ---
    for m in VALUE_METRICS:
        df[f"rank_{m}"] = winsorized_rank(df[m])
    df["composite"] = df[[f"rank_{m}" for m in VALUE_METRICS]].mean(axis=1)

    # --- モメンタムフィルター(下位20%と履歴不足を除外) ---
    mom_floor = df["momentum"].quantile(MOMENTUM_CUT)
    df = df[df["momentum"].notna() & (df["momentum"] > mom_floor)]
    funnel.append({"stage": "モメンタムフィルター(下位20%除外)", "count": int(len(df))})

    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1

    held_symbols = {p["symbol"] for p in portfolio["positions"]}

    def row_to_entry(row) -> dict:
        ey = row["earnings_yield"]
        by = row["book_yield"]
        fey = row["forecast_earnings_yield"]
        return {
            "rank": int(row["rank"]),
            "symbol": row["symbol"],
            "name": row["company_name"],
            "sector": row["sector_33"],
            "close": safe_round(row["close"], 1),
            "mcap_oku": safe_round(row["mcap_million"] / 100, 0),
            "composite": safe_round(row["composite"], 1),
            "per": safe_round(1 / ey, 1) if pd.notna(ey) and ey > 0 else None,
            "forecast_per": safe_round(1 / fey, 1) if pd.notna(fey) and fey > 0 else None,
            "pbr": safe_round(1 / by, 2) if pd.notna(by) and by > 0 else None,
            "dividend_yield_pct": safe_round(row["dividend_yield"] * 100, 2)
            if pd.notna(row["dividend_yield"]) else None,
            "cfo_yield_pct": safe_round(row["cfo_yield"] * 100, 1)
            if pd.notna(row["cfo_yield"]) else None,
            "momentum_pct": safe_round(row["momentum"] * 100, 1),
            "held": row["symbol"] in held_symbols,
        }

    ranking = [row_to_entry(r) for _, r in df.head(TOP_N).iterrows()]

    # --- 保有状況 ---
    rank_by_symbol = dict(zip(df["symbol"], df["rank"]))
    positions_out = []
    total_value = total_cost = 0.0
    for p in portfolio["positions"]:
        close = last_close.get(p["symbol"])
        value = close * p["shares"] if close is not None else None
        cost = p["avg_cost"] * p["shares"]
        if value is not None:
            total_value += value
            total_cost += cost
        positions_out.append({
            **p,
            "close": safe_round(close, 1),
            "value": safe_round(value, 0),
            "pnl": safe_round(value - cost, 0) if value is not None else None,
            "pnl_pct": safe_round((value / cost - 1) * 100, 1)
            if value is not None and cost > 0 else None,
            "current_rank": rank_by_symbol.get(p["symbol"]),
        })

    # --- 月次リバランス提案(ヒステリシス: 保有はランク20位以内なら継続) ---
    suggestions = {"sells": [], "buys": [], "note": None}
    if is_monthly:
        keep: set[str] = set()
        for p in positions_out:
            r = p["current_rank"]
            if r is not None and r <= TOP_N:
                keep.add(p["symbol"])
            else:
                suggestions["sells"].append({
                    "symbol": p["symbol"], "name": p["name"], "shares": p["shares"],
                    "reason": "ランク圏外" if r is None else f"ランク{r}位に低下",
                })
        slots = PORTFOLIO_N - len(keep)
        budget_per_slot = portfolio["cash_budget_jpy"] / PORTFOLIO_N
        for e in ranking:
            if slots <= 0:
                break
            if e["symbol"] in held_symbols or e["close"] is None:
                continue
            shares = max(1, int(budget_per_slot // e["close"]))
            suggestions["buys"].append({
                "symbol": e["symbol"], "name": e["name"], "close": e["close"],
                "shares": shares,
                "amount": safe_round(shares * e["close"], 0),
                "over_budget": e["close"] > budget_per_slot,
            })
            slots -= 1
        suggestions["note"] = (
            f"予算 {portfolio['cash_budget_jpy']:,}円 ÷ {PORTFOLIO_N}枠 = "
            f"1枠あたり約{budget_per_slot:,.0f}円。S株(1株単位)成行で発注し、"
            "約定後に data/portfolio.json を更新すること。"
        )

    # --- 前号との IN/OUT ---
    reports_dir = REPO / "content" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    prev = previous_report(reports_dir, report_id)
    in_out = {"in": [], "out": [], "prev_id": None}
    if prev is not None:
        prev_symbols = {e["symbol"]: e["name"] for e in prev.get("ranking", [])}
        cur_symbols = {e["symbol"]: e["name"] for e in ranking}
        in_out = {
            "prev_id": prev["id"],
            "in": [{"symbol": s, "name": n} for s, n in cur_symbols.items() if s not in prev_symbols],
            "out": [{"symbol": s, "name": n} for s, n in prev_symbols.items() if s not in cur_symbols],
        }

    fiscal_counts = fundamentals["fiscal_period"].value_counts().to_dict()
    report = {
        "id": report_id,
        "generated_at": now.isoformat(timespec="seconds"),
        "edition": "monthly" if is_monthly else "weekly",
        "freshness": {
            "fundamentals_run_id": fundamentals["run_id"].iloc[0],
            "fundamentals_fetched_at": fundamentals["fetched_at_utc"].max(),
            "fiscal_periods": {str(k): int(v) for k, v in fiscal_counts.items()},
            "price_max_date": prices_meta["max_date"],
            "price_failed": prices_meta["n_failed"],
            "eps_check_warn": int((fundamentals["eps_check"] == "warn").sum()),
        },
        "funnel": funnel,
        "ranking": ranking,
        "portfolio": {
            "as_of": portfolio["as_of"],
            "cash_budget_jpy": portfolio["cash_budget_jpy"],
            "positions": positions_out,
            "total_value": safe_round(total_value, 0),
            "total_pnl": safe_round(total_value - total_cost, 0),
        },
        "suggestions": suggestions if is_monthly else None,
        "in_out": in_out,
        "disclaimer": DISCLAIMER,
    }

    out_path = reports_dir / f"{report_id}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"号: {report_id} ({report['edition']})")
    for f in funnel:
        print(f"  {f['stage']}: {f['count']}")
    print(f"上位{TOP_N}銘柄:")
    for e in ranking[:5]:
        print(f"  {e['rank']:>2}. {e['symbol']} {e['name']} 複合{e['composite']}")
    print(f"出力: {out_path}")


if __name__ == "__main__":
    main()
