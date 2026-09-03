"""One-shot read-only JV-Link setup probe with a single in-process recovery.

Purpose:
- Validate option=4 setup data without touching HorseRacingAI SQLite.
- Wait for JVStatus to reach JVOpen download_count before JVRead.
- If JVRead returns -402/-403, delete only the returned corrupt file,
  JVClose, and retry JVOpen exactly once in the SAME process.
- Never loop indefinitely and never write to the project database.

The initial JV-Link setup/start-kit dialog may still be shown by JV-Link itself.
If it appears, choose the normal setup source once. The script will not create
additional UI automation.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
PROG_ID = "JVDTLab.JVLink"
BUFFER_SIZE = 1_048_576


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def emit(payload: dict, output: Path | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text, flush=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_ymd", default="20210816")
    ap.add_argument("--sid", default="UNKNOWN")
    ap.add_argument("--timeout-minutes", type=int, default=30)
    ap.add_argument("--max-records", type=int, default=20)
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/baseline/JVLINK_OPTION4_PROBE_V2.json"),
    )
    args = ap.parse_args()

    if platform.system() != "Windows":
        emit({"status": "FAILED", "message": "Windows required", "at_jst": now()})
        return 1
    if len(args.from_ymd) != 8 or not args.from_ymd.isdigit():
        emit({"status": "FAILED", "message": "--from must be YYYYMMDD", "at_jst": now()})
        return 1

    try:
        import win32com.client  # type: ignore[import-untyped]
    except Exception as exc:
        emit({"status": "FAILED", "message": f"pywin32 import failed: {exc}", "at_jst": now()})
        return 1

    report: dict = {
        "status": "RUNNING",
        "started_at_jst": now(),
        "from_ymd": args.from_ymd,
        "dataspec": "RACE",
        "option": 4,
        "database_write": False,
        "max_recovery_reopens": 1,
        "attempts": [],
    }

    jv = win32com.client.Dispatch(PROG_ID)
    try:
        init_code = int(jv.JVInit(args.sid))
        report["jvinit"] = init_code
        if init_code != 0:
            report.update(status="FAILED", message="JVInit failed", code=init_code, finished_at_jst=now())
            emit(report, args.output)
            return 1

        from_time = f"{args.from_ymd}000000"
        for attempt_no in (1, 2):
            attempt: dict = {"attempt": attempt_no, "started_at_jst": now()}
            report["attempts"].append(attempt)

            opened = jv.JVOpen("RACE", from_time, 4, 0, 0, "")
            open_code = int(opened[0])
            read_count = int(opened[1])
            download_count = int(opened[2])
            last_timestamp = str(opened[3])
            attempt.update(
                jvopen=open_code,
                read_count=read_count,
                download_count=download_count,
                last_file_timestamp=last_timestamp,
            )
            if open_code != 0:
                report.update(status="FAILED", message="JVOpen failed", code=open_code, finished_at_jst=now())
                emit(report, args.output)
                return 1

            deadline = time.monotonic() + args.timeout_minutes * 60
            while True:
                status = int(jv.JVStatus())
                attempt["jvstatus"] = status
                if status < 0:
                    report.update(status="FAILED", message="JVStatus failed", code=status, finished_at_jst=now())
                    emit(report, args.output)
                    return 1
                if status >= download_count:
                    break
                if time.monotonic() >= deadline:
                    report.update(status="FAILED", message="JVStatus timeout", finished_at_jst=now())
                    emit(report, args.output)
                    return 1
                print(f"attempt {attempt_no}: JVStatus {status}/{download_count} ...", flush=True)
                time.sleep(2)

            types: Counter[str] = Counter()
            positive_records = 0
            corrupt: tuple[int, str] | None = None

            while positive_records < args.max_records:
                result = jv.JVRead(" " * BUFFER_SIZE, BUFFER_SIZE, "")
                code = int(result[0])
                data = str(result[1])
                filename = str(result[3])

                if code > 0:
                    positive_records += 1
                    if len(data) >= 2:
                        types[data[:2]] += 1
                    continue
                if code == -1:  # file boundary, continue to next file
                    continue
                if code == 0:  # all files complete
                    break
                if code == -3:
                    time.sleep(0.2)
                    continue
                if code in (-402, -403):
                    corrupt = (code, filename)
                    break

                report.update(status="FAILED", message="JVRead failed", code=code, filename=filename, finished_at_jst=now())
                emit(report, args.output)
                return 1

            attempt.update(
                positive_records_checked=positive_records,
                record_type_counts=dict(types),
            )

            if corrupt is None:
                report.update(
                    status="SUCCESS",
                    message="JVOpen/JVStatus/JVRead sequence succeeded",
                    finished_at_jst=now(),
                )
                emit(report, args.output)
                return 0

            code, filename = corrupt
            attempt.update(corrupt_read_code=code, corrupt_filename=filename)
            if not filename:
                report.update(status="FAILED", message="Corrupt JV file had no filename", code=code, finished_at_jst=now())
                emit(report, args.output)
                return 1

            delete_code = int(jv.JVFiledelete(filename))
            attempt["jvfiledelete"] = delete_code
            if delete_code not in (0, -503):
                report.update(status="FAILED", message="JVFiledelete failed", code=delete_code, finished_at_jst=now())
                emit(report, args.output)
                return 1

            if attempt_no == 2:
                report.update(
                    status="FAILED",
                    message="Second corrupt file encountered; stopped after the single permitted recovery",
                    code=code,
                    finished_at_jst=now(),
                )
                emit(report, args.output)
                return 1

            # Official recovery for -402/-403: delete returned file, JVClose,
            # then restart from JVOpen. Re-initialize the COM object once and
            # continue in this same process so the user does not need to relaunch.
            jv.JVClose()
            init_code = int(jv.JVInit(args.sid))
            attempt["reinit_after_delete"] = init_code
            if init_code != 0:
                report.update(status="FAILED", message="JVInit after recovery failed", code=init_code, finished_at_jst=now())
                emit(report, args.output)
                return 1

        report.update(status="FAILED", message="Unexpected end", finished_at_jst=now())
        emit(report, args.output)
        return 1
    finally:
        try:
            jv.JVClose()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
