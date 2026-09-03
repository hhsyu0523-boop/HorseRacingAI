"""JV-Link option=4 setup probe with bounded automatic corrupt-file recovery.

This probe does NOT write HorseRacingAI SQLite. It may delete only JVD files that
JVRead itself reports as corrupt via -402/-403, using JVFiledelete, then reopens
setup data in the same process. The user may need to answer JV-Link's initial
setup-source dialog once. Recovery is bounded to avoid infinite loops.
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
    ap.add_argument("--timeout-minutes", type=int, default=45)
    ap.add_argument("--max-corrupt-recoveries", type=int, default=20)
    ap.add_argument("--max-positive-reads", type=int, default=200)
    ap.add_argument("--output", type=Path,
                    default=Path("outputs/baseline/JVLINK_SETUP_RECOVERY_PROBE.json"))
    args = ap.parse_args()

    if platform.system() != "Windows":
        emit({"status": "FAILED", "message": "Windows required", "at_jst": now()})
        return 1
    if len(args.from_ymd) != 8 or not args.from_ymd.isdigit():
        emit({"status": "FAILED", "message": "--from must be YYYYMMDD", "at_jst": now()})
        return 1

    import win32com.client  # type: ignore

    report: dict = {
        "status": "RUNNING",
        "started_at_jst": now(),
        "from_ymd": args.from_ymd,
        "dataspec": "RACE",
        "option": 4,
        "database_write": False,
        "max_corrupt_recoveries": args.max_corrupt_recoveries,
        "attempts": [],
        "deleted_corrupt_files": [],
    }

    from_time = f"{args.from_ymd}000000"
    jv = win32com.client.Dispatch(PROG_ID)
    try:
        init_code = int(jv.JVInit(args.sid))
        report["jvinit"] = init_code
        if init_code != 0:
            report.update(status="FAILED", stage="JVInit", code=init_code,
                          finished_at_jst=now())
            emit(report, args.output)
            return 1

        for attempt_no in range(1, args.max_corrupt_recoveries + 2):
            attempt: dict = {"attempt": attempt_no, "started_at_jst": now()}
            report["attempts"].append(attempt)

            opened = jv.JVOpen("RACE", from_time, 4, 0, 0, "")
            open_code = int(opened[0])
            read_count = int(opened[1])
            download_count = int(opened[2])
            last_timestamp = str(opened[3])
            attempt.update(jvopen=open_code, read_count=read_count,
                           download_count=download_count,
                           last_file_timestamp=last_timestamp)
            if open_code != 0:
                report.update(status="FAILED", stage="JVOpen", code=open_code,
                              finished_at_jst=now())
                emit(report, args.output)
                return 1

            deadline = time.monotonic() + args.timeout_minutes * 60
            while True:
                status = int(jv.JVStatus())
                attempt["jvstatus"] = status
                if status < 0:
                    report.update(status="FAILED", stage="JVStatus", code=status,
                                  finished_at_jst=now())
                    emit(report, args.output)
                    return 1
                if status >= download_count:
                    break
                if time.monotonic() >= deadline:
                    report.update(status="FAILED", stage="JVStatus", reason="timeout",
                                  finished_at_jst=now())
                    emit(report, args.output)
                    return 1
                print(f"attempt {attempt_no}: JVStatus {status}/{download_count} ...", flush=True)
                time.sleep(2)

            types: Counter[str] = Counter()
            positive_reads = 0
            corrupt: tuple[int, str] | None = None
            while positive_reads < args.max_positive_reads:
                r = jv.JVRead(" " * BUFFER_SIZE, BUFFER_SIZE, "")
                code = int(r[0])
                data = str(r[1])
                filename = str(r[3])
                if code > 0:
                    positive_reads += 1
                    if len(data) >= 2:
                        types[data[:2]] += 1
                    continue
                if code == -1:
                    continue
                if code == 0:
                    break
                if code == -3:
                    time.sleep(0.2)
                    continue
                if code in (-402, -403):
                    corrupt = (code, filename)
                    break
                report.update(status="FAILED", stage="JVRead", code=code,
                              filename=filename, finished_at_jst=now())
                emit(report, args.output)
                return 1

            attempt.update(positive_reads_checked=positive_reads,
                           record_type_counts=dict(types))

            if corrupt is None:
                report.update(status="SUCCESS",
                              message="Setup download/read completed without corrupt-file error",
                              finished_at_jst=now())
                emit(report, args.output)
                return 0

            code, filename = corrupt
            attempt.update(corrupt_read_code=code, corrupt_filename=filename)
            if not filename:
                report.update(status="FAILED", stage="JVRead", code=code,
                              message="Corrupt read returned no filename",
                              finished_at_jst=now())
                emit(report, args.output)
                return 1

            if len(report["deleted_corrupt_files"]) >= args.max_corrupt_recoveries:
                report.update(status="FAILED", message="Recovery limit reached",
                              finished_at_jst=now())
                emit(report, args.output)
                return 2

            delete_code = int(jv.JVFiledelete(filename))
            attempt["jvfiledelete"] = delete_code
            if delete_code not in (0, -503):
                report.update(status="FAILED", stage="JVFiledelete", code=delete_code,
                              filename=filename, finished_at_jst=now())
                emit(report, args.output)
                return 1
            report["deleted_corrupt_files"].append(filename)

            # Official recovery pattern: close and reopen from JVOpen after removing
            # the specific bad file. Reinitialize the same COM object; no DB access.
            try:
                jv.JVClose()
            except Exception:
                pass
            reinit = int(jv.JVInit(args.sid))
            attempt["reinit_after_delete"] = reinit
            if reinit != 0:
                report.update(status="FAILED", stage="JVInit-after-delete", code=reinit,
                              finished_at_jst=now())
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
