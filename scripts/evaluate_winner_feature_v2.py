"""Comprehensive leakage-safe winner feature V2 evaluation.

Builds candidate features from information already stored in race_history.
Feature groups are selected on an earlier development window, then the final
20% holdout is evaluated once. Only a clearly stronger final model is saved.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.database import DEFAULT_DATABASE_PATH

JST = timezone(timedelta(hours=9))
OUT_DIR = ROOT / "outputs" / "baseline"
REPORT_JSON = OUT_DIR / "WINNER_FEATURE_V2_METRICS.json"
REPORT_TXT = OUT_DIR / "WINNER_FEATURE_V2_METRICS.txt"
MODEL_DIR = Path(os.environ.get("HORSE_RACING_MODEL_DIR", str(ROOT / "models")))
MODEL_PATH = MODEL_DIR / "winner_feature_v2_stable.txt"
META_PATH = MODEL_DIR / "winner_feature_v2_stable.json"

BASE_EXCLUDED = {"race_key", "horse_no", "race_date", "horse_name", "jockey_name", "target_finish_position"}
BASE_CATEGORICAL = {"racecourse_code", "surface", "direction", "track_layout"}
MIN_FINAL_TOP1_GAIN = 0.02
MAX_FINAL_TOP3_DROP = 0.0
DEV_MIN_GAIN = 0.001

CLASS_RANK = {"新馬": 0, "未勝利": 1, "1勝": 2, "2勝": 3, "3勝": 4, "OP": 5, "G3": 6, "G2": 7, "G1": 8}
GROUP_ORDER = [
    "market",
    "physical",
    "style",
    "condition",
    "form_quality",
    "jockey_context",
    "class_weather",
    "race_context",
]


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def race_time_seconds(v):
    try:
        m, s = str(v).split(":", 1)
        return int(m) * 60 + float(s)
    except Exception:
        return None


def parse_positions(v):
    if not v:
        return []
    return [int(x) for x in re.findall(r"\d+", str(v))]


def finish_rates(runs):
    if not runs:
        return (0.0, 0.0, 0.0)
    n = len(runs)
    return (
        sum(int(r["finish_position"]) == 1 for r in runs) / n,
        sum(int(r["finish_position"]) <= 2 for r in runs) / n,
        sum(int(r["finish_position"]) <= 3 for r in runs) / n,
    )


def recent_weighted(runs, getter):
    if not runs:
        return 0.0
    vals = []
    weights = []
    for i, r in enumerate(reversed(runs[-5:]), start=1):
        v = getter(r)
        if v is None:
            continue
        w = 1.0 / i
        vals.append(float(v) * w)
        weights.append(w)
    return sum(vals) / sum(weights) if weights else 0.0


def load_rows():
    with sqlite3.connect(DEFAULT_DATABASE_PATH) as con:
        con.row_factory = sqlite3.Row
        fcols = [str(r[1]) for r in con.execute("PRAGMA table_info(feature_history)")]
        rows = con.execute("""
            SELECT f.*,
                   h.racecourse AS h_racecourse,
                   h.track_condition AS h_track_condition,
                   h.weather AS h_weather,
                   h.popularity AS h_popularity,
                   h.odds AS h_odds,
                   h.finish_position AS actual_finish_position,
                   h.race_time AS h_race_time,
                   h.last_3f AS h_last_3f,
                   h.passing_order AS h_passing_order,
                   h.body_weight AS h_body_weight,
                   h.assigned_weight AS h_assigned_weight,
                   h.race_class AS h_race_class
            FROM feature_history f
            JOIN race_history h ON h.race_key=f.race_key AND h.horse_no=f.horse_no
            WHERE h.finish_position IS NOT NULL
            ORDER BY f.race_date, f.race_key, f.horse_no
        """).fetchall()

        entry_cov = 0
        entry_total = 0
        try:
            entry_total = int(con.execute("SELECT COUNT(*) FROM race_entries").fetchone()[0])
            if entry_total:
                entry_cov = int(con.execute("""
                    SELECT COUNT(*) FROM race_history h
                    JOIN race_entries e ON e.race_key=h.race_key AND e.horse_no=h.horse_no
                """).fetchone()[0])
        except sqlite3.Error:
            pass

    if not rows:
        raise RuntimeError("feature_history is empty")
    base_features = [c for c in fcols if c not in BASE_EXCLUDED]
    return rows, base_features, {"race_entries_rows": entry_total, "race_entries_history_matches": entry_cov}


def split_dates(rows):
    dates = sorted({str(r["race_date"]) for r in rows})
    if len(dates) < 5:
        raise RuntimeError("not enough dates")
    final_n = max(1, math.ceil(len(dates) * 0.20))
    final_start = dates[-final_n]
    pre = [r for r in rows if str(r["race_date"]) < final_start]
    final = [r for r in rows if str(r["race_date"]) >= final_start]

    pre_dates = sorted({str(r["race_date"]) for r in pre})
    dev_n = max(1, math.ceil(len(pre_dates) * 0.25))
    dev_start = pre_dates[-dev_n]
    train = [r for r in pre if str(r["race_date"]) < dev_start]
    dev = [r for r in pre if str(r["race_date"]) >= dev_start]
    if not train or not dev or not final:
        raise RuntimeError("empty train/dev/final split")
    return train, dev, final, dev_start, final_start


def raw_key(r):
    return (str(r["race_key"]), int(r["horse_no"]))


def add_prior_features(rows):
    """Create leakage-safe extra features from earlier dates only."""
    by_date = defaultdict(list)
    for r in rows:
        by_date[str(r["race_date"])].append(r)

    horse_hist = defaultdict(list)
    jockey_hist = defaultdict(list)
    horse_jockey_hist = defaultdict(list)
    out = {}

    for d in sorted(by_date):
        day = by_date[d]
        temp = {}
        for r in day:
            horse = str(r["horse_name"])
            jockey = str(r["jockey_name"])
            hh = horse_hist[horse]
            jh = jockey_hist[jockey]
            hj = horse_jockey_hist[(horse, jockey)]
            prev = hh[-5:]
            last = hh[-1] if hh else None

            odds = safe_float(r["h_odds"], 0.0)
            pop = safe_float(r["h_popularity"], 0.0)
            market = {
                "mkt_log_odds": math.log1p(max(0.0, odds)),
                "mkt_implied": (1.0 / odds) if odds > 0 else 0.0,
                "mkt_popularity": pop,
                "mkt_is_favorite": float(pop == 1),
                "mkt_is_top3": float(0 < pop <= 3),
            }

            bw = safe_float(r["h_body_weight"], 0.0)
            aw = safe_float(r["h_assigned_weight"], 0.0)
            last_bw = safe_float(last["h_body_weight"], 0.0) if last else 0.0
            last_aw = safe_float(last["h_assigned_weight"], 0.0) if last else 0.0
            physical = {
                "phy_body_weight": bw,
                "phy_body_weight_delta": bw - last_bw if bw and last_bw else 0.0,
                "phy_assigned_weight": aw,
                "phy_assigned_weight_delta": aw - last_aw if last else 0.0,
            }

            firsts, lasts, gains, front = [], [], [], []
            for p in prev:
                pos = parse_positions(p["h_passing_order"])
                if pos:
                    firsts.append(pos[0])
                    lasts.append(pos[-1])
                    gains.append(pos[0] - pos[-1])
                    front.append(float(pos[-1] <= 4))
            style = {
                "sty_first_pos_avg": float(np.mean(firsts)) if firsts else 0.0,
                "sty_last_pos_avg": float(np.mean(lasts)) if lasts else 0.0,
                "sty_position_gain_avg": float(np.mean(gains)) if gains else 0.0,
                "sty_front_rate": float(np.mean(front)) if front else 0.0,
                "sty_samples": float(len(lasts)),
            }

            dist = int(r["distance"])
            surface = str(r["surface"])
            course = str(r["racecourse_code"])
            going = str(r["h_track_condition"])
            same_surface = [x for x in hh if str(x["surface"]) == surface]
            same_course = [x for x in hh if str(x["racecourse_code"]) == course]
            same_going = [x for x in hh if str(x["h_track_condition"]) == going]
            near_dist = [x for x in hh if abs(int(x["distance"]) - dist) <= 200]
            sw, _, st3 = finish_rates(same_surface)
            cw, _, ct3 = finish_rates(same_course)
            gw, _, gt3 = finish_rates(same_going)
            dw, _, dt3 = finish_rates(near_dist)
            condition = {
                "cond_surface_win": sw, "cond_surface_top3": st3, "cond_surface_n": float(len(same_surface)),
                "cond_course_win": cw, "cond_course_top3": ct3, "cond_course_n": float(len(same_course)),
                "cond_going_win": gw, "cond_going_top3": gt3, "cond_going_n": float(len(same_going)),
                "cond_distance_win": dw, "cond_distance_top3": dt3, "cond_distance_n": float(len(near_dist)),
            }

            form = {
                "form_weighted_finish": recent_weighted(prev, lambda x: int(x["actual_finish_position"])),
                "form_weighted_last3f": recent_weighted(prev, lambda x: safe_float(x["h_last_3f"], None)),
                "form_weighted_time": recent_weighted(prev, lambda x: race_time_seconds(x["h_race_time"])),
                "form_recent_win": finish_rates(prev)[0],
                "form_recent_top3": finish_rates(prev)[2],
            }
            if len(prev) >= 2:
                form["form_finish_trend"] = safe_float(prev[-2]["actual_finish_position"]) - safe_float(prev[-1]["actual_finish_position"])
            else:
                form["form_finish_trend"] = 0.0

            jw, _, jt3 = finish_rates(jh)
            hjw, _, hjt3 = finish_rates(hj)
            j_course = [x for x in jh if str(x["racecourse_code"]) == course]
            j_surface = [x for x in jh if str(x["surface"]) == surface]
            jcw, _, jct3 = finish_rates(j_course)
            jsw, _, jst3 = finish_rates(j_surface)
            jockey_context = {
                "jc_jockey_win": jw, "jc_jockey_top3": jt3, "jc_jockey_n": float(len(jh)),
                "jc_combo_win": hjw, "jc_combo_top3": hjt3, "jc_combo_n": float(len(hj)),
                "jc_course_win": jcw, "jc_course_top3": jct3, "jc_course_n": float(len(j_course)),
                "jc_surface_win": jsw, "jc_surface_top3": jst3, "jc_surface_n": float(len(j_surface)),
            }

            cur_class = CLASS_RANK.get(str(r["h_race_class"]), CLASS_RANK.get(str(r["race_class"]), -1))
            prev_class = CLASS_RANK.get(str(last["h_race_class"]), -1) if last else -1
            weather = str(r["h_weather"])
            same_weather = [x for x in hh if str(x["h_weather"]) == weather]
            ww, _, wt3 = finish_rates(same_weather)
            class_weather = {
                "cw_class_rank": float(cur_class),
                "cw_class_delta": float(cur_class - prev_class) if prev_class >= 0 and cur_class >= 0 else 0.0,
                "cw_weather_code": float(abs(hash(weather)) % 97),
                "cw_weather_win": ww,
                "cw_weather_top3": wt3,
                "cw_weather_n": float(len(same_weather)),
            }

            temp[raw_key(r)] = {
                "market": market,
                "physical": physical,
                "style": style,
                "condition": condition,
                "form_quality": form,
                "jockey_context": jockey_context,
                "class_weather": class_weather,
            }

        by_race = defaultdict(list)
        for r in day:
            by_race[str(r["race_key"])].append(r)
        for race_rows in by_race.values():
            keys = [raw_key(r) for r in race_rows]
            front_rates = [temp[k]["style"]["sty_front_rate"] for k in keys]
            odds = [temp[k]["market"]["mkt_log_odds"] for k in keys]
            base_strength = [safe_float(r["horse_win_rate"]) for r in race_rows]
            n = len(keys)
            pace_pressure = sum(x >= 0.5 for x in front_rates)
            for idx, (r, k) in enumerate(zip(race_rows, keys)):
                others = [x for j, x in enumerate(base_strength) if j != idx]
                best_other = max(others) if others else 0.0
                temp[k]["race_context"] = {
                    "rc_field_size": float(n),
                    "rc_pace_pressure": float(pace_pressure),
                    "rc_front_rate_vs_field": front_rates[idx] - (float(np.mean(front_rates)) if front_rates else 0.0),
                    "rc_log_odds_vs_field": odds[idx] - (float(np.mean(odds)) if odds else 0.0),
                    "rc_base_win_vs_field": base_strength[idx] - (float(np.mean(base_strength)) if base_strength else 0.0),
                    "rc_base_win_gap_best_other": base_strength[idx] - best_other,
                    "rc_strength_std": float(np.std(base_strength)) if base_strength else 0.0,
                }

        out.update(temp)
        for r in day:
            horse = str(r["horse_name"])
            jockey = str(r["jockey_name"])
            horse_hist[horse].append(r)
            jockey_hist[jockey].append(r)
            horse_jockey_hist[(horse, jockey)].append(r)
    return out


def base_matrix(rows, base_features, maps):
    data = []
    for r in rows:
        vals = []
        for f in base_features:
            v = r[f]
            if f in maps:
                vals.append(float(maps[f].get(str(v), -1)))
            else:
                vals.append(safe_float(v))
        data.append(vals)
    return np.asarray(data, dtype=np.float32)


def category_maps(rows, base_features):
    return {
        f: {v: i for i, v in enumerate(sorted({str(r[f]) for r in rows}))}
        for f in base_features if f in BASE_CATEGORICAL
    }


def feature_names(extra, groups):
    names = []
    for g in groups:
        if not extra:
            continue
        sample = next(iter(extra.values())).get(g, {})
        names.extend(sorted(sample))
    return names


def extended_matrix(rows, base_features, maps, extra, groups):
    base = base_matrix(rows, base_features, maps)
    names = list(base_features)
    extra_names = feature_names(extra, groups)
    if not extra_names:
        return base, names
    arr = np.zeros((len(rows), len(extra_names)), dtype=np.float32)
    for i, r in enumerate(rows):
        merged = {}
        e = extra[raw_key(r)]
        for g in groups:
            merged.update(e.get(g, {}))
        arr[i, :] = [safe_float(merged.get(n, 0.0)) for n in extra_names]
    return np.concatenate([base, arr], axis=1), names + extra_names


def train_binary(xtr, ytr, xval, yval, names):
    import lightgbm as lgb
    ds = lgb.Dataset(xtr, label=ytr, feature_name=names, free_raw_data=False)
    vs = lgb.Dataset(xval, label=yval, reference=ds, feature_name=names, free_raw_data=False)
    return lgb.train(
        {"objective": "binary", "metric": "binary_logloss", "learning_rate": 0.03,
         "num_leaves": 31, "min_data_in_leaf": 30, "feature_fraction": 0.8,
         "bagging_fraction": 0.8, "bagging_freq": 1, "seed": 42, "verbosity": -1},
        ds, num_boost_round=600, valid_sets=[vs],
        callbacks=[lgb.early_stopping(60, verbose=False)]
    )


def metrics(rows, scores):
    grouped = defaultdict(list)
    for r, s in zip(rows, scores):
        grouped[str(r["race_key"])].append((r, float(s)))
    races = top1 = top3 = exact12 = 0
    for items in grouped.values():
        if len(items) < 3:
            continue
        actual = sorted(items, key=lambda x: int(x[0]["actual_finish_position"]))
        pred = sorted(items, key=lambda x: x[1], reverse=True)
        ao = [int(x[0]["horse_no"]) for x in actual[:3]]
        po = [int(x[0]["horse_no"]) for x in pred[:3]]
        races += 1
        top1 += int(po[0] == ao[0])
        top3 += int(ao[0] in set(po[:3]))
        exact12 += int(po[:2] == ao[:2])
    return {
        "races": races,
        "winner_top1_hits": top1,
        "winner_top1_rate": top1 / races,
        "winner_in_top3_hits": top3,
        "winner_in_top3_rate": top3 / races,
        "exact_1_2_hits": exact12,
        "exact_1_2_rate": exact12 / races,
    }


def labels(rows):
    return np.asarray([int(int(r["actual_finish_position"]) == 1) for r in rows], dtype=np.int8)


def fit_eval(train, val, base_features, extra, groups):
    maps = category_maps(train, base_features)
    xtr, names = extended_matrix(train, base_features, maps, extra, groups)
    xval, _ = extended_matrix(val, base_features, maps, extra, groups)
    model = train_binary(xtr, labels(train), xval, labels(val), names)
    return model, metrics(val, model.predict(xval)), names


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        rows, base_features, coverage = load_rows()
        extra = add_prior_features(rows)
        train, dev, final, dev_start, final_start = split_dates(rows)

        _, base_dev, _ = fit_eval(train, dev, base_features, extra, [])
        selected = []
        dev_results = {"baseline": base_dev}
        current = base_dev

        for group in GROUP_ORDER:
            candidate_groups = selected + [group]
            _, m, _ = fit_eval(train, dev, base_features, extra, candidate_groups)
            dev_results[group] = m
            if (m["winner_top1_rate"] >= current["winner_top1_rate"] + DEV_MIN_GAIN and
                    m["winner_in_top3_rate"] >= current["winner_in_top3_rate"] - 0.002):
                selected.append(group)
                current = m

        pre_final = train + dev
        _, base_final, _ = fit_eval(pre_final, final, base_features, extra, [])
        candidate_model, candidate_final, candidate_names = fit_eval(pre_final, final, base_features, extra, selected)

        adopted = (
            bool(selected)
            and candidate_final["winner_top1_rate"] >= base_final["winner_top1_rate"] + MIN_FINAL_TOP1_GAIN
            and candidate_final["winner_in_top3_rate"] >= base_final["winner_in_top3_rate"] - MAX_FINAL_TOP3_DROP
        )

        model_saved = False
        if adopted:
            import lightgbm as lgb
            maps = category_maps(rows, base_features)
            xall, all_names = extended_matrix(rows, base_features, maps, extra, selected)
            yall = labels(rows)
            rounds = max(50, int(candidate_model.best_iteration or 300))
            ds = lgb.Dataset(xall, label=yall, feature_name=all_names, free_raw_data=False)
            final_model = lgb.train(
                {"objective": "binary", "metric": "binary_logloss", "learning_rate": 0.03,
                 "num_leaves": 31, "min_data_in_leaf": 30, "feature_fraction": 0.8,
                 "bagging_fraction": 0.8, "bagging_freq": 1, "seed": 42, "verbosity": -1},
                ds, num_boost_round=rounds
            )
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            final_model.save_model(str(MODEL_PATH))
            META_PATH.write_text(json.dumps({
                "model": "winner_feature_v2_stable",
                "trained_at_jst": now(),
                "selected_groups": selected,
                "feature_names": all_names,
                "dev_start": dev_start,
                "final_start": final_start,
                "baseline_final": base_final,
                "candidate_final": candidate_final,
                "num_boost_round": rounds,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            model_saved = True

        unavailable = {
            "gate_no": "race_history does not retain historical gate_no; race_entries coverage is reported separately",
            "trainer_name": "race_history does not retain historical trainer_name",
            "sex_age": "race_history does not retain historical sex_age",
            "detailed_laps": "not present in current race_history schema",
            "training_work": "not present in current race_history schema",
            "pedigree": "not present in current race_history schema",
        }
        report = {
            "status": "SUCCESS",
            "dev_start": dev_start,
            "final_start": final_start,
            "rows": len(rows),
            "base_feature_count": len(base_features),
            "candidate_groups": GROUP_ORDER,
            "selected_groups": selected,
            "dev_results": dev_results,
            "baseline_final": base_final,
            "candidate_final": candidate_final,
            "adoption_rule": {
                "min_final_top1_gain": MIN_FINAL_TOP1_GAIN,
                "max_final_top3_drop": MAX_FINAL_TOP3_DROP,
            },
            "adopted": adopted,
            "model_saved": model_saved,
            "coverage": coverage,
            "unavailable_without_more_history_data": unavailable,
            "finished_at_jst": now(),
        }
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        lines = [
            "HorseRacingAI WINNER FEATURE V2",
            f"status=SUCCESS dev_start={dev_start} final_start={final_start}",
            "groups_tested=" + ",".join(GROUP_ORDER),
            "selected=" + (",".join(selected) if selected else "NONE"),
            f"baseline_final top1={base_final['winner_top1_rate']:.2%} top3={base_final['winner_in_top3_rate']:.2%} exact12={base_final['exact_1_2_rate']:.2%}",
            f"candidate_final top1={candidate_final['winner_top1_rate']:.2%} top3={candidate_final['winner_in_top3_rate']:.2%} exact12={candidate_final['exact_1_2_rate']:.2%}",
            f"adopted={adopted} model_saved={model_saved}",
            "unavailable=gate_no,trainer_name,sex_age,detailed_laps,training_work,pedigree",
        ]
        REPORT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines), flush=True)
        return 0
    except Exception as exc:
        report = {"status": "FAILED", "error": repr(exc), "finished_at_jst": now()}
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
