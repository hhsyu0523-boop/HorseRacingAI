"""One-command historical backfill using JV-Link setup date ranges.

This fixes the previous zero-row validation by using the documented JVOpen
FromTime-ToTime form ("FROM-TO") for option=4 setup data.  Old setup data is
monthly aggregated, so the read window deliberately covers the containing
month/year and get_race_history filters records back to the requested dates.

Flow remains fail-safe: validate 2021-07-18 against a disposable DB copy first;
only if rows are returned does the imported autorun create a production backup
and process 2021-08-16..2025-07-25 in bounded chunks.
"""
from __future__ import annotations

import sys
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
        code, read_count, download_count, timestamp = self._adapter.open(
            dataspec, date_range, option
        )
        if code != 0:
            raise self._error("JVOpen", code)
        self._opened = True
        return base.jl.OpenResult(read_count, download_count, timestamp)

    def fetch(self, dataspec: str, from_time: str, option: int = 4) -> Iterator[str]:
        # get_race_history supplies a single from_time; setup history needs the
        # bounded FROM-TO range prepared for this chunk instead.
        try:
            self.open_range(dataspec, option=4)
            yield from self.iter_records()
        finally:
            self.close()


def setup_range(from_date: date, to_date: date) -> tuple[str, str]:
    # Start at the first day of the containing month so monthly setup archives
    # that contain the requested first day are not skipped.
    range_from = f"{from_date.year:04d}{from_date.month:02d}01000000"
    if to_date.month == 12 and to_date.day == 31:
        # Setup archives can use pseudo-times through 99:99:99; using all 9s
        # also includes the December monthly aggregate.
        range_to = f"{to_date.year:04d}9999999999"
    else:
        # Include the full containing month; filtering happens after parsing.
        range_to = f"{to_date.year:04d}{to_date.month:02d}99999999"
    return range_from, range_to


def fetch_save(from_date: date, to_date: date, db_path: Path) -> dict:
    started = base.now()
    range_from, range_to = setup_range(from_date, to_date)
    client = RangeJVLinkClient(
        range_from,
        range_to,
        sid="UNKNOWN",
        max_prepare_retries=9000,
    )
    result = client.get_race_history(from_date, to_date)
    repo = base.RaceRepository(database_path=db_path)
    before = base.stats(db_path)
    saved = repo.save_history(result.entries, from_date, to_date)
    after = base.stats(db_path)
    return {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "jvopen_range": f"{range_from}-{range_to}",
        "started_at_jst": started,
        "finished_at_jst": base.now(),
        "fetched_entries": len(result.entries),
        "saved_entries": saved,
        "parse_errors": result.error_count,
        "before": before,
        "after": after,
    }


# Replace only the fetch/save operation.  Reuse the already-tested safety flow,
# backup creation, chunking, reporting, and DB safeguards from autorun.
base.fetch_save = fetch_save

if __name__ == "__main__":
    raise SystemExit(base.main())
