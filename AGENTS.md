# AGENTS.md — 週報 (SHUHO)

このリポジトリは、**毎週土曜朝に自動更新される日本株スクリーナー「週報」**です。
EDINET財務データ×株価で割安×モメンタム複合スコアを計算し、GitHub Pagesに公開します。
朝報(choho)と同じ「JSON正本 → push → Actions がデプロイ」方式で運用します。

## 絶対のルール

- **自動売買はしない**。このシステムの出力は銘柄リストと売買提案のみ。発注は人間が行う
- 定期実行(Actions)が触ってよいのは **`content/reports/` 配下のみ**。
  `scripts/`・`data/`・設定ファイルを自動実行で変更しない
- `data/portfolio.json` は**発注後に人間が手動更新する唯一のファイル**
- 欠損値は補完しない。欠損は欠損としてレポートに明記する(EDINETパイプラインの思想を踏襲)
- EDINET_API_KEYの値を出力・保存・コミットしない(このリポジトリの週次実行では不要。キーが要るのはローカルの財務データ更新のみ)
- スコアリングルールの変更は月次レビュー時のみ。ドローダウン中の場当たり的変更は禁止

## 月次オペレーション(人間の作業・毎月第1土曜)

1. Pagesの「月次リバランス号」を開く(月初の土曜に自動生成される)
2. 売り候補(保有のランク圏外落ち)と買い候補(**S判定=非常に割安のみ**)を確認
3. **買いは義務ではない。** S候補がない月・妙味を感じない月は現金温存
4. 買う場合は月曜朝にSBI証券で**単元株(100株)**成行発注
5. 約定後 `data/portfolio.json` を更新して push(shares・avg_cost・acquired)

## 四半期オペレーション(EDINET検算データ更新・ローカル・任意)

スコアの一次ソースはYahoo Finance(週次自動)。EDINETスナップショットはEPS検算専用
なので、更新は任意。決算発表シーズン後(6月中旬・9月中旬・12月中旬・3月中旬)に:

```powershell
# 1. EDINET再収集(../Codex リポジトリで・約40分)
cd C:\Users\tomak\Documents\Codex
$runId = 'edinet-live1000-' + (Get-Date -Format yyyyMMdd-HHmmss)
node skills/collect-jp-equity-data/scripts/run_collection_batches.mjs `
  --universe-csv config/universe/jp_equity_universe_target_latest.csv `
  --out-dir ../JP_Equity_EDINET_Data --sources edinet --mode live `
  --max-symbols 1000 --min-symbols 1000 --target-period FY2026 `
  --edinet-lookback-days 365 --request-interval-ms 180 --run-id $runId
npm.cmd run db:jp-equity:build

# 2. スナップショット書き出し → コミット
cd C:\Users\tomak\Documents\トレードシステム
.venv\Scripts\python scripts/export_fundamentals.py
git add data/fundamentals.csv data/universe.csv
git commit -m "財務スナップショット更新"; git push
```

## 停止条件(設計書v1.0 §2/§5 を踏襲・事前コミットメント)

- この戦略単体でピークから **−30%** → 新規買い停止。原因分析が終わるまで再開禁止
- 24ヶ月運用して「現金＋インデックス積立」に総合で劣後し続けたら撤退

## リポジトリ構成

```
content/reports/YYYY-MM-DD.json  # 週次スクリーニング結果(正本・Actionsが追加)
data/fundamentals.csv            # EDINET財務スナップショット(四半期更新)
data/universe.csv                # 1000銘柄ユニバース(sector/フラグ付き)
data/portfolio.json              # 現在の保有(人間が更新)
scripts/                         # 決定論的スクリプト(Python)
templates/                       # 週報ページのJinja2テンプレート
.github/workflows/weekly-screen.yml  # 毎週土曜07:00 JST + 手動実行
```
