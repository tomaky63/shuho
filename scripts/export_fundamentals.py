"""EDINET研究DB(SQLite)から財務スナップショットを書き出す(ローカル実行専用)。

../JP_Equity_EDINET_Data/db/jp_equity_research.sqlite の最新runをピボットし、
data/fundamentals.csv(1行=1銘柄)と data/universe.csv を出力する。
週次のGitHub Actionsはこの出力(コミット済みCSV)だけを読む。
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(r"C:\Users\tomak\Documents\JP_Equity_EDINET_Data\db\jp_equity_research.sqlite")

# スコア計算に使う指標のみ書き出す(total_assets/net_assets/equity_ratioは
# 取り違えの疑いがあるため意図的に除外 — docs/design.md参照)
METRICS = [
    "net_sales",
    "operating_profit",
    "ordinary_profit",
    "net_income",
    "eps",
    "equity",
    "cash_flow_operating",
    "issued_shares",
    "treasury_shares",
    "average_shares",
    "annual_dividend_per_share",
    "forecast_annual_dividend_per_share",
    "forecast_eps",
]

EPS_CHECK_TOLERANCE = 0.25


def resolve_latest_run(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT run_id FROM ingest_runs WHERE target_symbol_count >= 100 "
        "ORDER BY generated_at_utc DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise SystemExit("実行対象のrunがDBに見つからない(target_symbol_count>=100)")
    return row[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--run-id", default=None, help="省略時は最新の大規模run")
    ap.add_argument("--out-dir", default=str(REPO / "data"))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    run_id = args.run_id or resolve_latest_run(con)

    nf = pd.read_sql_query(
        "SELECT symbol, metric, value, fiscal_period, period_kind, "
        "source_document_id, fetched_at_utc "
        "FROM normalized_financials "
        "WHERE run_id = ? AND missing_flag = 0 AND value IS NOT NULL",
        con,
        params=[run_id],
    )
    if nf.empty:
        raise SystemExit(f"run {run_id} に有効な財務レコードがない")

    wide = nf.pivot_table(index="symbol", columns="metric", values="value", aggfunc="first")
    wide = wide.reindex(columns=METRICS)

    meta = nf.groupby("symbol").agg(
        fiscal_period=("fiscal_period", "first"),
        source_document_id=("source_document_id", "first"),
        fetched_at_utc=("fetched_at_utc", "max"),
    )
    out = wide.join(meta)

    # 単位検算: net_income[百万円]*1e6 / average_shares ≒ eps[円]
    computed_eps = out["net_income"] * 1e6 / out["average_shares"]
    ratio = computed_eps / out["eps"]
    out["eps_check"] = "na"
    valid = ratio.notna() & (out["eps"] != 0)
    out.loc[valid & ((ratio - 1).abs() <= EPS_CHECK_TOLERANCE), "eps_check"] = "ok"
    out.loc[valid & ((ratio - 1).abs() > EPS_CHECK_TOLERANCE), "eps_check"] = "warn"

    uni = pd.read_sql_query(
        "SELECT symbol, company_name, sector_33, market_category, scale_category, "
        "is_financial, low_liquidity_candidate, include_for_collection, priority_score "
        "FROM universe_symbols",
        con,
    )
    con.close()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out.insert(0, "run_id", run_id)
    out.reset_index().to_csv(out_dir / "fundamentals.csv", index=False, encoding="utf-8-sig")
    uni.to_csv(out_dir / "universe.csv", index=False, encoding="utf-8-sig")

    n = len(out)
    print(f"run_id: {run_id}")
    print(f"銘柄数: {n}")
    for m in METRICS:
        print(f"  {m}: {out[m].notna().sum()}/{n}")
    print(f"eps検算: ok={int((out['eps_check'] == 'ok').sum())} "
          f"warn={int((out['eps_check'] == 'warn').sum())} "
          f"na={int((out['eps_check'] == 'na').sum())}")
    print(f"出力: {out_dir / 'fundamentals.csv'}, {out_dir / 'universe.csv'}")


if __name__ == "__main__":
    main()
