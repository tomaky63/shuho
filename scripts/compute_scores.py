"""バリュー複合×モメンタムのスコアを計算し、週報JSONを出力する。

入力:
  data/yahoo_fundamentals.csv  バリュー指標の一次ソース(fetch_fundamentals_yahoo.py)
  data/prices.csv              モメンタム用日足(fetch_prices.py)
  data/universe.csv            銘柄属性(EDINETユニバース)
  data/fundamentals.csv        EDINETスナップショット(EPS検算専用)
  data/portfolio.json          保有状況
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
PRICE_STALE_DAYS = 7

# --- 絶対バリュー判定(相対ランキングと独立した水準ベースの分類) ---
# 買い候補はS判定のみ。閾値の根拠と見直しルールは docs/design.md 参照
CLASS_CRITERIA = {
    "予想PER10倍以下": lambda r: r["per_yield"] >= 0.10,
    "PBR0.8倍以下": lambda r: r["book_yield"] >= 1 / 0.8,
    "配当利回り3.5%以上": lambda r: r["dividend_yield"] >= 0.035,
    "営業CF利回り12%以上": lambda r: r["cfo_yield"] >= 0.12,
}
DEEP_PER_YIELD = 0.125  # 予想PER8倍以下なら「特に安い」扱い


def value_class(row) -> tuple[str, list[str]]:
    """S=非常に割安(買い妙味大) / A=十分割安 / B=割安寄り / C=様子見"""
    hits = [name for name, ok in CLASS_CRITERIA.items()
            if pd.notna(row.get(_metric_of(name))) and ok(row)]
    n = len(hits)
    deep = pd.notna(row["per_yield"]) and row["per_yield"] >= DEEP_PER_YIELD
    if n == 4 or (n == 3 and deep):
        return "S", hits
    if n == 3:
        return "A", hits
    if n == 2:
        return "B", hits
    return "C", hits


def _metric_of(criterion: str) -> str:
    return {
        "予想PER10倍以下": "per_yield",
        "PBR0.8倍以下": "book_yield",
        "配当利回り3.5%以上": "dividend_yield",
        "営業CF利回り12%以上": "cfo_yield",
    }[criterion]

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
    return json.loads((reports_dir / f"{candidates[-1]}.json").read_text(encoding="utf-8-sig"))


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

    edinet = pd.read_csv(REPO / "data" / "fundamentals.csv", dtype={"symbol": str})
    yahoo = pd.read_csv(REPO / "data" / "yahoo_fundamentals.csv", dtype={"symbol": str})
    universe = pd.read_csv(REPO / "data" / "universe.csv", dtype={"symbol": str})
    portfolio = json.loads((REPO / "data" / "portfolio.json").read_text(encoding="utf-8"))
    prices_meta = json.loads((REPO / "data" / "prices_meta.json").read_text(encoding="utf-8"))
    last_close, last_date, momentum = load_prices(REPO / "data" / "prices.csv")

    df = universe.merge(yahoo, on="symbol", how="left")
    df = df.merge(edinet[["symbol", "eps", "eps_check"]], on="symbol", how="left")
    df["close"] = df["symbol"].map(last_close)
    df["price_date"] = pd.to_datetime(df["symbol"].map(last_date))
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

    # --- 指標計算(一次ソース: Yahoo。1株あたり値÷株価で単位問題を回避) ---
    df = df.copy()
    df["mcap_million"] = df["marketCap"] / 1e6

    df["earnings_yield"] = df["trailingEps"] / df["close"]
    df["forecast_earnings_yield"] = df["forwardEps"] / df["close"]
    df["book_yield"] = df["bookValue"] / df["close"]
    df["cfo_yield"] = df["operatingCashflow"] / df["marketCap"]
    # 無配企業の配当利回りは0として扱う(欠損と区別できないが保守的側)
    df["dividend_yield"] = df["dividendYield"].fillna(0) / 100
    # 絶対判定用: 予想益回り(欠損時は実績益回りで代用)
    df["per_yield"] = df["forecast_earnings_yield"].fillna(df["earnings_yield"])

    core_metrics = ["earnings_yield", "book_yield", "cfo_yield", "forecast_earnings_yield"]
    df["n_value_metrics"] = df[core_metrics].notna().sum(axis=1)
    df = df[df["n_value_metrics"] >= MIN_VALUE_METRICS]
    funnel.append({"stage": f"バリュー指標{MIN_VALUE_METRICS}つ以上", "count": int(len(df))})

    quality = (df["trailingEps"] > 0) & (df["operatingCashflow"] > 0)
    df = df[quality.fillna(False)]
    funnel.append({"stage": "クオリティゲート(最終黒字・営業CF黒字)", "count": int(len(df))})

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
        cls, hits = value_class(row)
        return {
            "value_class": cls,
            "class_detail": "・".join(hits) if hits else "該当基準なし",
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
    rank_by_symbol = {s: int(r) for s, r in zip(df["symbol"], df["rank"])}
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

    # --- 売買候補 ---
    # 買い: S判定(非常に割安)のみを単元株ベースで毎号提示。買いは義務ではない
    # 売り: 月次号のみ(ヒステリシス: 保有はランク20位以内なら継続)
    lot = int(portfolio.get("lot_size", 100))
    suggestions = {"sells": [], "buys": [], "note": None}
    for e in ranking:
        if e["value_class"] != "S" or e["held"] or e["close"] is None:
            continue
        suggestions["buys"].append({
            "symbol": e["symbol"], "name": e["name"], "close": e["close"],
            "value_class": e["value_class"], "class_detail": e["class_detail"],
            "shares": lot,
            "amount": safe_round(lot * e["close"], 0),
        })
    if is_monthly:
        for p in positions_out:
            r = p["current_rank"]
            if r is None or r > TOP_N:
                suggestions["sells"].append({
                    "symbol": p["symbol"], "name": p["name"], "shares": p["shares"],
                    "reason": "ランク圏外" if r is None else f"ランク{r}位に低下",
                })
    suggestions["note"] = (
        f"買い候補は絶対判定S(非常に割安)のみ・{lot}株単位の概算。"
        "買いは義務ではない — 候補ゼロの号は現金温存が判断。"
        "発注したら data/portfolio.json を更新すること。"
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

    # EDINET検算: 両ソースでEPSが取れる銘柄のうち±25%以内で一致する割合
    both = df[(df["eps_check"] == "ok") & (df["eps"] > 0) & (df["trailingEps"] > 0)]
    edinet_agree = int(((both["eps"] / both["trailingEps"] - 1).abs() <= 0.25).sum())

    n_target = funnel[3]["count"]  # 金融除外後のスコア対象数
    report = {
        "id": report_id,
        "generated_at": now.isoformat(timespec="seconds"),
        "edition": "monthly" if is_monthly else "weekly",
        "freshness": {
            "yahoo_fetched_at": str(yahoo["fetched_at_utc"].iloc[0]),
            "yahoo_missing": int(n_target - yahoo["symbol"].nunique()),
            "edinet_run_id": str(edinet["run_id"].iloc[0]),
            "edinet_eps_agree": f"{edinet_agree}/{len(both)}",
            "price_max_date": prices_meta["max_date"],
            "price_failed": prices_meta["n_failed"],
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
        "suggestions": suggestions,
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
        print(f"  {e['rank']:>2}. [{e['value_class']}] {e['symbol']} {e['name']} 複合{e['composite']}")
    dist = pd.Series([e["value_class"] for e in ranking]).value_counts().to_dict()
    print(f"上位{TOP_N}の判定分布: {dist}")
    print(f"買い候補(S判定): {len(suggestions['buys'])}銘柄")
    print(f"出力: {out_path}")


if __name__ == "__main__":
    main()
