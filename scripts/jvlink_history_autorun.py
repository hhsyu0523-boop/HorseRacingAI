"""One-command JV-Link historical validation + production backfill runner.

Flow:
1) Read one known JRA race day (2021-07-18) into a disposable DB copy.
2) Abort if zero historical rows are returned.
3) If validation succeeds, back up the real DB.
4) Backfill the missing production range in bounded chunks:
   2021-08-16 .. 2025-07-25.

Important: this runner patches JVRead handling for this process only so that
JVRead=-1 is treated as a file boundary (continue), not end-of-stream.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.jvlink_loader as jl
from scripts.database import DEFAULT_DATABASE_PATH, RaceRepository

JST = timezone(timedelta(hours=9))
OUT = ROOT / "outputs" / "baseline" / "HISTORY_AUTORUN.json"
TEST_DB = ROOT / ".runtime" / "history_autorun" / "horse_racing_test.db"


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def stats(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    with sqlite3.connect(path) as con:
        row = con.execute(
            "SELECT MIN(race_date), MAX(race_date), COUNT(*), COUNT(DISTINCT race_key) FROM race_history"
        ).fetchone()
    return {
        "exists": True,
        "min_date": row[0],
        "max_date": row[1],
        "runner_rows": int(row[2]),
        "race_count": int(row[3]),
    }


def patched_iter_records(self):
    if not self._opened or self._adapter is None:
        raise jl.JVLinkError("JVRead", -111, "先にopen()を実行してください")
    prepare_retries = 0
    while True:
        code, data, filename = self._adapter.read(self.buffer_size)
        if code > 0:
            prepare_retries = 0
            yield data[:code]
            continue
        if code == -1:
            # JV-Link file boundary: continue to the next file.
            continue
        if code == 0:
            return
        if code == -3:
            prepare_retries += 1
            if prepare_retries > self.max_prepare_retries:
                raise jl.JVLinkError("JVRead", code, "データ準備の待機がタイムアウトしました")
            time.sleep(self.retry_interval)
            continue
        raise jl.JVLinkError("JVRead", code, f"filename={filename}")


jl.JVLinkClient.iter_records = patched_iter_records
jl.ACCUMULATED_OPTION = 4


def fetch_save(from_date: date, to_date: date, db_path: Path) -> dict:
    started = now()
    client = jl.JVLinkClient(sid="UNKNOWN", max_prepare_retries=9000)
    result = client.get_race_history(from_date, to_date)
    repo = RaceRepository(database_path=db_path)
    before = stats(db_path)
    saved = repo.save_history(result.entries, from_date, to_date)
    after = stats(db_path)
    return {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "started_at_jst": started,
        "finished_at_jst": now(),
        "fetched_entries": len(result.entries),
        "saved_entries": saved,
        "parse_errors": result.error_count,
        "before": before,
        "after": after,
    }


def write(report: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    report = {"status": "RUNNING", "started_at_jst": now(), "test": None, "chunks": []}
    try:
        TEST_DB.parent.mkdir(parents=True, exist_ok=True)
        if DEFAULT_DATABASE_PATH.exists():
            shutil.copy2(DEFAULT_DATABASE_PATH, TEST_DB)
        else:
            TEST_DB.unlink(missing_ok=True)

        test = fetch_save(date(2021, 7, 18), date(2021, 7, 18), TEST_DB)
        report["test"] = test
        if test["fetched_entries"] <= 0:
            report.update(
                status="FAILED_VALIDATION",
                message="2021-07-18 returned zero historical rows; production DB was not changed",
                finished_at_jst=now(),
            )
            write(report)
            return 2

        backup = DEFAULT_DATABASE_PATH.with_suffix(
            f".pre_5year_backfill_{datetime.now():%Y%m%d_%H%M%S}.bak"
        )
        shutil.copy2(DEFAULT_DATABASE_PATH, backup)
        report["backup"] = str(backup)
        report["production_before"] = stats(DEFAULT_DATABASE_PATH)

        chunks = [
            (date(2021, 8, 16), date(2021, 12, 31)),
            (date(2022, 1, 1), date(2022, 12, 31)),
            (date(2023, 1, 1), date(2023, 12, 31)),
            (date(2024, 1, 1), date(2024, 12, 31)),
            (date(2025, 1, 1), date(2025, 7, 25)),
        ]
        for start, end in chunks:
            chunk = fetch_save(start, end, DEFAULT_DATABASE_PATH)
            report["chunks"].append(chunk)
            write(report)

        report.update(
            status="SUCCESS",
            production_after=stats(DEFAULT_DATABASE_PATH),
            finished_at_jst=now(),
        )
        write(report)
        return 0
    except Exception as exc:
        report.update(status="FAILED", error=repr(exc), finished_at_jst=now())
        write(report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
