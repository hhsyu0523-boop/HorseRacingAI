"""Historical backfill using JV-Link setup date ranges with -402/-403 recovery."""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.jvlink_history_autorun as base


class RangeJVLinkClient(base.jl.JVLinkClient):
    def __init__(self, range_from: str, range_to: str, **kwargs):
        super().__init__(**kwargs)
        self.range_from = range_from
        self.range_to = range_to

    def open_range(self, dataspec: str, option: int = 4):
        self.connect()
        assert self._adapter is not None
        date_range = f"{self.range_from}-{self.range_to}"
        code, read_count, download_count, timestamp = self._adapter.open(dataspec, date_range, option)
        if code != 0:
            raise self._error("JVOpen", code)
        self._opened = True
        return base.jl.OpenResult(read_count, download_count, timestamp)

    def _delete_corrupt_file(self, filename: str) -> bool:
        name = Path(filename).name if filename else ""
        if not name:
            return False
        # Official recovery path is JVFiledelete when available.
        adapter = self._adapter
        com = getattr(adapter, "_com", None)
        if com is not None and hasattr(com, "JVFiledelete"):
            try:
                result = int(com.JVFiledelete(name))
                if result == 0:
                    return True
            except Exception:
                pass
        # Fallback for environments where JVFiledelete is inaccessible via late binding.
        for root in (
            Path(r"C:\ProgramData\JRA-VAN\Data Lab\data"),
            Path(r"C:\Program Files (x86)\JRA-VAN\Data Lab\data"),
        ):
            target = root / name
            if target.exists():
                target.unlink()
                return True
        return False

    def iter_records(self):
        """Read setup data and recover corrupt JVD files reported as -402/-403."""
        if not self._opened or self._adapter is None:
            raise base.jl.JVLinkError("JVRead", -111, "open required")
        prepare_retries = 0
        corrupt_recoveries = 0
        while True:
            code, data, filename = self._adapter.read(self.buffer_size)
            if code > 0:
                prepare_retries = 0
                yield data[:code]
                continue
            if code == -1:
                continue
            if code == 0:
                return
            if code == -3:
                prepare_retries += 1
                if prepare_retries > self.max_prepare_retries:
                    raise base.jl.JVLinkError("JVRead", code, "data preparation timed out")
                time.sleep(self.retry_interval)
                continue
            if code in (-402, -403):
                corrupt_recoveries += 1
                if corrupt_recoveries > 50:
                    raise base.jl.JVLinkError("JVRead", code, "too many corrupt-file recoveries")
                if not self._delete_corrupt_file(filename):
                    raise base.jl.JVLinkError("JVRead", code, f"corrupt file could not be deleted: {filename}")
                self.close()
                time.sleep(0.5)
                self.open_range(base.jl.DATA_SPEC_RACE, option=4)
                continue
            raise base.jl.JVLinkError("JVRead", code, f"filename={filename}")

    def fetch(self, dataspec: str, from_time: str, option: int = 4) -> Iterator[str]:
        try:
            self.open_range(dataspec, option=4)
            yield from self.iter_records()
        finally:
            self.close()


def setup_range(from_date: date, to_date: date) -> tuple[str, str]:
    range_from = f"{from_date.year:04d}{from_date.month:02d}01000000"
    if to_date.month == 12 and to_date.day == 31:
        range_to = f"{to_date.year:04d}9999999999"
    else:
        range_to = f"{to_date.year:04d}{to_date.month:02d}99999999"
    return range_from, range_to


def fetch_save(from_date: date, to_date: date, db_path: Path) -> dict:
    started = base.now()
    range_from, range_to = setup_range(from_date, to_date)
    client = RangeJVLinkClient(range_from, range_to, sid="UNKNOWN", max_prepare_retries=9000)
    result = client.get_race_history(from_date, to_date)
    repo = base.RaceRepository(database_path=db_path)
    before = base.stats(db_path)
    saved = repo.save_history(result.entries, from_date, to_date)
    after = base.stats(db_path)
    return {
        "from": from_date.isoformat(), "to": to_date.isoformat(),
        "jvopen_range": f"{range_from}-{range_to}",
        "started_at_jst": started, "finished_at_jst": base.now(),
        "fetched_entries": len(result.entries), "saved_entries": saved,
        "parse_errors": result.error_count, "before": before, "after": after,
    }

base.fetch_save = fetch_save

if __name__ == "__main__":
    raise SystemExit(base.main())
