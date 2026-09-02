"""Audit the local HorseRacingAI SQLite state without exposing licensed raw data."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "database" / "horse_racing.db"
JST = timezone(timedelta(hours=9))


def scalar(conn: sqlite3.Connection, sql: str):
    row = conn.execute(sql).fetchone()
    return None if row is None else row[0]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def audit(db_path: Path) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        result = {
            "audited_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
            "database_path": str(db_path),
            "database_size_bytes": db_path.stat().st_size,
            "tables": {},
        }

        for table in (
            "race_history",
            "feature_history",
            "race_list",
            "race_entries",
            "history_collection_progress",
            "race_prediction",
        ):
            exists = table_exists(conn, table)
            info = {"exists": exists}
            if exists:
                info["rows"] = int(scalar(conn, f"SELECT COUNT(*) FROM {table}") or 0)
            result["tables"][table] = info

        if table_exists(conn, "race_history"):
            result["history"] = {
                "min_date": scalar(conn, "SELECT MIN(race_date) FROM race_history"),
                "max_date": scalar(conn, "SELECT MAX(race_date) FROM race_history"),
                "runner_rows": int(scalar(conn, "SELECT COUNT(*) FROM race_history") or 0),
                "race_count": int(scalar(conn, "SELECT COUNT(DISTINCT race_key) FROM race_history") or 0),
                "horse_count": int(scalar(conn, "SELECT COUNT(DISTINCT horse_name) FROM race_history") or 0),
                "missing_finish_position": int(scalar(conn, "SELECT COUNT(*) FROM race_history WHERE finish_position IS NULL") or 0),
            }

        if table_exists(conn, "feature_history"):
            columns = [r[1] for r in conn.execute("PRAGMA table_info(feature_history)").fetchall()]
            result["features"] = {
                "rows": int(scalar(conn, "SELECT COUNT(*) FROM feature_history") or 0),
                "column_count": len(columns),
                "columns": columns,
            }

        if table_exists(conn, "history_collection_progress"):
            result["collection_progress"] = [
                {"range_start": row[0], "completed_through": row[1]}
                for row in conn.execute(
                    "SELECT range_start, completed_through FROM history_collection_progress ORDER BY range_start"
                ).fetchall()
            ]

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "baseline" / "LOCAL_STATE.json",
    )
    args = parser.parse_args()

    report = audit(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
