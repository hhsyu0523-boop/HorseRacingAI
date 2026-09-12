"""Fetch current-week JV-Link RACE data for one date using option=2.

This is intentionally separate from historical accumulated-data reads. JV-Link's
current race cards live in the current-week stream; option=1 can legitimately
return downloaded files that contain no RA/SE records for today's card.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime

from scripts.database import RaceRepository
from scripts.jvlink_loader import (
    CURRENT_WEEK_FROM_TIME,
    CURRENT_WEEK_OPTION,
    DATA_SPEC_RACE,
    JVLinkClient,
)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYYMMDD")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    target = datetime.strptime(args.date, "%Y%m%d").date()
    client = JVLinkClient()
    races = {}
    entries = {}
    type_counts = defaultdict(int)

    for data in client.fetch(DATA_SPEC_RACE, CURRENT_WEEK_FROM_TIME, option=CURRENT_WEEK_OPTION):
        for record in data.split("\r\n"):
            if not record:
                continue
            raw = client._record_bytes(record)
            if raw is None or len(raw) < 2:
                continue
            rtype = raw[:2].decode("ascii", errors="ignore")
            type_counts[rtype] += 1
            if raw[:2] == b"RA":
                race = client._parse_ra_record(record)
                if race is not None and race.date == target:
                    races[race.race_key] = race
            elif raw[:2] == b"SE":
                entry = client._parse_se_record(record)
                if entry is not None and entry.date == target and 1 <= entry.horse_no <= 18:
                    entries[(entry.race_key, entry.horse_no)] = entry

    repo = RaceRepository()
    saved_races = repo.save_races(list(races.values())) if races else 0
    saved_entries = repo.save_entries(list(entries.values())) if entries else 0

    print(f"CURRENT_DAY_FETCH date={args.date} races={len(races)} entries={len(entries)} saved_races={saved_races} saved_entries={saved_entries}")
    if not races:
        summary = ",".join(f"{k}:{v}" for k, v in sorted(type_counts.items()))
        print(f"NO_RACES record_types={summary}")
        return 2

    for race in sorted(races.values(), key=lambda x: (x.racecourse_code, x.race_no)):
        count = sum(1 for e in entries.values() if e.race_key == race.race_key)
        print(f"[{race.race_key}] {race.racecourse} {race.race_no}R entries={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
