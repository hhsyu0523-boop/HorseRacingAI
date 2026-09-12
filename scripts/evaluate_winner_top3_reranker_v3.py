"""Winner V3: leakage-safe reranking of the baseline top-3 only.

Goal: improve winner Top1 without sacrificing winner-in-Top3 capture.  The
candidate is structurally restricted to reorder the baseline Top3 set, so the
winner-in-Top3 set is unchanged. DEV is split into reranker-train/tune; FINAL is
opened once after model/threshold choices are frozen.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import evaluate_winner_feature_v2 as v2

JST = timezone(timedelta(hours=9))
OUT_DIR = ROOT / "outputs" / "baseline"
REPORT_JSON = OUT_DIR / "WINNER_TOP3_RERANKER_V3_METRICS.json"
REPORT_TXT = OUT_DIR / "WINNER_TOP3_RERANKER_V3_METRICS.txt"
MODEL_DIR = Path(os.environ.get("HORSE_RACING_MODEL_DIR", str(ROOT / "models")))
MODEL_PATH = MODEL_DIR / "winner_top3_reranker_v3_candidate.txt"
META_PATH = MODEL_DIR / "winner_top3_reranker_v3_candidate.json"
THRESHOLDS = [0.00, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20]
MIN_TOP1_GAIN = 0.01
MAX_EXACT12_DROP = 0.002


def now():
    return datetime.now(JST).isoformat(timespec="seconds")


def train_fixed(x, y, names, rounds):
    import lightgbm as lgb
    ds = lgb.Dataset(x, label=y, feature_name=names, free_raw_data=False)
    return lgb.train(
        {"objective": "binary", "metric": "binary_logloss", "learning_rate": 0.03,
         "num_leaves": 31, "min_data_in_leaf": 30, "feature_fraction": 0.8,
         "bagging_fraction": 0.8, "bagging_freq": 1, "seed": 73, "verbosity": -1},
        ds, num_boost_round=max(30, int(rounds)))


def flatten_extra(extra, key):
    merged = {}
    for group in v2.GROUP_ORDER:
        merged.update(extra[key].get(group, {}))
    return merged


def extra_names(extra):
    sample_key = next(iter(extra))
    names = []
    for group in v2.GROUP_ORDER:
        names.extend(sorted(extra[sample_key].get(group, {})))
    return names


def group_races(rows, scores):
    grouped = defaultdict(list)
    for i, (r, s) in enumerate(zip(rows, scores)):
        grouped[str(r["race_key"])].append((i, r, float(s)))
    return grouped


def candidate_matrix(rows, scores, extra, names_extra, allowed_races=None):
    xs, ys, meta = [], [], []
    grouped = group_races(rows, scores)
    for race_key, items in grouped.items():
        if allowed_races is not None and race_key not in allowed_races:
            continue
        ordered = sorted(items, key=lambda x: x[2], reverse=True)
        top = ordered[:3]
        actual = next((int(r["horse_no"]) for _, r, _ in items if int(r["actual_finish_position"]) == 1), None)
        if actual is None or actual not in {int(r["horse_no"]) for _, r, _ in top}:
            continue
        top_scores = np.asarray([x[2] for x in top], dtype=float)
        mean = float(top_scores.mean())
        std = float(top_scores.std()) or 1.0
        for rank, (_, r, score) in enumerate(top, start=1):
            e = flatten_extra(extra, v2.raw_key(r))
            row = [
                float(score),
                float(rank == 1), float(rank == 2), float(rank == 3),
                float(score - top_scores[0]),
                float(score - mean),
                float((score - mean) / std),
                float(top_scores[0] - top_scores[1]),
                float(top_scores[1] - top_scores[2]),
            ]
            row.extend(v2.safe_float(e.get(n, 0.0)) for n in names_extra)
            xs.append(row)
            ys.append(int(int(r["horse_no"]) == actual))
            meta.append((race_key, int(r["horse_no"]), rank))
    feature_names = ["base_score", "base_rank1", "base_rank2", "base_rank3",
                     "gap_vs_top1", "score_vs_top3_mean", "score_z_top3",
                     "top1_top2_gap", "top2_top3_gap"] + names_extra
    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.int8), meta, feature_names


def rerank_orders(rows, base_scores, rerank_scores_by_key, threshold):
    grouped = group_races(rows, base_scores)
    orders = {}
    switched = 0
    for race_key, items in grouped.items():
        base = sorted(items, key=lambda x: x[2], reverse=True)
        top = base[:3]
        rs = [(int(r["horse_no"]), float(rerank_scores_by_key.get((race_key, int(r["horse_no"])), -1e9)))
              for _, r, _ in top]
        rs_sorted = sorted(rs, key=lambda x: x[1], reverse=True)
        margin = rs_sorted[0][1] - rs_sorted[1][1]
        if margin >= threshold and rs_sorted[0][1] > -1e8:
            top_horses = [h for h, _ in rs_sorted]
        else:
            top_horses = [int(r["horse_no"]) for _, r, _ in top]
        base_horses = [int(r["horse_no"]) for _, r, _ in base]
        if top_horses[0] != base_horses[0]:
            switched += 1
        orders[race_key] = top_horses + base_horses[3:]
    return orders, switched


def order_metrics(rows, orders):
    grouped = defaultdict(list)
    for r in rows:
        grouped[str(r["race_key"])].append(r)
    races = top1 = top3 = exact12 = 0
    for key, items in grouped.items():
        if key not in orders or len(items) < 3:
            continue
        actual = [int(r["horse_no"]) for r in sorted(items, key=lambda r: int(r["actual_finish_position"]))[:3]]
        pred = orders[key]
        races += 1
        top1 += int(pred[0] == actual[0])
        top3 += int(actual[0] in set(pred[:3]))
        exact12 += int(pred[:2] == actual[:2])
    return {"races": races, "winner_top1_hits": top1, "winner_top1_rate": top1/races,
            "winner_in_top3_hits": top3, "winner_in_top3_rate": top3/races,
            "exact_1_2_hits": exact12, "exact_1_2_rate": exact12/races}


def base_orders(rows, scores):
    return {k: [int(r["horse_no"]) for _, r, _ in sorted(v, key=lambda x: x[2], reverse=True)]
            for k, v in group_races(rows, scores).items()}


def score_map(model, x, meta):
    p = model.predict(x)
    return {(race_key, horse_no): float(s) for (race_key, horse_no, _), s in zip(meta, p)}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, base_features, coverage = v2.load_rows()
    extra = v2.add_prior_features(rows)
    train, dev, final, dev_start, final_start = v2.split_dates(rows)
    names_extra = extra_names(extra)

    # Baseline DEV model: train only on TRAIN; DEV is untouched baseline scoring.
    maps_train = v2.category_maps(train, base_features)
    xtr, base_names = v2.extended_matrix(train, base_features, maps_train, extra, [])
    xdev, _ = v2.extended_matrix(dev, base_features, maps_train, extra, [])
    base_dev_model = v2.train_binary(xtr, v2.labels(train), xdev, v2.labels(dev), base_names)
    base_rounds = int(base_dev_model.best_iteration or 300)
    dev_scores = base_dev_model.predict(xdev)

    dev_dates = sorted({str(r["race_date"]) for r in dev})
    cut = max(1, int(math.floor(len(dev_dates) * 0.60)))
    rerank_train_dates = set(dev_dates[:cut])
    tune_dates = set(dev_dates[cut:]) or set(dev_dates[-1:])
    rerank_train = [r for r in dev if str(r["race_date"]) in rerank_train_dates]
    tune = [r for r in dev if str(r["race_date"]) in tune_dates]
    score_by_key = {v2.raw_key(r): float(s) for r, s in zip(dev, dev_scores)}
    train_scores = np.asarray([score_by_key[v2.raw_key(r)] for r in rerank_train])
    tune_scores = np.asarray([score_by_key[v2.raw_key(r)] for r in tune])

    xrt, yrt, mrt, rerank_names = candidate_matrix(rerank_train, train_scores, extra, names_extra)
    xru, yru, mru, _ = candidate_matrix(tune, tune_scores, extra, names_extra)
    if len(np.unique(yrt)) < 2 or len(np.unique(yru)) < 2:
        raise RuntimeError("reranker DEV split lacks both classes")
    reranker = v2.train_binary(xrt, yrt, xru, yru, rerank_names)
    rerank_rounds = int(reranker.best_iteration or 200)
    tune_rmap = score_map(reranker, xru, mru)
    base_tune = order_metrics(tune, base_orders(tune, tune_scores))

    tuning = []
    for threshold in THRESHOLDS:
        orders, switches = rerank_orders(tune, tune_scores, tune_rmap, threshold)
        m = order_metrics(tune, orders)
        tuning.append({"threshold": threshold, "switches": switches, **m,
                       "top1_gain": m["winner_top1_rate"] - base_tune["winner_top1_rate"],
                       "exact12_gain": m["exact_1_2_rate"] - base_tune["exact_1_2_rate"]})
    valid = [x for x in tuning if x["exact12_gain"] >= -MAX_EXACT12_DROP]
    pool = valid or tuning
    chosen = sorted(pool, key=lambda x: (x["winner_top1_rate"], x["exact_1_2_rate"], -x["switches"]), reverse=True)[0]
    threshold = float(chosen["threshold"])

    # Refit reranker on all DEV after threshold is frozen. Scores remain OOS from TRAIN baseline.
    xrd, yrd, mrd, _ = candidate_matrix(dev, dev_scores, extra, names_extra)
    reranker_final = train_fixed(xrd, yrd, rerank_names, rerank_rounds)

    # FINAL baseline is trained on all pre-final data with fixed rounds chosen before FINAL.
    pre_final = train + dev
    maps_pre = v2.category_maps(pre_final, base_features)
    xpre, pre_names = v2.extended_matrix(pre_final, base_features, maps_pre, extra, [])
    xfinal, _ = v2.extended_matrix(final, base_features, maps_pre, extra, [])
    baseline_final_model = train_fixed(xpre, v2.labels(pre_final), pre_names, base_rounds)
    final_scores = baseline_final_model.predict(xfinal)
    baseline_final = order_metrics(final, base_orders(final, final_scores))

    xrf, yrf, mrf, _ = candidate_matrix(final, final_scores, extra, names_extra)
    final_rmap = score_map(reranker_final, xrf, mrf)
    candidate_orders, switches = rerank_orders(final, final_scores, final_rmap, threshold)
    candidate_final = order_metrics(final, candidate_orders)

    adopted = (
        candidate_final["winner_top1_rate"] >= baseline_final["winner_top1_rate"] + MIN_TOP1_GAIN
        and candidate_final["winner_in_top3_hits"] == baseline_final["winner_in_top3_hits"]
        and candidate_final["exact_1_2_rate"] >= baseline_final["exact_1_2_rate"] - MAX_EXACT12_DROP
    )
    model_saved = False
    if adopted:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        reranker_final.save_model(str(MODEL_PATH))
        META_PATH.write_text(json.dumps({"model":"winner_top3_reranker_v3_candidate",
            "trained_at_jst":now(), "threshold":threshold, "feature_names":rerank_names,
            "base_rounds":base_rounds, "rerank_rounds":rerank_rounds,
            "baseline_final":baseline_final, "candidate_final":candidate_final,
            "note":"Candidate only; Stable is not overwritten."}, ensure_ascii=False, indent=2), encoding="utf-8")
        model_saved = True

    report = {"status":"SUCCESS", "dev_start":dev_start, "final_start":final_start,
              "rerank_train_dates":[min(rerank_train_dates), max(rerank_train_dates)],
              "tune_dates":[min(tune_dates), max(tune_dates)],
              "baseline_dev_tune":base_tune, "threshold_tuning":tuning,
              "selected_threshold":threshold, "baseline_final":baseline_final,
              "candidate_final":candidate_final, "final_switches":switches,
              "top1_gain_pt":100*(candidate_final["winner_top1_rate"]-baseline_final["winner_top1_rate"]),
              "top3_gain_pt":100*(candidate_final["winner_in_top3_rate"]-baseline_final["winner_in_top3_rate"]),
              "exact12_gain_pt":100*(candidate_final["exact_1_2_rate"]-baseline_final["exact_1_2_rate"]),
              "adopted":adopted, "model_saved":model_saved,
              "adoption_rule":{"min_top1_gain_pt":100*MIN_TOP1_GAIN,"top3_hits_must_match":True,
                               "max_exact12_drop_pt":100*MAX_EXACT12_DROP},
              "coverage":coverage}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = (f"HorseRacingAI WINNER TOP3 RERANKER V3\nstatus=SUCCESS final_start={final_start}\n"
            f"baseline top1={100*baseline_final['winner_top1_rate']:.2f}% top3={100*baseline_final['winner_in_top3_rate']:.2f}% exact12={100*baseline_final['exact_1_2_rate']:.2f}%\n"
            f"candidate top1={100*candidate_final['winner_top1_rate']:.2f}% top3={100*candidate_final['winner_in_top3_rate']:.2f}% exact12={100*candidate_final['exact_1_2_rate']:.2f}%\n"
            f"gain top1={report['top1_gain_pt']:+.2f}pt top3={report['top3_gain_pt']:+.2f}pt exact12={report['exact12_gain_pt']:+.2f}pt switches={switches}\n"
            f"threshold={threshold:.2f} adopted={adopted} model_saved={model_saved}\n")
    REPORT_TXT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
