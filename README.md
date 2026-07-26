# HorseRacingAI
Horse Racing AI Development Project

## Phase 3 Step 2

JRA-VANのレース詳細・出馬表を条件指定で取得し、SQLiteへ保存します。

### 変更ファイル

- `main.py`
- `scripts/jvlink_loader.py`
- `scripts/database.py`
- `README.md`
- `.gitignore`

### 実装内容

- `race-list --date YYYYMMDD` による指定日の全レース取得
- レース名、距離、馬場、回り、条件、発走時刻の表示
- `race-entries --race RACE_KEY` による指定レースの出馬表取得
- 馬名、枠、馬番、騎手、調教師、性齢、斤量、人気、オッズの表示
- `database/horse_racing.db` の自動作成
- `race_schedule`、`race_list`、`race_entries` テーブルへのUPSERT保存
- JV-Link接続、取得件数、保存件数、処理時間のINFOログ
- SID未設定、JV-Linkエラー、データなし、SQLite保存失敗の処理

`RACE_KEY` は日付8桁、競馬場コード2桁、R番号2桁を連結した12桁です。
例えば `202607260101` は2026年7月26日、競馬場コード01、1Rを表します。

### 実行コマンド

```powershell
py -3.13-32 main.py race-schedule
py -3.13-32 main.py race-list --date 20260726
py -3.13-32 main.py race-entries --race 202607260101
```

`.venv32` を利用する場合:

```powershell
.\.venv32\Scripts\python.exe main.py race-list --date 20260726
.\.venv32\Scripts\python.exe main.py race-entries --race 202607260101
```

### サンプル実行結果

```text
========================
2026-07-26 札幌
========================
 1R 09:50 2歳未勝利           芝1200m 右 未勝利 [202607260101]

========================
札幌 1R [202607260101]
========================
枠 馬番 馬名             性齢 騎手       調教師     斤量 人気 オッズ
 1    1 サンプルホース     牡2  サンプル騎手 サンプル師 55.0    1    3.2
```

実際の表示内容はJRA-VANから取得したデータにより異なります。

## Phase 3 Step 3

JV-Linkから指定期間の確定済みレース結果を取得し、
`database/horse_racing.db` の `race_history` テーブルへ蓄積します。

### 実装内容

- `fetch-history --from YYYYMMDD --to YYYYMMDD` による期間指定取得
- 開催日、競馬場、R、レース名、距離、馬場、天候、馬名、騎手、
  人気、オッズ、着順、タイム、上がり3F、通過順位、馬体重、斤量の保存
- レースキーと馬番を一意キーにした重複保存の防止
- 開始日ごとの完了日を記録し、中断後は未処理日の先頭から再開
- 取得件数、保存件数、処理時間、エラー件数のINFOログ出力

### 実行例

32bit Python 3.13を直接指定する場合:

```powershell
py -3.13-32 main.py fetch-history --from 20250101 --to 20260726
```

`.venv32`を使用する場合:

```powershell
.\.venv32\Scripts\python.exe main.py fetch-history --from 20250101 --to 20260726
```

同じ開始日で再実行すると、
`history_collection_progress` に記録された完了日の翌日から再開します。

### サンプル実行結果

```text
INFO scripts.jvlink_loader: 接続開始 (SID=UNKNOWN)
INFO scripts.jvlink_loader: 接続成功
INFO scripts.jvlink_loader: JVOpen成功 (dataspec=RACE, read=12345, download=0)
INFO __main__: 取得件数: 9876
INFO __main__: 保存件数: 9876
INFO __main__: エラー件数: 0
INFO __main__: 処理時間: 12.345秒
履歴収集完了: 20250101 - 20260726 取得=9876 保存=9876 エラー=0
```

件数と処理時間は取得対象およびJRA-VANのデータ状況により異なります。

## Phase 4 Step 1

`race_history`からAI学習用特徴量を生成し、`feature_history`へ保存します。
各行の特徴量には対象レースより前の日付の成績だけを使用し、
対象レースの着順は学習ラベルとして別カラムへ保存します。

### 生成する特徴量

- 馬: 過去5走、平均着順、勝率、連対率、複勝率、平均人気、
  人気平均との差、平均上がり3F、平均タイム差
- 騎手: 過去成績の勝率、連対率、複勝率
- コース: 競馬場コード、距離、芝・ダート、右左、内外
- 馬場: 良、稍重、重、不良のフラグ
- 間隔: 前走からの日数
- クラス: 新馬、未勝利、1勝、2勝、3勝、OP、G3、G2、G1のフラグ

過去成績のない馬・騎手は集計値を`0`とし、`past_race_count`または
`jockey_race_count`で未出走と区別できます。

### 実行コマンド

```powershell
py -3.13-32 main.py build-features
```

`.venv32`を使用する場合:

```powershell
.\.venv32\Scripts\python.exe main.py build-features
```

### サンプル実行結果

```text
INFO __main__: 履歴件数: 9876
INFO __main__: 特徴量保存件数: 9876
INFO __main__: 処理時間: 0.321秒
特徴量生成完了: 履歴=9876 保存=9876
```

## Phase 4 Step 2

`feature_history`を日付順にtrain / validationへ分割し、LightGBMで
「1着」と「3着以内」の2種類の二値分類モデルを学習します。

### 32bit環境へのインストール

LightGBMの通常のWindows wheelは64bit用です。32bitの`.venv32`では、
Visual Studio Build Toolsの「C++によるデスクトップ開発」とCMakeを用意し、
LightGBMをWin32向けにソースビルドします。ARM64 Pythonにはインストールしません。

```powershell
.\.venv32\Scripts\python.exe -m pip install --upgrade pip cmake
.\.venv32\Scripts\python.exe -m pip install pandas numpy pywin32 pytest
.\.venv32\Scripts\python.exe -m pip install "lightgbm>=4.7.0" `
    --no-binary=lightgbm --config-settings=cmake.args="-AWin32"
```

インストール確認:

```powershell
.\.venv32\Scripts\python.exe -c "import lightgbm; print(lightgbm.__version__)"
```

### 学習

```powershell
py -3.13-32 main.py train-model
```

または:

```powershell
.\.venv32\Scripts\python.exe main.py train-model
```

日時が新しい20%の日付をvalidationとして使用します。同じ開催日のデータが
trainとvalidationに分かれることはありません。評価結果としてAccuracy、
Precision、Recall、F1、ROC-AUC、LogLossを表示します。

生成物:

- `models/winner_model.pkl`: 1着確率モデル
- `models/place_model.pkl`: 3着以内確率モデル
- `models/winner_feature_importance.csv`: 1着モデルの重要特徴量上位50件
- `models/place_feature_importance.csv`: 3着以内モデルの重要特徴量上位50件

### 予測

`feature_history`に存在するレースについて確率を降順表示します。

```powershell
py -3.13-32 main.py predict-model --race 202607260101 --model winner
py -3.13-32 main.py predict-model --race 202607260101 --model place
```

予測モジュールを直接実行することもできます。

```powershell
.\.venv32\Scripts\python.exe -m scripts.predict_model `
    --race 202607260101 --model winner
```

### 学習結果例

```text
[winner]
train=8000 validation=2000 validation_start=2026-03-01
Accuracy=0.912000 Precision=0.481000 Recall=0.376000
F1=0.422000 ROC-AUC=0.781000 LogLoss=0.264000
model=models\winner_model.pkl
importance=models\winner_feature_importance.csv
```

評価値は保存されている履歴と分割期間によって変わります。

## Phase 4 Step 3

`feature_history`をLightGBMと同じ日時境界で分割し、XGBoostによる
1着モデルと3着以内モデルを学習します。特徴量の並びとカテゴリ変換も
LightGBM版と共通化しているため、後続のアンサンブルで確率を結合できます。

### 32bit環境について

XGBoost公式のWindows wheelはx86-64版のみで、32bit Python用wheelは
公開されていません。`.venv32`で実行するには、XGBoost本体とPython packageを
Win32向けにソースビルドした独自wheelが必要です。ARM64 Pythonへは
インストールしません。

独自Win32 wheelを用意した後のインストール例:

```powershell
.\.venv32\Scripts\python.exe -m pip install C:\path\to\xgboost_win32.whl
.\.venv32\Scripts\python.exe -c "import xgboost; print(xgboost.__version__)"
```

### 学習

```powershell
py -3.13-32 main.py train-xgboost
```

または:

```powershell
.\.venv32\Scripts\python.exe main.py train-xgboost
```

Accuracy、Precision、Recall、F1、ROC-AUC、LogLossを表示し、
以下を保存します。

- `models/winner_xgb.pkl`: 1着確率モデル
- `models/place_xgb.pkl`: 3着以内確率モデル
- `models/importance_xgb.csv`: 両モデルの重要特徴量ランキング

### 予測

```powershell
py -3.13-32 main.py predict-xgboost --race 202607260101 --model winner
py -3.13-32 main.py predict-xgboost --race 202607260101 --model place
```

予測モジュールの直接実行:

```powershell
.\.venv32\Scripts\python.exe -m scripts.predict_xgboost `
    --race 202607260101 --model winner
```

### 学習結果例

```text
[xgboost:winner]
train=8000 validation=2000 validation_start=2026-03-01
Accuracy=0.914000 Precision=0.493000 Recall=0.382000
F1=0.430000 ROC-AUC=0.789000 LogLoss=0.259000
model=models\winner_xgb.pkl
importance=models\importance_xgb.csv
```

## Phase 4 Step 4

LightGBMとXGBoostのwinner/placeモデルを読み込み、最終予測を生成します。
初期設定ではwinner、placeともにLightGBM 50%、XGBoost 50%です。

### 実行コマンド

```powershell
py -3.13-32 main.py predict-race --race 202607260101
```

または:

```powershell
.\.venv32\Scripts\python.exe main.py predict-race --race 202607260101
```

出力項目:

- 勝率: winnerモデル2種類の重み付き平均
- 複勝率: placeモデル2種類の重み付き平均
- 順位: AI Scoreの降順
- AI Score: 勝率60%、複勝率40%の合成スコア
- Confidence: LightGBMとXGBoostの予測一致度

予測結果は実行ごとに一意のIDを付け、`race_prediction`テーブルへ
履歴として保存します。

### 重み設定

`config/ensemble.json`を編集すると、モデルとAI Scoreの重みを変更できます。
各グループの値は実行時に合計1.0となるよう自動的に正規化されます。

```json
{
  "winner": {"lightgbm": 0.5, "xgboost": 0.5},
  "place": {"lightgbm": 0.5, "xgboost": 0.5},
  "ai_score": {"winner": 0.6, "place": 0.4}
}
```

### 出力例

```text
202607260101 Ensemble Prediction
順位 馬番 馬名               勝率    複勝率  AI Score Confidence
   1    3 サンプルホース       31.20%  68.40%    46.08      94.50
   2    7 テストホース         24.10%  57.80%    37.58      91.20
```

## Phase 5

過去レースを時系列に並べ、直前までの全データで再学習して次の1レースを
予測する拡張窓方式のローリングバックテストを実行します。予測後は実績を
学習データへ追加し、次レースで再学習します。

### 実行コマンド

```powershell
py -3.13-32 main.py backtest
```

期間と初期学習レース数を指定する場合:

```powershell
py -3.13-32 main.py backtest `
    --from 20250101 --to 20260726 --min-training-races 20
```

`.venv32`を使用する場合:

```powershell
.\.venv32\Scripts\python.exe main.py backtest `
    --from 20250101 --to 20260726
```

### 評価内容

- 全体: 的中率、単勝回収率、複勝回収率、ROI、勝率、複勝率、
  平均実着順、平均AI Score
- 人気別: 1番人気、2〜5番人気、穴馬
- コース別: JRA10競馬場
- 距離別: 短距離（1400m以下）、マイル（1800m以下）、
  中距離（2400m以下）、長距離（2401m以上）
- 馬場別: 良、稍重、重、不良

各レースで単勝100円・複勝100円を購入する想定です。ROIは両券種の
合計投資額に対する損益率です。的中率は3着以内的中率として集計します。

### 払戻データ

Phase 5以降の`fetch-history`はJV-DataのHRレコードから単勝・複勝払戻金を
保存します。Phase 5適用前に収集済みの行は払戻列が空のため、正確な
複勝回収率を得るには対象期間の履歴を再取得してください。払戻欠損の
的中件数はHTMLレポートにも表示されます。

### 生成物

- `reports/backtest_report.html`: 全体・人気・コース・距離・馬場別集計
- `reports/backtest.csv`: レース単位の予測と収支
- `reports/feature_importance.html`: 累積重要特徴量上位50件
- `backtest_history`: SQLiteへ保存される実行別バックテスト履歴

レポートファイルはバックテストのたびに最新結果で更新され、SQLite履歴は
一意の実行IDを付けて追記されます。
