"""Safe JV-Link option=4 history backfill runner.

Use after the option=4 setup probe has completed successfully.

Modes:
- default: write into a temporary copy of the existing database only
- --apply: write into the real HorseRacingAI database using INSERT OR IGNORE

This bypasses the existing (possibly stale) history_collection_progress resume
marker and requests setup data with JVOpen option=4, which is required for the
older missing history.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Allow direct execution as: python scripts/jvlink_history_option4_backfill.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.jvlink_loader as jl
from scripts.database import DEFAULT_DATABASE_PATH, RaceRepository

JST = timezone(timedelta(hours=9))


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def parse_ymd(value: str):
    return datetime.strptime(value, "%Y%m%d").date()


def db_stats(path: Path) -> dict:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_ymd", required=True)
    ap.add_argument("--to", dest="to_ymd", required=True)
    ap.add_argument("--sid", default="UNKNOWN")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/baseline/OPTION4_HISTORY_BACKFILL.json"),
    )
    args = ap.parse_args()

    from_date = parse_ymd(args.from_ymd)
    to_date = parse_ymd(args.to_ymd)
    if from_date > to_date:
        raise SystemExit("--from must be <= --to")

    real_db = DEFAULT_DATABASE_PATH
    if args.apply:
        target_db = real_db
        backup = real_db.with_suffix(f".pre_option4_{datetime.now():%Y%m%d_%H%M%S}.bak")
        if real_db.exists():
            shutil.copy2(real_db, backup)
    else:
        tmp_dir = Path(".runtime") / "option4_backfill_test"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        target_db = tmp_dir / "horse_racing_test.db"
        if real_db.exists():
            shutil.copy2(real_db, target_db)
        else:
            target_db.unlink(missing_ok=True)
        backup = None

    report = {
        "status": "RUNNING",
        "started_at_jst": now(),
        "from": args.from_ymd,
        "to": args.to_ymd,
        "apply": bool(args.apply),
        "target_db": str(target_db),
        "backup": str(backup) if backup else None,
        "before": db_stats(target_db),
    }

    # get_race_history uses this module-level constant. For this process only,
    # switch accumulated-history reads from option=1 to setup option=4.
    jl.ACCUMULATED_OPTION = 4

    try:
        result = jl.JVLinkClient(sid=args.sid, max_prepare_retries=9000).get_race_history(
            from_date, to_date
        )
        repo = RaceRepository(database_path=target_db)
        saved = repo.save_history(result.entries, from_date, to_date)
        report.update(
            status="SUCCESS",
            fetched_entries=len(result.entries),
            saved_entries=saved,
            parse_errors=result.error_count,
            after=db_stats(target_db),
            finished_at_jst=now(),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        report.update(status="FAILED", error=repr(exc), finished_at_jst=now())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
