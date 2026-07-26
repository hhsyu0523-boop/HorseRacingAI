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
