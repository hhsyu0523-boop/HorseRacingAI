"""Self-contained 5-year race-level holdout evaluation.

Uses only the current leakage-safe feature_history table and race_history labels.
It does not depend on previously saved model bundles, so local model feature-schema
mismatches cannot invalidate the evaluation.
"""
from __future__ import annotations

import json
import math
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
JSON_OUT = OUT_DIR / "POST_5YEAR_SELFCONTAINED_METRICS.json"
TXT_OUT = OUT_DIR / "POST_5YEAR_SELFCONTAINED_METRICS.txt"

EXCLUDED = {"race_key", "horse_no", "race_date", "horse_name", "jockey_name", "target_finish_position"}
CATEGORICAL = {"racecourse_code", "surface", "direction", "track_layout"}


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def load_rows():
    with sqlite3.connect(DEFAULT_DATABASE_PATH) as con:
        con.row_factory = sqlite3.Row
        cols = [str(r[1]) for r in con.execute("PRAGMA table_info(feature_history)")]
        rows = con.execute(
            """
            SELECT f.*, h.finish_position AS actual_finish_position
            FROM feature_history f
            JOIN race_history h ON h.race_key=f.race_key AND h.horse_no=f.horse_no
            ORDER BY f.race_date, f.race_key, f.horse_no
            """
        ).fetchall()
    if not rows:
        raise RuntimeError("feature_history is empty")
    features = [c for c in cols if c not in EXCLUDED]
    return rows, features


def split_by_date(rows, ratio=0.2):
    dates = sorted({str(r["race_date"]) for r in rows})
    if len(dates) < 2:
        raise RuntimeError("not enough dates")
    n_val = max(1, math.ceil(len(dates) * ratio))
    start = dates[len(dates)-n_val]
    train = [r for r in rows if str(r["race_date"]) < start]
    val = [r for r in rows if str(r["race_date"]) >= start]
    if not train or not val:
        raise RuntimeError("empty train/validation split")
    return train, val, start


def category_maps(train, features):
    return {
        f: {v:i for i,v in enumerate(sorted({str(r[f]) for r in train}))}
        for f in features if f in CATEGORICAL
    }


def matrix(rows, features, maps):
    out=[]
    for r in rows:
        vals=[]
        for f in features:
            v=r[f]
            if f in maps:
                vals.append(float(maps[f].get(str(v), -1)))
            else:
                vals.append(float(v) if v is not None else 0.0)
        out.append(vals)
    return np.asarray(out, dtype=np.float32)


def train_model(x_train, y_train, x_val, y_val, features):
    import lightgbm as lgb
    y_train = np.asarray(y_train, dtype=np.int8)
    y_val = np.asarray(y_val, dtype=np.int8)
    ds=lgb.Dataset(x_train,label=y_train,feature_name=features,free_raw_data=False)
    vs=lgb.Dataset(x_val,label=y_val,reference=ds,feature_name=features,free_raw_data=False)
    model=lgb.train(
        {"objective":"binary","metric":"binary_logloss","learning_rate":0.03,"num_leaves":31,
         "feature_fraction":0.8,"bagging_fraction":0.8,"bagging_freq":1,"seed":42,"verbosity":-1},
        ds,num_boost_round=500,valid_sets=[vs],callbacks=[lgb.early_stopping(50,verbose=False)]
    )
    return model


def main() -> int:
    report={"status":"RUNNING","started_at_jst":now()}
    try:
        rows, features = load_rows()
        train, val, validation_start = split_by_date(rows,0.2)
        maps = category_maps(train,features)
        x_train=matrix(train,features,maps)
        x_val=matrix(val,features,maps)
        y_win=[int(int(r["actual_finish_position"])==1) for r in train]
        y_top3=[int(int(r["actual_finish_position"])<=3) for r in train]
        vy_win=[int(int(r["actual_finish_position"])==1) for r in val]
        vy_top3=[int(int(r["actual_finish_position"])<=3) for r in val]
        win=train_model(x_train,y_win,x_val,vy_win,features)
        top3=train_model(x_train,y_top3,x_val,vy_top3,features)
        wp=[float(x) for x in win.predict(x_val)]
        tp=[float(x) for x in top3.predict(x_val)]

        grouped=defaultdict(list)
        for r,a,b in zip(val,wp,tp):
            grouped[str(r["race_key"])].append((r,a,b))

        races=winner=exact12=exact123=winner_top3=top3set=0
        for items in grouped.values():
            usable=[x for x in items if x[0]["actual_finish_position"] is not None]
            if len(usable)<3:
                continue
            actual=sorted(usable,key=lambda x:int(x[0]["actual_finish_position"]))
            bywin=sorted(usable,key=lambda x:x[1],reverse=True)
            bytop3=sorted(usable,key=lambda x:x[2],reverse=True)
            ao=[int(x[0]["horse_no"]) for x in actual[:3]]
            po=[int(x[0]["horse_no"]) for x in bywin[:3]]
            ps={int(x[0]["horse_no"]) for x in bytop3[:3]}
            races+=1
            winner += int(po[0]==ao[0])
            exact12 += int(po[:2]==ao[:2])
            exact123 += int(po[:3]==ao[:3])
            winner_top3 += int(ao[0] in set(po[:3]))
            top3set += int(set(ao)==ps)
        if races==0:
            raise RuntimeError("no evaluable races")

        metrics={
            "validation_start":validation_start,
            "train_rows":len(train),"validation_rows":len(val),"feature_count":len(features),
            "validation_races":races,
            "winner_top1_hits":winner,"winner_top1_rate":winner/races,
            "exact_1_2_hits":exact12,"exact_1_2_rate":exact12/races,
            "exact_1_2_3_hits":exact123,"exact_1_2_3_rate":exact123/races,
            "winner_in_predicted_top3_hits":winner_top3,"winner_in_predicted_top3_rate":winner_top3/races,
            "actual_top3_set_captured_hits":top3set,"actual_top3_set_captured_rate":top3set/races,
        }
        report={"status":"SUCCESS","metrics":metrics,"finished_at_jst":now()}
        OUT_DIR.mkdir(parents=True,exist_ok=True)
        JSON_OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        lines=[
            "HorseRacingAI 5YEAR SELF-CONTAINED HOLDOUT",
            "status=SUCCESS",
            f"validation_start={validation_start}",
            f"train_rows={len(train)} validation_rows={len(val)} features={len(features)}",
            f"validation_races={races}",
            f"winner_top1={winner}/{races} ({winner/races:.2%})",
            f"exact_1_2={exact12}/{races} ({exact12/races:.2%})",
            f"exact_1_2_3={exact123}/{races} ({exact123/races:.2%})",
            f"winner_in_top3={winner_top3}/{races} ({winner_top3/races:.2%})",
            f"actual_top3_set_captured={top3set}/{races} ({top3set/races:.2%})",
        ]
        TXT_OUT.write_text("\n".join(lines)+"\n",encoding="utf-8")
        print("\n".join(lines),flush=True)
        return 0
    except Exception as exc:
        report={"status":"FAILED","error":repr(exc),"finished_at_jst":now()}
        OUT_DIR.mkdir(parents=True,exist_ok=True)
        JSON_OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
