"""Evaluate already-trained 5-year models without rebuilding features or retraining.

This evaluator is deliberately tolerant of the two model-bundle formats that have
existed in the local HorseRacingAI tree. It joins race_history explicitly for the
actual finish order and never rebuilds the 5-year history.
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.database import DEFAULT_DATABASE_PATH

JST = timezone(timedelta(hours=9))
OUT_DIR = ROOT / "outputs" / "baseline"
JSON_OUT = OUT_DIR / "POST_5YEAR_RACE_METRICS.json"
TXT_OUT = OUT_DIR / "POST_5YEAR_RACE_METRICS.txt"


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def find_models() -> tuple[Path, Path]:
    candidates = [
        (ROOT / "models" / "win_model.pkl", ROOT / "models" / "top3_model.pkl"),
        (ROOT / "models" / "winner_model.pkl", ROOT / "models" / "place_model.pkl"),
    ]
    for a, b in candidates:
        if a.exists() and b.exists():
            return a, b
    raise RuntimeError("trained win/top3 model pair not found")


def load_bundle(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"model missing: {path}")
    with path.open("rb") as f:
        obj = pickle.load(f)

    # Historical local builds used both dict bundles and direct estimator pickles.
    if isinstance(obj, dict):
        model = None
        for key in ("booster", "model", "estimator", "classifier"):
            if key in obj and obj[key] is not None:
                model = obj[key]
                break
        if model is None:
            raise RuntimeError(f"model object missing in bundle: {path}; keys={sorted(obj.keys())}")

        feature_names = None
        for key in ("feature_names", "features", "feature_columns", "columns"):
            value = obj.get(key)
            if value:
                feature_names = list(value)
                break
        if feature_names is None:
            if hasattr(model, "feature_name"):
                try:
                    feature_names = list(model.feature_name())
                except Exception:
                    feature_names = None
            if feature_names is None and hasattr(model, "feature_names_in_"):
                feature_names = [str(x) for x in model.feature_names_in_]
        if not feature_names:
            raise RuntimeError(f"feature names missing in bundle: {path}; keys={sorted(obj.keys())}")

        validation_start = (
            obj.get("validation_start")
            or obj.get("holdout_start")
            or obj.get("validation_start_date")
        )
        return {
            "model": model,
            "feature_names": feature_names,
            "category_maps": dict(obj.get("category_maps", {})),
            "validation_start": validation_start,
            "raw_keys": sorted(obj.keys()),
        }

    # A direct sklearn/lightgbm estimator is also accepted if it carries feature names.
    model = obj
    feature_names = None
    if hasattr(model, "feature_name"):
        try:
            feature_names = list(model.feature_name())
        except Exception:
            feature_names = None
    if feature_names is None and hasattr(model, "feature_names_in_"):
        feature_names = [str(x) for x in model.feature_names_in_]
    if not feature_names:
        raise RuntimeError(f"unsupported direct model pickle: {path}; type={type(model).__name__}")
    return {
        "model": model,
        "feature_names": feature_names,
        "category_maps": {},
        "validation_start": None,
        "raw_keys": [],
    }


def transform(rows: list[sqlite3.Row], bundle: dict) -> list[list[float]]:
    feature_names = list(bundle["feature_names"])
    category_maps = dict(bundle.get("category_maps", {}))
    available = set(rows[0].keys()) if rows else set()
    missing = [name for name in feature_names if name not in available]
    if missing:
        raise RuntimeError(f"model features missing from validation query: {missing}")
    matrix: list[list[float]] = []
    for row in rows:
        values: list[float] = []
        for name in feature_names:
            value = row[name]
            if name in category_maps:
                values.append(float(category_maps[name].get(str(value), -1)))
            else:
                values.append(float(value) if value is not None else 0.0)
        matrix.append(values)
    return matrix


def predict_probabilities(bundle: dict, matrix: list[list[float]]) -> list[float]:
    model = bundle["model"]
    if hasattr(model, "predict_proba"):
        values = model.predict_proba(matrix)
        return [float(row[1]) for row in values]
    if hasattr(model, "predict"):
        values = model.predict(matrix)
        return [float(x) for x in values]
    raise RuntimeError(f"model has no prediction method: {type(model).__name__}")


def load_rows(validation_start: str) -> list[sqlite3.Row]:
    with sqlite3.connect(DEFAULT_DATABASE_PATH) as con:
        con.row_factory = sqlite3.Row
        return con.execute(
            """
            SELECT f.*,
                   h.finish_position AS actual_finish_position,
                   h.horse_name AS actual_horse_name
            FROM feature_history AS f
            JOIN race_history AS h
              ON h.race_key=f.race_key AND h.horse_no=f.horse_no
            WHERE f.race_date >= ?
            ORDER BY f.race_date, f.race_key, f.horse_no
            """,
            (validation_start,),
        ).fetchall()


def fallback_validation_start() -> str:
    # The successful 5-year training run shown locally used this chronological holdout.
    # If an older bundle omitted the field, derive the same 20% day-based split from
    # feature_history rather than retraining.
    with sqlite3.connect(DEFAULT_DATABASE_PATH) as con:
        dates = [
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT race_date FROM feature_history ORDER BY race_date"
            )
        ]
    if len(dates) < 2:
        raise RuntimeError("cannot derive validation_start")
    import math
    validation_days = max(1, math.ceil(len(dates) * 0.2))
    return dates[len(dates) - validation_days]


def main() -> int:
    report = {"status": "RUNNING", "started_at_jst": now()}
    try:
        win_path, top3_path = find_models()
        win = load_bundle(win_path)
        top3 = load_bundle(top3_path)
        validation_start = str(
            win.get("validation_start")
            or top3.get("validation_start")
            or fallback_validation_start()
        )
        rows = load_rows(validation_start)
        if not rows:
            raise RuntimeError("validation rows are empty")

        win_p = predict_probabilities(win, transform(rows, win))
        top3_p = predict_probabilities(top3, transform(rows, top3))

        grouped = defaultdict(list)
        for row, wp, tp in zip(rows, win_p, top3_p):
            grouped[str(row["race_key"])].append((row, wp, tp))

        races = winner_hit = exact12 = exact123 = winner_top3 = top3_set = 0
        for items in grouped.values():
            usable = [x for x in items if x[0]["actual_finish_position"] is not None]
            if len(usable) < 3:
                continue
            actual = sorted(usable, key=lambda x: int(x[0]["actual_finish_position"]))
            by_win = sorted(usable, key=lambda x: x[1], reverse=True)
            by_top3 = sorted(usable, key=lambda x: x[2], reverse=True)
            actual_order = [int(x[0]["horse_no"]) for x in actual[:3]]
            pred_order = [int(x[0]["horse_no"]) for x in by_win[:3]]
            pred_top3 = {int(x[0]["horse_no"]) for x in by_top3[:3]}
            races += 1
            winner_hit += int(pred_order[0] == actual_order[0])
            exact12 += int(pred_order[:2] == actual_order[:2])
            exact123 += int(pred_order[:3] == actual_order[:3])
            winner_top3 += int(actual_order[0] in set(pred_order[:3]))
            top3_set += int(set(actual_order) == pred_top3)

        if races == 0:
            raise RuntimeError("no evaluable races")

        metrics = {
            "validation_start": validation_start,
            "validation_races": races,
            "winner_top1_hits": winner_hit,
            "winner_top1_rate": winner_hit / races,
            "exact_1_2_hits": exact12,
            "exact_1_2_rate": exact12 / races,
            "exact_1_2_3_hits": exact123,
            "exact_1_2_3_rate": exact123 / races,
            "winner_in_predicted_top3_hits": winner_top3,
            "winner_in_predicted_top3_rate": winner_top3 / races,
            "actual_top3_set_captured_hits": top3_set,
            "actual_top3_set_captured_rate": top3_set / races,
            "win_model": str(win_path),
            "top3_model": str(top3_path),
        }
        report.update({"status": "SUCCESS", "metrics": metrics, "finished_at_jst": now()})
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        JSON_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            "HorseRacingAI 5YEAR RACE METRICS",
            "status=SUCCESS",
            f"validation_start={validation_start}",
            f"validation_races={races}",
            f"winner_top1={winner_hit}/{races} ({winner_hit/races:.2%})",
            f"exact_1_2={exact12}/{races} ({exact12/races:.2%})",
            f"exact_1_2_3={exact123}/{races} ({exact123/races:.2%})",
            f"winner_in_top3={winner_top3}/{races} ({winner_top3/races:.2%})",
            f"actual_top3_set_captured={top3_set}/{races} ({top3_set/races:.2%})",
        ]
        TXT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines), flush=True)
        return 0
    except Exception as exc:
        report.update({"status": "FAILED", "error": repr(exc), "finished_at_jst": now()})
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        JSON_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
