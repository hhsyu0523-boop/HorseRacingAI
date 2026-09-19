"""Read-only model recovery audit; never trains or publishes a model."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.feature_engine import FeatureEngineeringEngine
from scripts.train_model import EXCLUDED_COLUMNS


def main():
    data = Path(sys.argv[1]).resolve()
    report = {"training_executed": False, "models_adopted": False, "cutoff_exclusive": "2026-09-19"}
    report["dependencies"] = {}
    for name in ("lightgbm", "xgboost", "numpy"):
        try:
            report["dependencies"][name] = importlib.import_module(name).__version__
        except Exception as exc:
            report["dependencies"][name] = repr(exc)
    with sqlite3.connect(":memory:") as scratch:
        FeatureEngineeringEngine._initialize_table(scratch)
        expected = [r[1] for r in scratch.execute("PRAGMA table_info(feature_history)")]
    report["pre_features"] = [c for c in expected if c not in EXCLUDED_COLUMNS]
    with sqlite3.connect((data / "database/horse_racing.db").as_uri() + "?mode=ro", uri=True) as con:
        report["tables"] = {}
        for table in ("race_history", "feature_history"):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            if not cols:
                report["tables"][table] = {"missing": True}
                continue
            summary = con.execute(f"SELECT count(*),min(race_date),max(race_date),count(distinct race_key) FROM {table}").fetchone()
            excluded = con.execute(f"SELECT count(*) FROM {table} WHERE race_date >= '2026-09-19'").fetchone()[0]
            holdout = con.execute(f"SELECT count(*),count(distinct race_key) FROM {table} WHERE race_date >= '2025-08-24' AND race_date <= '2026-08-16'").fetchone()
            report["tables"][table] = dict(columns=cols, summary=summary, rows_on_or_after_cutoff=excluded, recorded_holdout_counts=holdout)
            if table == "feature_history":
                report["training_features_missing_from_pre"] = sorted(set(cols) - EXCLUDED_COLUMNS - set(report["pre_features"]))
                report["pre_features_missing_from_training"] = sorted(set(report["pre_features"]) - set(cols))
    report["local_model_files"] = [dict(name=p.name, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted((data / "models").glob("*.pkl"))]
    output = ROOT / "docs/evidence/daily-20260919/model_input_audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
