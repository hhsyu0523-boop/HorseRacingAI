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
