"""Read-only JV-Link probe using a bounded setup date range.

Purpose: validate that RACE setup data can be obtained with option=4 when
FromTime-ToTime is supplied explicitly, without writing HorseRacingAI SQLite.

Default range is the first missing slice only: 2021-08-16 through 2021 year-end.
The setup ToTime uses month 12 / day 99 because JV-Link setup data is aggregated
by month and a normal Dec-31 boundary can omit the December setup bundle.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
PROG_ID = "JVDTLab.JVLink"
BUFFER_SIZE = 1_048_576


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--range", dest="date_range", default="20210816000000-20211299999999")
    p.add_argument("--timeout-minutes", type=int, default=30)
    p.add_argument("--max-positive-reads", type=int, default=20)
    p.add_argument("--output", type=Path, default=Path("outputs/baseline/JVLINK_YEAR_RANGE_PROBE.json"))
    args = p.parse_args()

    report = {
        "status": "RUNNING",
        "started_at_jst": now(),
        "dataspec": "RACE",
        "date_range": args.date_range,
        "option": 4,
        "database_write": False,
    }

    try:
        import win32com.client  # type: ignore
    except Exception as exc:
        emit({**report, "status": "FAILED", "stage": "IMPORT", "error": repr(exc)})
        return 1

    jv = None
    try:
        jv = win32com.client.Dispatch(PROG_ID)
        init_code = int(jv.JVInit("UNKNOWN"))
        report["jvinit"] = init_code
        if init_code != 0:
            emit({**report, "status": "FAILED", "stage": "JVInit"})
            return 1

        opened = jv.JVOpen("RACE", args.date_range, 4, 0, 0, "")
        open_code = int(opened[0])
        read_count = int(opened[1])
        download_count = int(opened[2])
        last_timestamp = str(opened[3])
        report.update({
            "jvopen": open_code,
            "read_count": read_count,
            "download_count": download_count,
            "last_file_timestamp": last_timestamp,
        })
        if open_code != 0:
            emit({**report, "status": "FAILED", "stage": "JVOpen"})
            return 1

        deadline = time.monotonic() + args.timeout_minutes * 60
        while True:
            status = int(jv.JVStatus())
            report["jvstatus"] = status
            if status < 0:
                emit({**report, "status": "FAILED", "stage": "JVStatus"})
                return 1
            if status >= download_count:
                break
            if time.monotonic() >= deadline:
                emit({**report, "status": "FAILED", "stage": "JVStatus", "reason": "timeout"})
                return 1
            print(f"JVStatus {status}/{download_count} ...", flush=True)
            time.sleep(2)

        types: Counter[str] = Counter()
        positive_reads = 0
        corrupt = None
        while positive_reads < args.max_positive_reads:
            r = jv.JVRead(" " * BUFFER_SIZE, BUFFER_SIZE, "")
            code = int(r[0])
            data = str(r[1])
            filename = str(r[3])
            if code > 0:
                positive_reads += 1
                for rec in data[:code].split("\r\n"):
                    if len(rec) >= 2:
                        types[rec[:2]] += 1
                continue
            if code in (0, -1):
                break
            if code == -3:
                time.sleep(0.2)
                continue
            if code in (-402, -403):
                corrupt = {"code": code, "filename": filename}
                # Read-only probe: do not delete automatically here.
                break
            emit({**report, "status": "FAILED", "stage": "JVRead", "read_code": code, "filename": filename})
            return 1

        report.update({
            "status": "SUCCESS" if corrupt is None else "CORRUPT_FILE_FOUND",
            "finished_at_jst": now(),
            "positive_reads_checked": positive_reads,
            "record_type_counts": dict(types),
            "corrupt_file": corrupt,
        })
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(report)
        return 0 if corrupt is None else 2
    finally:
        if jv is not None:
            try:
                jv.JVClose()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
