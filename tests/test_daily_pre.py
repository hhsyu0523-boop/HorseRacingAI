import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from scripts.daily_source import normalize
from scripts.daily_operation import Blocked, daily_lock, model_preflight, read_locked, save_pre
from test_daily_source import program_records


def fixture_program():
    return normalize(program_records(),'20260919','program','2026-09-19T08:00:00+09:00')


def fixture_predictor(races):
    return {r['race_key']:[dict(horse_no=e['horse_no'],win_probability=.5/e['horse_no'],
                              place_probability=.8/e['horse_no'],ai_score=90/e['horse_no'])
                           for e in r['entries']] for r in races}


def morning(): return datetime.fromisoformat('2026-09-19T08:01:00+09:00')


class PreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)

    def test_one_row_per_race_and_immutable_duplicate(self):
        first,created=save_pre(self.folder,fixture_program(),fixture_predictor,{'fixture_only':True},morning)
        before=(self.folder/'PRE.json').read_bytes()
        def must_not_predict(_): raise AssertionError('duplicate recomputed PRE')
        second,created_again=save_pre(self.folder,fixture_program(),must_not_predict,{},morning)
        self.assertTrue(created); self.assertFalse(created_again)
        self.assertEqual(first,second); self.assertEqual(before,(self.folder/'PRE.json').read_bytes())
        self.assertEqual(len(first['payload']['rows']),12)
        self.assertIs(first['payload']['result_information_used'],False)

    def test_result_poison_never_reaches_predictor(self):
        p=fixture_program(); p['races'][0]['entries'][0]['finish_position']=1
        def forbidden(_): raise AssertionError('result reached inference')
        with self.assertRaisesRegex(Blocked,'NON_PRE_FIELD'):
            save_pre(self.folder,p,forbidden,{},morning)
        self.assertFalse((self.folder/'PRE.json').exists())

    def test_result_stage_and_deadline_block(self):
        p=fixture_program(); p['races'][0]['source_kind']='6'
        with self.assertRaisesRegex(Blocked,'RESULT_STAGE'):
            save_pre(self.folder,p,fixture_predictor,{},morning)
        p=fixture_program(); p['races'][0]['scheduled_at']='2026-09-19T08:01:00+09:00'
        with self.assertRaisesRegex(Blocked,'DEADLINE'):
            save_pre(self.folder,p,fixture_predictor,{},morning)

    def test_slow_inference_cannot_lock_after_start(self):
        times=iter([morning(),datetime.fromisoformat('2026-09-19T15:01:00+09:00')])
        with self.assertRaises(Blocked):
            save_pre(self.folder,fixture_program(),fixture_predictor,{},lambda:next(times))
        self.assertFalse((self.folder/'PRE.json').exists())

    def test_incomplete_predictions_do_not_publish_partial_file(self):
        with self.assertRaisesRegex(Blocked,'PREDICTION_RACE_SET'):
            save_pre(self.folder,fixture_program(),lambda _: {},{},morning)
        self.assertFalse((self.folder/'PRE.json').exists())

    def test_parallel_publish_has_one_winner(self):
        def work(_): return save_pre(self.folder,fixture_program(),fixture_predictor,{},morning)
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(work,range(4)))
        self.assertEqual(sum(created for _,created in results),1)
        self.assertEqual(len({r['sha256'] for r,_ in results}),1)

    def test_tampered_lock_is_blocked_not_rebuilt(self):
        save_pre(self.folder,fixture_program(),fixture_predictor,{},morning)
        (self.folder/'PRE.json').write_text('{}')
        with self.assertRaisesRegex(Blocked,'LOCK_INTEGRITY'):
            save_pre(self.folder,fixture_program(),fixture_predictor,{},morning)
        self.assertEqual((self.folder/'PRE.json').read_text(),'{}')

    def test_lock_rejects_second_runner(self):
        with daily_lock(self.folder):
            with self.assertRaisesRegex(Blocked,'RUN_ALREADY'):
                with daily_lock(self.folder): pass
        self.assertFalse((self.folder/'RUN.lock').exists())

    def test_missing_four_models_does_not_fallback_to_best_model(self):
        (self.folder/'best_model.pkl').write_bytes(b'not a model')
        with self.assertRaisesRegex(Blocked,'MODEL_COMPATIBILITY_MISSING:winner_model.pkl'):
            model_preflight(self.folder,self.folder/'ensemble.json')


if __name__=='__main__': unittest.main()
