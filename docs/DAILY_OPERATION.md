# Issue #2 — daily operation, Phase A

**実運用の受入は未完了です。Phase B（④〜⑥）は開始していません。**
①〜③を分離して実装し、合成データによる一連の正常系・異常系をテストしました。
実機では①の当日24レース・287頭取得、および③の公式確定結果2レース取得を確認。
②の当日PRE固定・③の当日予測との実照合はBLOCKです。

## 既存経路との関係

基準は `automation/predict_today.ps1 → main.py predict-race →
scripts/prediction_engine.py → scripts/ensemble_predict.py` と
`config/ensemble.json` の既存設定です。これらのファイルは変更しません。
新しい入口は、同じ `EnsemblePredictionEngine` を専用一時DBで呼び出します。
旧PredictionEngineの買い目生成はPhase Bのため呼び出しません。

必要モデルは `winner_model.pkl`, `place_model.pkl`, `winner_xgb.pkl`,
`place_xgb.pkl` の4つです。`best_model.pkl`への代替、再学習、重み変更はありません。
既存feature_engineの意味・順序を使い、モデル列が一致しない、必要な過去走・
騎手履歴・馬場・数値が不足する場合は日全体をBLOCKします。
新馬等を便宜的なゼロ特徴量で埋めて日全体を成功扱いにしません。

## Windows入口

指定ブランチのworktree内から次を実行します。Python 3.11 x64（モデル環境）と
JV-Linkが使えるPythonを分けています。新ライブラリ・モデルは導入していません。

```powershell
powershell.exe -NoProfile -File automation\run_daily_phase_a.ps1 `
  -DataRoot C:\Users\HidekazuHasegawa\HorseRacingAI
```

日付はJSTの当日。旧autorum/predict_todayはmainへpushする既存処理を含むため、
この入口から呼び出しません。既存タスクの変更・新規タスク登録はしていません。
タスクスケジューラの登録とGitHub定期監視は⑥に属し、実機Phase A受入後に実装・
確認する必要があります。このPhase A版を無人運用完成とは扱わないでください。

## 当日の順序

1. 4モデルの存在と設定を検査し、JVInitと0B15で当日番組を取得します。
   JVReadのファイル境界(-1)を越えて読み、0まで到達したことを記録します。
   RA登録頭数とSE数、開催場の1〜12R、日付・識別子を照合。不完全ならBLOCK。
   JV-Link呼び出しは別プロセスで120秒の上限を設けます。
2. 取得後10分以内、かつ**全レースの発走前**にのみPREを作成します。
   PRE入力は許可した番組・出馬表項目だけ。SEのオッズ・人気・当日結果は除外。
   履歴DBはread-onlyで対象日より前の行だけ読み、当日のfeature_historyは使いません。
   全件推論後にも締切を確認し、全頭の出力・有限確率が揃った場合だけ日付単位で固定。
3. PREが存在し発走後になったら0B12を取得し、確定済み結果だけを照合します。
   Top1、勝者の予測Top3内率、Exact12、Exact123を分母付きで保存。
   結果待ちはWARNING、同着・不完全結果は未解決扱いで成功にしません。

PREは全レース1行ずつ、Top3・全頭順位・スコア・確率・生成時刻・発走時刻・
`result_information_used=false`・モデル/設定/入力SHA256を含むJSONです。
一時ファイルをflushした後、置換不能なhard-link作成で公開します。
hard-linkが使えない保存先ではエラーとなり、危険な上書き処理には切り替えません。

## 保存先と再実行

すべてこのworktreeの `.daily_runtime/YYYYMMDD/` 内。生の契約データはGitに追加しません。

- `PRE.json`: 固定された日単位PRE。既存なら再予測せずハッシュを検証して再利用。
- `source/`: 取得ごとの番組／結果。用途別・UUID別に保存。
- `final/<hash>.json`: 結果内容ごとのFINAL。公式訂正時も旧版を保持。
- `FINAL_LATEST.json`: 最新FINAL。PREのハッシュと紐付け。
- `runs/<uuid>.json`, `STATUS.json`: 実行記録と最新の機械可読状態。
- `RUN.lock`: 同時実行を拒否。異常終了後に残った場合は、実行プロセスがないことを
  確認してから管理者が解除。自動的に他プロセスのロックを奪いません。

結果取得が一時的に欠落して既存FINALより件数が減る場合はBLOCKして旧FINALを維持。
成功したPREがない日に、結果からPREを復元したり過去時刻で保存したりしません。

## 状態

| 状態 | 意味 |
|---|---|
| SUCCESS | 当該段階が正常終了。全体SUCCESSはPREと全レース結果が揃った場合のみ |
| WARNING | 有効なPREがあり結果待ち |
| BLOCKED | モデル・データ・時刻・整合性・排他などの条件不足 |
| FAILED | 想定外の処理例外 |

現時点で購入API・購入候補・ROI出力はありません。状態には常に
`phase_b=NOT_STARTED_PHASE_A_GATE` を明記します。

## 2026-09-19の証拠と未完了事項

- `docs/evidence/daily-20260919/live_status.json`: ①成功24R/287頭、②③BLOCK。
- `live_acquisition.json`, `live_results_fetch.json`: 実機のJVInit/Open/Readコードと件数。
- `tests.txt`, `synthetic_phase_a.json`: 自動テストと合成PRE/FINAL。実績精度ではありません。
- 本日の最初の発走09:45に対し、最初の完全取得は09:53。遡及PREは作りません。
- 指定ブランチと元作業フォルダーのmodels/に指定4モデルがなく、互換性確認不能。
- 正式4モデルの配置と互換性確認、次の対象日の最初の発走前の固定、発走後の
  実照合が必要です。それを実証してから④選別、⑤利益、⑥無人監視へ進みます。
- Stable/main、本番DB、既存モデル、既存PRE、自動実行タスクは変更していません。

テスト再実行（Python標準ライブラリのみ）:

```powershell
python tests/verify_daily_phase_a.py --output docs/evidence/daily-20260919
```

仕様根拠: [JV-Data仕様](https://jra-van.jp/dlb/sdv/sdk/JV-Data4901.pdf)、
[JVReadファイル境界の公式回答](https://developer.jra-van.jp/t/topic/120)。
