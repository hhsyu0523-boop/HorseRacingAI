"""Isolated JV-Link adapter for Issue #2; never updates the research database.

JV-Data 4.9.0.1: RA registered count at byte 882, SE confirmed rank at 335.
JVRead -1 is a file boundary, NOT end of stream. Only 0 proves completion.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from scripts.jvlink_loader import JVLinkClient, Win32ComJVLinkAdapter

JST = timezone(timedelta(hours=9))


class SourceBlocked(RuntimeError):
    pass


def now():
    return datetime.now(JST)


def stream(adapter, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        code, data, filename = adapter.read(1048576)
        if code == 0:
            return
        if code == -1:
            continue
        if code == -3:
            time.sleep(.2)
            continue
        if code < 0:
            raise SourceBlocked(f'JVREAD_ERROR:{code}')
        raw = JVLinkClient._record_bytes(data)
        if raw is None or len(raw) < code:
            raise SourceBlocked('JVREAD_TRUNCATED_RECORD')
        yield raw[:code], filename
    raise SourceBlocked('JVREAD_TIMEOUT')


def normalize(records, day, mode, acquired_at):
    """PRE output is a whitelist, never a copy of the result-capable SE record."""
    races, entries, payouts, result_rows = {}, {}, {}, {}
    digest = hashlib.sha256()
    raw_count = 0
    field = JVLinkClient._field
    for raw, filename in records:
        digest.update(raw)
        raw_count += 1
        typ = raw[:2]
        if typ not in (b'RA', b'SE', b'HR') or field(raw, 12, 8) != day:
            continue
        if len(raw) < {b'RA':1272, b'SE':555, b'HR':719}[typ]:
            raise SourceBlocked('TRUNCATED_'+typ.decode())
        text = raw.decode('cp932')
        if typ == b'RA':
            race = JVLinkClient._parse_ra_record(text)
            if race is None or not 1 <= race.race_no <= 12 or race.racecourse_code not in {f'{i:02}' for i in range(1,11)}:
                raise SourceBlocked('INVALID_RA')
            row = asdict(race)
            row['date'] = race.date.isoformat()
            row['registered'] = int(field(raw, 882, 2))
            row['source_kind'] = field(raw, 3, 1)
            going = field(raw, 889 if race.surface == '芝' else 890, 1)
            row['track_condition'] = {'1':'良','2':'稍重','3':'重','4':'不良'}.get(going)
            row['scheduled_at'] = datetime.fromisoformat(row['date']+'T'+race.start_time+':00+09:00').isoformat()
            if race.race_key in races and races[race.race_key] != row:
                raise SourceBlocked('CONFLICTING_RA:'+race.race_key)
            races[race.race_key] = row
        elif typ == b'SE':
            entry = JVLinkClient._parse_se_record(text)
            if entry is None or not 1 <= entry.horse_no <= 18:
                raise SourceBlocked('INVALID_SE')
            key = (entry.race_key, entry.horse_no)
            # Deliberately omit odds/popularity as the SE fields can be final market data.
            row = {k:v for k,v in asdict(entry).items() if k not in ('date','odds','popularity')}
            row['source_kind'] = field(raw,3,1)
            row['horse_id'] = field(raw,31,10)
            if key in entries and entries[key] != row:
                raise SourceBlocked('CONFLICTING_SE:'+entry.race_key)
            entries[key] = row
            if mode == 'results' and field(raw,3,1) in ('6','7'):
                rank = int(field(raw,335,2) or 0)
                result_rows[key] = dict(horse_no=entry.horse_no, rank=rank)
        elif mode == 'results':
            target = datetime.strptime(day,'%Y%m%d').date()
            payouts.update(JVLinkClient._parse_hr_payouts(raw,target,target))
    if not races:
        raise SourceBlocked('NO_RACES_FROM_SOURCE')
    output = []
    for rid, race in sorted(races.items()):
        runners = [row for (key,no),row in sorted(entries.items()) if key == rid]
        if not 3 <= race['registered'] <= 18 or len(runners) != race['registered']:
            raise SourceBlocked('INCOMPLETE_ENTRIES:'+rid)
        if not all(r['horse_name'] and r['horse_id'] and r['jockey_name'] for r in runners):
            raise SourceBlocked('MISSING_RUNNER_IDENTITY:'+rid)
        if mode == 'program':
            output.append(dict(**race, entries=runners))
        else:
            if datetime.fromisoformat(race['scheduled_at']) >= datetime.fromisoformat(acquired_at):
                continue
            ordered = sorted([r for (key,no),r in result_rows.items() if key == rid],key=lambda r:(r['rank'],r['horse_no']))
            top = [r for r in ordered if 1 <= r['rank'] <= 3]
            # Ties/partial results are explicit unresolved states; never silently mis-score.
            if len(ordered) != race['registered'] or [r['rank'] for r in top] != [1,2,3]:
                continue
            output.append(dict(race_key=rid,scheduled_at=race['scheduled_at'],
                               actual=[r['horse_no'] for r in top],
                               result_source='JV-Link:0B12',result_confirmed=True,
                               fetched_at=acquired_at))
    if mode == 'program':
        for venue in {r['racecourse_code'] for r in output}:
            if {r['race_no'] for r in output if r['racecourse_code']==venue} != set(range(1,13)):
                raise SourceBlocked('INCOMPLETE_VENUE_PROGRAM:'+venue)
    return dict(schema_version=1,date=day,status='SUCCESS',mode=mode,
                source='JV-Link:'+('0B15' if mode=='program' else '0B12'),
                acquired_at=acquired_at,stream_complete=True,raw_record_count=raw_count,
                source_sha256=digest.hexdigest(),races=output)


def acquire(day, mode, adapter_factory=Win32ComJVLinkAdapter):
    adapter = adapter_factory()
    try:
        code = adapter.init(os.environ.get('JVLINK_SID','UNKNOWN'))
        if code != 0:
            raise SourceBlocked(f'JVINIT_ERROR:{code}')
        spec = '0B15' if mode == 'program' else '0B12'
        code = int(adapter._com.JVRTOpen(spec, day))
        if code != 0:
            raise SourceBlocked(f'JVRTOPEN_ERROR:{code}')
        records = list(stream(adapter))
        result = normalize(records, day, mode, now().isoformat())
        result['connection'] = dict(jvinit_code=0,jvrtopen_code=0,jvread_end_code=0)
        return result
    finally:
        adapter.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date',required=True)
    p.add_argument('--mode',choices=['program','results'],required=True)
    p.add_argument('--output',type=Path,required=True)
    args = p.parse_args()
    datetime.strptime(args.date,'%Y%m%d')
    try:
        report = acquire(args.date,args.mode)
        code = 0
    except Exception as exc:
        report = dict(status='BLOCKED',date=args.date,mode=args.mode,
                      acquired_at=now().isoformat(),reason=str(exc),stream_complete=False)
        code = 2
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k != 'races'},ensure_ascii=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
