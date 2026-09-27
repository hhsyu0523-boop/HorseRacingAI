"""Write reviewable synthetic Phase A evidence; never marks live acceptance passed."""
import argparse
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scripts.daily_operation import save_pre,reconcile
from scripts.daily_source import normalize
from test_daily_source import program_records
from test_daily_pre import fixture_program,fixture_predictor,morning


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    log=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_daily_*.py')
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    (args.output/'tests.txt').write_text(log.getvalue(),encoding='utf-8')
    report=dict(evidence_type='SYNTHETIC_TESTS_NOT_LIVE_ACCEPTANCE',
                tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                success=result.wasSuccessful(),phase_a_verified_live=False,
                phase_b='NOT_STARTED_PHASE_A_GATE')
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp)
        pre,_=save_pre(folder,fixture_program(),fixture_predictor,{'fixture_only':True},morning)
        raw_before=(folder/'PRE.json').read_bytes()
        results=normalize(program_records(True),'20260919','results','2026-09-19T16:00:00+09:00')
        final=reconcile(folder,results,datetime.fromisoformat('2026-09-19T16:01:00+09:00'))
        report.update(pre_unchanged=raw_before==(folder/'PRE.json').read_bytes(),
                      synthetic_pre=pre,synthetic_final=final)
    (args.output/'synthetic_phase_a.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(log.getvalue())
    print('SYNTHETIC evidence only; live Phase A acceptance remains false.')
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__': raise SystemExit(main())
