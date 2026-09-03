"""Safe, read-only JV-Link option=4 probe for historical RACE setup data.

This script does NOT write to HorseRacingAI SQLite. It validates the official
JV-Link sequence only:

JVInit -> JVOpen(option=4) -> JVStatus until download complete -> JVRead

If JVRead reports a corrupt zero-byte/content file (-402/-403), only the
filename returned by JVRead is deleted via JVFiledelete and the probe stops.
The caller can then run the probe once more. There is no automatic retry loop.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
DEFAULT_SID = "UNKNOWN"
DEFAULT_PROG_ID = "JVDTLab.JVLink"
BUFFER_SIZE = 1_048_576


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def fail(message: str, *, code: int | None = None, extra: dict | None = None) -> int:
    payload = {"status": "FAILED", "at_jst": now(), "message": message}
    if code is not None:
        payload["code"] = code
    if extra:
        payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_ymd", default="20210816")
    parser.add_argument("--sid", default=DEFAULT_SID)
    parser.add_argument("--timeout-minutes", type=int, default=30)
    parser.add_argument("--max-read-files", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/baseline/JVLINK_OPTION4_PROBE.json"),
    )
    args = parser.parse_args()

    if platform.system() != "Windows":
        return fail("JV-Link probe must run on Windows")
    if len(args.from_ymd) != 8 or not args.from_ymd.isdigit():
        return fail("--from must be YYYYMMDD")
    if args.timeout_minutes <= 0 or args.max_read_files <= 0:
        return fail("timeout/max-read-files must be positive")

    try:
        import win32com.client  # type: ignore[import-untyped]
    except Exception as exc:
        return fail(f"pywin32 import failed: {exc}")

    report: dict = {
        "status": "RUNNING",
        "started_at_jst": now(),
        "from_ymd": args.from_ymd,
        "dataspec": "RACE",
        "option": 4,
        "database_write": False,
    }

    jv = None
    try:
        jv = win32com.client.Dispatch(DEFAULT_PROG_ID)
        init_code = int(jv.JVInit(args.sid))
        report["jvinit"] = init_code
        if init_code != 0:
            return fail("JVInit failed", code=init_code, extra=report)

        from_time = f"{args.from_ymd}000000"
        opened = jv.JVOpen("RACE", from_time, 4, 0, 0, "")
        open_code = int(opened[0])
        read_count = int(opened[1])
        download_count = int(opened[2])
        last_timestamp = str(opened[3])
        report.update(
            {
                "jvopen": open_code,
                "read_count": read_count,
                "download_count": download_count,
                "last_file_timestamp": last_timestamp,
            }
        )
        if open_code != 0:
            return fail("JVOpen failed", code=open_code, extra=report)

        # Official SDK guidance: JVStatus returns downloaded file count; do not
        # call JVRead until it reaches JVOpen's download_count.
        deadline = time.monotonic() + args.timeout_minutes * 60
        last_status = None
        while True:
            status = int(jv.JVStatus())
            last_status = status
            report["jvstatus"] = status
            if status < 0:
                return fail("JVStatus failed", code=status, extra=report)
            if status >= download_count:
                break
            if time.monotonic() >= deadline:
                return fail(
                    "JVStatus download wait timed out",
                    extra={**report, "downloaded": status, "expected": download_count},
                )
            print(f"JVStatus {status}/{download_count} ...", flush=True)
            time.sleep(2.0)

        types: Counter[str] = Counter()
        positive_reads = 0
        deleted_file = None
        while positive_reads < args.max_read_files:
            result = jv.JVRead(" " * BUFFER_SIZE, BUFFER_SIZE, "")
            code = int(result[0])
            data = str(result[1])
            filename = str(result[3])
            if code > 0:
                positive_reads += 1
                # Count record prefixes without attempting full parsing.
                for rec in data[:code].split("\r\n"):
                    if len(rec) >= 2:
                        types[rec[:2]] += 1
                continue
            if code in (0, -1):
                break
            if code == -3:
                # Defensive only; JVStatus should have completed already.
                time.sleep(0.2)
                continue
            if code in (-402, -403):
                if not filename:
                    return fail(
                        "JVRead reported corrupt file but returned no filename",
                        code=code,
                        extra=report,
                    )
                delete_code = int(jv.JVFiledelete(filename))
                deleted_file = filename
                report.update(
                    {
                        "corrupt_read_code": code,
                        "corrupt_filename": filename,
                        "jvfiledelete": delete_code,
                    }
                )
                # No retry here: stop after one surgical delete.
                return fail(
                    "Corrupt JV file deleted; run the probe once more only",
                    code=code,
                    extra=report,
                )
            return fail("JVRead failed", code=code, extra={**report, "filename": filename})

        report.update(
            {
                "status": "SUCCESS",
                "finished_at_jst": now(),
                "downloaded": last_status,
                "positive_reads_checked": positive_reads,
                "record_type_counts": dict(types),
                "corrupt_file_deleted": deleted_file,
            }
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"saved: {args.output}")
        return 0
    finally:
        if jv is not None:
            try:
                jv.JVClose()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
