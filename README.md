# 週報 (SHUHO) — 割安×モメンタム 日本株ウィークリースクリーナー

毎週土曜 07:00 JST に GitHub Actions が自動実行し、EDINET財務データ×株価から
「割安なのに下落トレンドではない」銘柄ランキングを GitHub Pages に公開する。

- 自動売買はしない。買い候補は**絶対判定S(非常に割安)のみ・単元株(100株)単位**で提示され、
  買うかどうかは人間が決める(買いは義務ではない)。売り候補は月初の土曜の「月次リバランス号」に掲載
- ランニングコスト **¥0/月**(EDINET API無料 / yfinance無料 / GitHub Actions・Pages無料枠)
- LLMは実行ループに入れない(全て決定論的スクリプト)

## 仕組み

```
[毎週土曜・GitHub Actions]
scripts/fetch_prices.py             yfinanceで1000銘柄の日足13ヶ月(モメンタム用)
scripts/fetch_fundamentals_yahoo.py yfinanceでPER/PBR/配当等(バリュー指標の一次ソース)
scripts/compute_scores.py           バリュー複合×モメンタム → content/reports/YYYY-MM-DD.json
scripts/build_site.py               JSON → dist/ (静的HTML) → GitHub Pages デプロイ

[四半期に1回・ローカル(任意・検算用)]
EDINET収集パイプライン(../Codex) → jp_equity_research.sqlite
  → scripts/export_fundamentals.py → data/fundamentals.csv (コミット・EPS検算専用)
```

## セットアップ(初回のみ)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## ローカル実行

```powershell
.venv\Scripts\python scripts/export_fundamentals.py   # 財務スナップショット更新(ローカルのみ)
.venv\Scripts\python scripts/fetch_prices.py          # 株価取得
.venv\Scripts\python scripts/compute_scores.py        # スコア計算 → レポートJSON
.venv\Scripts\python scripts/build_site.py            # サイト生成 → dist/
```

## 運用ルール

[AGENTS.md](AGENTS.md) と [docs/design.md](docs/design.md) を参照。

## 免責

本リポジトリおよび生成物は個人の学習・記録目的であり、投資勧誘・投資助言ではない。
投資判断は自己責任で行うこと。
