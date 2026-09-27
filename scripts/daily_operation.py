"""Issue #2 Phase A only: acquisition -> immutable PRE -> separate FINAL.

Existing model selection/weights are unchanged. No betting, training, main pushes
or fallback models. Live Phase A verification is required before Phase B work.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from scripts.daily_source import JST, now

ROOT = Path(__file__).resolve().parents[1]
MODEL_NAMES = ('winner_model.pkl','place_model.pkl','winner_xgb.pkl','place_xgb.pkl')


class Blocked(RuntimeError):
    pass


def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    t=datetime.fromisoformat(value)
    if t.tzinfo is None:
        raise Blocked('TIMEZONE_REQUIRED')
    return t


def validate_day(day):
    if not re.fullmatch(r'\d{8}',day):
        raise Blocked('INVALID_DATE')
    return datetime.strptime(day,'%Y%m%d').date()


def atomic_json(path, data, immutable=False):
    """Flush then publish atomically. Hard-link creation cannot replace a lock."""
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(canonical(data)+b'\n'); f.flush(); os.fsync(f.fileno())
        if immutable:
            os.link(temp,path)
        else:
            os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def envelope(body):
    return dict(payload=body,sha256=sha(canonical(body)))


def read_locked(path, kind, day):
    try:
        e=json.loads(path.read_text(encoding='utf-8'))
        b=e['payload']
        if e['sha256']!=sha(canonical(b)) or b['kind']!=kind or b['date']!=day:
            raise Blocked('LOCK_INTEGRITY_FAILURE')
        return e
    except (ValueError,KeyError,TypeError) as exc:
        raise Blocked('LOCK_INTEGRITY_FAILURE') from exc


@contextmanager
def daily_lock(folder):
    folder.mkdir(parents=True,exist_ok=True)
    lock=folder/'RUN.lock'
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError as exc:
        raise Blocked('RUN_ALREADY_ACTIVE_OR_STALE_LOCK') from exc
    try:
        with os.fdopen(fd,'w') as f: f.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def model_preflight(models, config):
    missing=[n for n in MODEL_NAMES if not (models/n).is_file()]
    if missing:
        raise Blocked('MODEL_COMPATIBILITY_MISSING:'+','.join(missing))
    from scripts.ensemble_predict import load_ensemble_config
    load_ensemble_config(config)
    return {**{n:sha((models/n).read_bytes()) for n in MODEL_NAMES},
            'ensemble.json':sha(config.read_bytes())}


def validate_program(program, day, at):
    if program.get('date')!=day or program.get('mode')!='program' or program.get('status')!='SUCCESS' or program.get('stream_complete') is not True:
        raise Blocked('ACQUISITION_INCOMPLETE')
    if program.get('source')!='JV-Link:0B15':
        raise Blocked('UNEXPECTED_PROGRAM_SOURCE')
    acquired=timestamp(program['acquired_at'])
    if acquired>at or at-acquired>timedelta(minutes=10):
        raise Blocked('SOURCE_STALE_OR_FUTURE')
    if acquired.astimezone(JST).strftime('%Y%m%d')!=day:
        raise Blocked('SOURCE_DATE_MISMATCH')
    races=program['races']
    if not races or len({r['race_key'] for r in races})!=len(races):
        raise Blocked('EMPTY_OR_DUPLICATE_PROGRAM')
    safe=[]
    allowed_race={'date','race_key','racecourse_code','racecourse','meeting_no','day_no','race_no',
                  'race_name','distance','surface','direction','track_layout','condition','race_class',
                  'start_time','registered','source_kind','scheduled_at','entries','track_condition'}
    allowed_entry={'race_key','racecourse_code','racecourse','meeting_no','day_no','race_no',
                   'gate_no','horse_no','horse_name','jockey_name','trainer_name','sex_age',
                   'assigned_weight','source_kind','horse_id'}
    for race in races:
        if set(race)-allowed_race or any(set(e)-allowed_entry for e in race['entries']):
            raise Blocked('NON_PRE_FIELD_REJECTED')
        rid=race['race_key']
        start=timestamp(race['scheduled_at'])
        if not re.fullmatch(day+r'\d{4}',rid) or start.astimezone(JST).strftime('%Y%m%d')!=day:
            raise Blocked('RACE_DATE_MISMATCH')
        if at>=start or acquired>=start:
            raise Blocked('PRE_DEADLINE_PASSED:'+rid)
        if race['source_kind'] not in ('2','3','4') or any(e['source_kind'] not in ('2','3','4') for e in race['entries']):
            raise Blocked('RESULT_STAGE_SOURCE_REJECTED:'+rid)
        numbers=[e['horse_no'] for e in race['entries']]
        if len(numbers)!=race['registered'] or len(set(numbers))!=len(numbers) or len(numbers)<3:
            raise Blocked('INCOMPLETE_RUNNERS:'+rid)
        if any(e['race_key']!=rid for e in race['entries']):
            raise Blocked('ENTRY_RACE_MISMATCH')
        safe.append(deepcopy(race))
    return safe


def save_pre(folder, program, predictor, model_hashes, clock=now):
    day=program['date']; validate_day(day)
    dest=folder/'PRE.json'
    if dest.exists():
        return read_locked(dest,'PRE',day),False
    races=validate_program(program,day,clock())
    predictions=predictor(deepcopy(races))
    if set(predictions)!=set(r['race_key'] for r in races):
        raise Blocked('PREDICTION_RACE_SET_MISMATCH')
    generated=clock()
    # Recheck after all model calls: long inference must not cross a deadline.
    validate_program(program,day,generated)
    rows=[]
    for race in races:
        ranking=predictions[race['race_key']]
        if {p['horse_no'] for p in ranking}!={e['horse_no'] for e in race['entries']} or len(ranking)!=race['registered']:
            raise Blocked('PREDICTION_RUNNER_SET_MISMATCH')
        for p in ranking:
            if set(p)!={'horse_no','win_probability','place_probability','ai_score'}:
                raise Blocked('INVALID_PREDICTION_SCHEMA')
            if any(not math.isfinite(float(p[k])) for k in ('win_probability','place_probability','ai_score')):
                raise Blocked('NONFINITE_PREDICTION')
            if any(not 0<=p[k]<=1 for k in ('win_probability','place_probability')):
                raise Blocked('INVALID_PROBABILITY')
        ranking=sorted(ranking,key=lambda p:(-p['ai_score'],p['horse_no']))
        rows.append(dict(race_key=race['race_key'],scheduled_at=race['scheduled_at'],
                         generated_at=generated.isoformat(),result_information_used=False,
                         top3=[p['horse_no'] for p in ranking[:3]],ranking=ranking))
    locked=envelope(dict(schema_version=1,kind='PRE',date=day,generated_at=generated.isoformat(),
                         result_information_used=False,source_sha256=program['source_sha256'],
                         source_acquired_at=program['acquired_at'],models=model_hashes,rows=rows))
    try:
        atomic_json(dest,locked,immutable=True)
        return locked,True
    except FileExistsError:
        return read_locked(dest,'PRE',day),False


def reconcile(folder, source, at=None):
    at=at or now()
    day=source['date']; validate_day(day)
    pre=read_locked(folder/'PRE.json','PRE',day)
    if source.get('mode')!='results' or source.get('status')!='SUCCESS' or source.get('source')!='JV-Link:0B12' or source.get('stream_complete') is not True:
        raise Blocked('RESULT_SOURCE_INCOMPLETE')
    known={r['race_key']:r for r in pre['payload']['rows']}
    results={}
    for row in source['races']:
        rid=row['race_key']
        if rid not in known or rid in results:
            raise Blocked('UNKNOWN_OR_DUPLICATE_RESULT')
        p=known[rid]
        fetched=timestamp(row['fetched_at'])
        start=timestamp(p['scheduled_at'])
        if row.get('result_confirmed') is not True or not start<fetched<=at or timestamp(row['scheduled_at'])!=start:
            raise Blocked('RESULT_TIME_OR_CONFIRMATION_INVALID')
        actual=row['actual']
        if len(actual)!=3 or len(set(actual))!=3 or not set(actual).issubset({v['horse_no'] for v in p['ranking']}):
            raise Blocked('INVALID_OR_TIED_RESULT')
        top=p['top3']
        results[rid]=dict(race_key=rid,actual=actual,predicted=top,
                          top1=top[0]==actual[0],winner_in_top3=actual[0] in top,
                          exact12=top[:2]==actual[:2],exact123=top==actual)
    # Do not regress a partially settled day when a later feed temporarily omits results.
    latest=folder/'FINAL_LATEST.json'
    if latest.exists():
        previous=read_locked(latest,'FINAL',day)['payload']
        if previous['pre_sha256']!=pre['sha256']:
            raise Blocked('FINAL_PRE_HASH_MISMATCH')
        prior={r['race_key']:r for r in previous['rows']}
        if not set(prior).issubset(results):
            raise Blocked('RESULT_FEED_REGRESSED')
    rows=[results[rid] for rid in sorted(results)]
    n=len(rows)
    metrics={k:dict(hits=sum(r[k] for r in rows),n=n,rate=sum(r[k] for r in rows)/n if n else None)
             for k in ('top1','winner_in_top3','exact12','exact123')}
    body=dict(schema_version=1,kind='FINAL',date=day,pre_sha256=pre['sha256'],
              status='SUCCESS' if n==len(known) else 'WARNING',reason='COMPLETE' if n==len(known) else 'WAITING_FOR_RESULTS',
              pending=sorted(set(known)-set(results)),metrics=metrics,rows=rows)
    final=envelope(body)
    dest=folder/'final'/f"{final['sha256']}.json"
    try: atomic_json(dest,final,immutable=True)
    except FileExistsError: read_locked(dest,'FINAL',day)
    if read_locked(folder/'PRE.json','PRE',day)['sha256']!=pre['sha256']:
        raise Blocked('PRE_CHANGED_DURING_RECONCILIATION')
    atomic_json(latest,final)
    return final


def source_worker(python, day, mode, folder):
    dest=folder/'source'/f'{mode}-{uuid.uuid4().hex}.json'
    dest.parent.mkdir(parents=True,exist_ok=True)
    try:
        p=subprocess.run([str(python),'-m','scripts.daily_source','--date',day,'--mode',mode,'--output',str(dest)],
                         cwd=ROOT,capture_output=True,timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise Blocked('JVLINK_PROCESS_TIMEOUT') from exc
    if not dest.exists(): raise Blocked('JVLINK_PROCESS_FAILED:'+str(p.returncode))
    data=json.loads(dest.read_text(encoding='utf-8'))
    if p.returncode or data.get('status')!='SUCCESS':
        raise Blocked(data.get('reason','JVLINK_PROCESS_FAILED'))
    return data


def run(args):
    validate_day(args.date)
    if args.date!=now().strftime('%Y%m%d'):
        raise Blocked('LIVE_RUN_DATE_MUST_BE_TODAY')
    folder=args.output.resolve()/args.date
    report=dict(date=args.date,generated_at=now().isoformat(),status='BLOCKED',
                phase_a_verified_live=False,phase_b='NOT_STARTED_PHASE_A_GATE',stages={})
    try:
        with daily_lock(folder):
            # Record compatibility independently even when the PRE deadline has passed.
            try:
                hashes=model_preflight(args.models,args.config)
                report['model_compatibility']=dict(status='FILES_PRESENT_SCHEMA_NOT_YET_VERIFIED',hashes=hashes)
            except Exception as exc:
                hashes=None
                report['model_compatibility']=dict(status='BLOCKED',reason=str(exc))
            program=source_worker(args.jv_python,args.date,'program',folder)
            report['stages']['1']=dict(status='SUCCESS',source_sha256=program['source_sha256'],
                                      races=len(program['races']),entries=sum(len(r['entries']) for r in program['races']),
                                      acquired_at=program['acquired_at'],connection=program['connection'])
            if (folder/'PRE.json').exists():
                pre=read_locked(folder/'PRE.json','PRE',args.date)
            else:
                validate_program(program,args.date,now())
                if hashes is None: raise Blocked(report['model_compatibility']['reason'])
                from scripts.daily_features import ExistingEnsemble
                predictor=ExistingEnsemble(args.history_db,args.models,args.config,folder/'inference')
                pre,_=save_pre(folder,program,predictor,hashes)
            report['stages']['2']=dict(status='SUCCESS',pre_sha256=pre['sha256'],races=len(pre['payload']['rows']))
            if all(timestamp(r['scheduled_at'])>=now() for r in pre['payload']['rows']):
                report['stages']['3']=dict(status='WARNING',reason='WAITING_FOR_FIRST_RACE')
                report['status']='WARNING'
            else:
                source=source_worker(args.jv_python,args.date,'results',folder)
                final=reconcile(folder,source)
                report['stages']['3']=dict(status=final['payload']['status'],final_sha256=final['sha256'],metrics=final['payload']['metrics'])
                report['status']=final['payload']['status']
                report['phase_a_verified_live']=report['status']=='SUCCESS'
    except Exception as exc:
        report['status']='BLOCKED' if isinstance(exc,(Blocked,FileNotFoundError)) else 'FAILED'
        report['reason']=str(exc)
    for step in ('1','2','3'):
        report['stages'].setdefault(step,dict(status='BLOCKED',reason=report.get('reason','UPSTREAM_NOT_READY')))
    atomic_json(folder/'runs'/f'{uuid.uuid4().hex}.json',report,immutable=True)
    atomic_json(folder/'STATUS.json',report)
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0 if report['status'] in ('SUCCESS','WARNING') else 2


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--date',default=now().strftime('%Y%m%d'))
    p.add_argument('--jv-python',type=Path,required=True)
    p.add_argument('--models',type=Path,required=True)
    p.add_argument('--history-db',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'config/ensemble.json')
    p.add_argument('--output',type=Path,default=ROOT/'.daily_runtime')
    raise SystemExit(run(p.parse_args()))
