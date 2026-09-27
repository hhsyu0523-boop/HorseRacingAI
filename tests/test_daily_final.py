from copy import deepcopy
from contextlib import closing
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.daily_source import normalize
from scripts.daily_operation import Blocked, read_locked, reconcile, save_pre
from scripts.daily_features import HISTORY_COLUMNS, build_current_features
from test_daily_source import program_records
from test_daily_pre import fixture_program, fixture_predictor, morning


def afternoon(): return datetime.fromisoformat('2026-09-19T16:01:00+09:00')


class FinalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.pre,_=save_pre(self.folder,fixture_program(),fixture_predictor,{'fixture_only':True},morning)
        self.before=(self.folder/'PRE.json').read_bytes()
        self.results=normalize(program_records(True),'20260919','results','2026-09-19T16:00:00+09:00')

    def test_phase_a_fixture_end_to_end_and_duplicate(self):
        first=reconcile(self.folder,self.results,afternoon())
        second=reconcile(self.folder,self.results,afternoon())
        self.assertEqual(first,second)
        self.assertEqual(first['payload']['status'],'SUCCESS')
        for m in first['payload']['metrics'].values():
            self.assertEqual(m,dict(hits=12,n=12,rate=1.0))
        self.assertEqual(len(list((self.folder/'final').glob('*.json'))),1)
        self.assertEqual(self.before,(self.folder/'PRE.json').read_bytes())

    def test_pending_results_are_warning_not_perfect_zero_race_score(self):
        self.results['races']=[]
        result=reconcile(self.folder,self.results,afternoon())['payload']
        self.assertEqual(result['status'],'WARNING')
        self.assertEqual(len(result['pending']),12)
        self.assertIsNone(result['metrics']['top1']['rate'])

    def test_partial_results_do_not_regress_and_pre_never_changes(self):
        first=deepcopy(self.results); first['races']=first['races'][:2]
        reconcile(self.folder,first,afternoon())
        first['races']=first['races'][:1]
        with self.assertRaisesRegex(Blocked,'REGRESSED'):
            reconcile(self.folder,first,afternoon())
        self.assertEqual(self.before,(self.folder/'PRE.json').read_bytes())

    def test_future_early_or_unconfirmed_results_rejected(self):
        for value in ('2026-09-19T14:00:00+09:00','2026-09-20T16:00:00+09:00'):
            r=deepcopy(self.results); r['races'][0]['fetched_at']=value
            with self.assertRaises(Blocked): reconcile(self.folder,r,afternoon())
        self.results['races'][0]['result_confirmed']=False
        with self.assertRaises(Blocked): reconcile(self.folder,self.results,afternoon())

    def test_tied_unknown_or_duplicate_result_rejected(self):
        for actual in ([1,1,2],[1,2,18]):
            r=deepcopy(self.results); r['races'][0]['actual']=actual
            with self.assertRaises(Blocked): reconcile(self.folder,r,afternoon())
        self.results['races'].append(self.results['races'][0])
        with self.assertRaises(Blocked): reconcile(self.folder,self.results,afternoon())

    def test_official_correction_creates_new_final_without_overwriting_old(self):
        initial=reconcile(self.folder,self.results,afternoon())
        self.results['races'][0]['actual']=[3,2,1]
        final=reconcile(self.folder,self.results,afternoon())
        self.assertNotEqual(initial['sha256'],final['sha256'])
        self.assertEqual(len(list((self.folder/'final').glob('*.json'))),2)
        self.assertEqual(final['payload']['metrics']['top1']['hits'],11)
        self.assertEqual(self.before,(self.folder/'PRE.json').read_bytes())

    def test_actual_current_day_db_results_cannot_change_features(self):
        db=self.folder/'history.db'
        rows=[('202609120601','2026-09-12','06',1600,'芝','右','なし','良','1勝',i,
               'HORSE'+str(i),'JOCKEY',i,i,34.5,'1:35.0') for i in range(1,4)]
        with closing(sqlite3.connect(db)) as c, c:
            c.execute('CREATE TABLE race_history ('+','.join(HISTORY_COLUMNS)+')')
            c.executemany('INSERT INTO race_history VALUES ('+','.join('?' for _ in HISTORY_COLUMNS)+')',rows)
        program=fixture_program()['races']
        for race in program: race['track_condition']='良'
        before=build_current_features(program,db)
        with closing(sqlite3.connect(db)) as c, c:
            injected=[tuple('2026-09-19' if i==1 else '202609190601' if i==0 else 99 if i==13 else v
                            for i,v in enumerate(row)) for row in rows]
            c.executemany('INSERT INTO race_history VALUES ('+','.join('?' for _ in HISTORY_COLUMNS)+')',injected)
        disk_before=db.read_bytes()
        after=build_current_features(program,db)
        self.assertEqual(before,after)
        self.assertEqual(disk_before,db.read_bytes())
        self.assertTrue(all(row[-1]==0 for row in after))


if __name__=='__main__': unittest.main()
