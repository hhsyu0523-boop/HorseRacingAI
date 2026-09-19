from argparse import Namespace
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.daily_operation import run
from test_daily_pre import fixture_program, morning


class RunnerTests(unittest.TestCase):
    def test_missing_approved_models_keeps_phase_b_closed_on_repeated_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            args=Namespace(date='20260919',output=folder,models=folder/'missing-models',
                           config=folder/'ensemble.json',history_db=folder/'history.db',jv_python=Path('fake'))
            program=fixture_program(); program['connection']={'jvinit_code':0,'jvrtopen_code':0,'jvread_end_code':0}
            with patch('scripts.daily_operation.now',side_effect=morning), \
                 patch('scripts.daily_operation.source_worker',return_value=program), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(args),2)
                self.assertEqual(run(args),2)
            status=json.loads((folder/'20260919/STATUS.json').read_text())
            self.assertEqual(status['stages']['1']['status'],'SUCCESS')
            self.assertEqual(status['stages']['2']['status'],'BLOCKED')
            self.assertEqual(status['phase_b'],'NOT_STARTED_PHASE_A_GATE')
            self.assertFalse(status['phase_a_verified_live'])
            self.assertFalse((folder/'20260919/PRE.json').exists())
            self.assertEqual(len(list((folder/'20260919/runs').glob('*.json'))),2)


if __name__=='__main__': unittest.main()
