"""Pre-race inputs for the existing four-model ensemble, without result DB writes.

Use existing feature semantics and ensemble weights. A model/schema/data mismatch
is BLOCKED; never substitute best_model, retrain, or use today's feature_history.
"""
from collections import defaultdict
from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3
import tempfile

from scripts.daily_operation import Blocked, MODEL_NAMES, model_preflight
from scripts.feature_engine import FeatureEngineeringEngine, _Run, _Performance, _feature_row, _normalize_class

HISTORY_COLUMNS = ('race_key','race_date','racecourse_code','distance','surface','direction',
                   'track_layout','track_condition','race_class','horse_no','horse_name',
                   'jockey_name','popularity','finish_position','last_3f','race_time')


def history_before(path, day):
    if not path.is_file(): raise Blocked('HISTORY_DATABASE_MISSING')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        # This is the only read from the operational DB. No current-day rows or
        # precomputed feature_history (which may contain labels) cross this boundary.
        rows=source.execute('SELECT '+','.join(HISTORY_COLUMNS)+
                            ' FROM race_history WHERE race_date < ? ORDER BY race_date,race_key,horse_no',(day,)).fetchall()
    with closing(sqlite3.connect(':memory:')) as scratch:
        scratch.row_factory=sqlite3.Row
        scratch.execute('CREATE TABLE race_history ('+','.join(HISTORY_COLUMNS)+')')
        scratch.executemany('INSERT INTO race_history VALUES ('+','.join('?' for _ in HISTORY_COLUMNS)+')',rows)
        return FeatureEngineeringEngine._load_runs(scratch)


def build_current_features(races, history_db):
    days={r['date'] for r in races}
    if len(days)!=1: raise Blocked('MIXED_FEATURE_DATES')
    day=next(iter(days)); target=date.fromisoformat(day)
    histories=defaultdict(list); jockeys=defaultdict(_Performance)
    for run in history_before(history_db,day):
        if run.race_date>=target: raise Blocked('HISTORY_CUTOFF_VIOLATION')
        histories[run.horse_name].append(run)
        jockeys[run.jockey_name].add(run.finish_position)
    rows=[]
    for race in races:
        if race.get('track_condition') not in ('良','稍重','重','不良'):
            raise Blocked('GOING_UNAVAILABLE:'+race['race_key'])
        for entry in race['entries']:
            previous=histories[entry['horse_name']][-5:]
            if not previous or not jockeys[entry['jockey_name']].starts:
                raise Blocked('HISTORY_INCOMPLETE:'+race['race_key'])
            current=_Run(race_key=race['race_key'],race_date=target,racecourse_code=race['racecourse_code'],
                         distance=race['distance'],surface=race['surface'],direction=race['direction'],
                         track_layout=race['track_layout'],track_condition=race['track_condition'],
                         race_class=_normalize_class(race['race_class']),horse_no=entry['horse_no'],
                         horse_name=entry['horse_name'],jockey_name=entry['jockey_name'],popularity=None,
                         finish_position=0,last_3f=None,race_seconds=None)
            rows.append(_feature_row(current,previous,jockeys[entry['jockey_name']]))
    return rows


class ExistingEnsemble:
    def __init__(self, history_db, models, config, scratch):
        self.history_db=Path(history_db); self.models=Path(models)
        self.config=Path(config); self.scratch=Path(scratch)

    def __call__(self,races):
        from scripts.train_model import load_model_bundle, EXCLUDED_COLUMNS
        from scripts.train_xgboost import load_xgb_bundle
        from scripts.ensemble_predict import EnsemblePredictionEngine
        hashes=model_preflight(self.models,self.config)
        bundles=[load_model_bundle(self.models/n) if i<2 else load_xgb_bundle(self.models/n)
                 for i,n in enumerate(MODEL_NAMES)]
        rows=build_current_features(races,self.history_db)
        self.scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.scratch) as temp:
            db=Path(temp)/'pre_only.sqlite3'
            with closing(sqlite3.connect(db)) as connection, connection:
                FeatureEngineeringEngine._initialize_table(connection)
                columns=[r[1] for r in connection.execute('PRAGMA table_info(feature_history)')]
                allowed=set(columns)-EXCLUDED_COLUMNS
                for bundle in bundles:
                    features=bundle['feature_names']
                    if not features or not set(features).issubset(allowed):
                        raise Blocked('MODEL_FEATURE_SCHEMA_MISMATCH')
                    indexes=[columns.index(c) for c in features]
                    if any(row[i] is None for row in rows for i in indexes):
                        raise Blocked('REQUIRED_FEATURE_MISSING')
                connection.executemany(FeatureEngineeringEngine._insert_statement(),rows)
            # Same prediction engine used by PredictionEngine, without its separate
            # bet-generation/reporting side effects (Phase B remains gated).
            engine=EnsemblePredictionEngine(db,self.models,self.config)
            output={}
            for race in races:
                result=engine.predict(race['race_key'])
                output[race['race_key']]=[dict(horse_no=p.horse_no,win_probability=p.win_probability,
                                               place_probability=p.place_probability,ai_score=p.ai_score)
                                         for p in result.predictions]
        if model_preflight(self.models,self.config)!=hashes:
            raise Blocked('MODEL_CHANGED_DURING_INFERENCE')
        return output
