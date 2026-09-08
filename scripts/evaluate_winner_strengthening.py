"""Validate stronger winner models on the fixed 5-year holdout and train only if adopted."""
from __future__ import annotations

import json
import math
import os
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
REPORT_JSON = OUT_DIR / "WINNER_STRENGTHENING_METRICS.json"
REPORT_TXT = OUT_DIR / "WINNER_STRENGTHENING_METRICS.txt"
MODEL_DIR = Path(os.environ.get("HORSE_RACING_MODEL_DIR", str(ROOT / "models")))
MODEL_PATH = MODEL_DIR / "winner_ranker_stable.txt"
META_PATH = MODEL_DIR / "winner_ranker_stable.json"

EXCLUDED = {"race_key", "horse_no", "race_date", "horse_name", "jockey_name", "target_finish_position"}
CATEGORICAL = {"racecourse_code", "surface", "direction", "track_layout"}
MIN_TOP1_GAIN = 0.02


def now():
    return datetime.now(JST).isoformat(timespec="seconds")


def load_rows():
    with sqlite3.connect(DEFAULT_DATABASE_PATH) as con:
        con.row_factory = sqlite3.Row
        cols = [str(r[1]) for r in con.execute("PRAGMA table_info(feature_history)")]
        rows = con.execute("""
            SELECT f.*, h.finish_position AS actual_finish_position
            FROM feature_history f
            JOIN race_history h ON h.race_key=f.race_key AND h.horse_no=f.horse_no
            WHERE h.finish_position IS NOT NULL
            ORDER BY f.race_date, f.race_key, f.horse_no
        """).fetchall()
    if not rows:
        raise RuntimeError("feature_history is empty")
    return rows, [c for c in cols if c not in EXCLUDED]


def split_by_date(rows, ratio=0.2):
    dates = sorted({str(r["race_date"]) for r in rows})
    n_val = max(1, math.ceil(len(dates) * ratio))
    start = dates[len(dates)-n_val]
    train = [r for r in rows if str(r["race_date"]) < start]
    val = [r for r in rows if str(r["race_date"]) >= start]
    if not train or not val:
        raise RuntimeError("empty train/validation split")
    return train, val, start


def category_maps(train, features):
    return {f: {v:i for i,v in enumerate(sorted({str(r[f]) for r in train}))}
            for f in features if f in CATEGORICAL}


def matrix(rows, features, maps):
    data=[]
    for r in rows:
        vals=[]
        for f in features:
            v=r[f]
            if f in maps:
                vals.append(float(maps[f].get(str(v), -1)))
            else:
                try:
                    vals.append(float(v) if v is not None else 0.0)
                except (TypeError, ValueError):
                    vals.append(0.0)
        data.append(vals)
    return np.asarray(data, dtype=np.float32)


def add_race_relative(base, rows, features):
    numeric_idx=[i for i,f in enumerate(features) if f not in CATEGORICAL]
    z=np.zeros((len(rows), len(numeric_idx)), dtype=np.float32)
    pct=np.zeros_like(z)
    grouped=defaultdict(list)
    for i,r in enumerate(rows):
        grouped[str(r["race_key"])].append(i)
    for idxs in grouped.values():
        arr=base[np.asarray(idxs)[:,None], np.asarray(numeric_idx)[None,:]]
        mean=arr.mean(axis=0)
        std=arr.std(axis=0)
        std=np.where(std < 1e-8, 1.0, std)
        z[idxs,:]=(arr-mean)/std
        order=np.argsort(np.argsort(arr,axis=0),axis=0)
        denom=max(1,len(idxs)-1)
        pct[idxs,:]=order/denom
    names=list(features)+[features[i]+"__race_z" for i in numeric_idx]+[features[i]+"__race_pct" for i in numeric_idx]
    return np.concatenate([base,z,pct],axis=1), names


def groups(rows):
    out=[]
    cur=None
    n=0
    for r in rows:
        key=str(r["race_key"])
        if cur is None:
            cur=key
        if key!=cur:
            out.append(n); n=0; cur=key
        n+=1
    if n: out.append(n)
    return out


def relevance(rows):
    # Winner is deliberately dominant because the primary KPI is Top1 winner accuracy.
    rel=[]
    for r in rows:
        p=int(r["actual_finish_position"])
        rel.append(7 if p==1 else 3 if p==2 else 1 if p==3 else 0)
    return np.asarray(rel,dtype=np.int32)


def train_binary(xtr, ytr, xval, yval, names):
    import lightgbm as lgb
    ds=lgb.Dataset(xtr,label=ytr,feature_name=names,free_raw_data=False)
    vs=lgb.Dataset(xval,label=yval,reference=ds,feature_name=names,free_raw_data=False)
    return lgb.train({"objective":"binary","metric":"binary_logloss","learning_rate":0.03,
                      "num_leaves":31,"feature_fraction":0.8,"bagging_fraction":0.8,
                      "bagging_freq":1,"seed":42,"verbosity":-1},
                     ds,num_boost_round=500,valid_sets=[vs],callbacks=[lgb.early_stopping(50,verbose=False)])


def train_ranker(xtr, rtr, gtr, xval, rval, gval, names):
    import lightgbm as lgb
    ds=lgb.Dataset(xtr,label=rtr,group=gtr,feature_name=names,free_raw_data=False)
    vs=lgb.Dataset(xval,label=rval,group=gval,reference=ds,feature_name=names,free_raw_data=False)
    return lgb.train({"objective":"lambdarank","metric":"ndcg","ndcg_eval_at":[1,3],
                      "learning_rate":0.03,"num_leaves":31,"min_data_in_leaf":30,
                      "feature_fraction":0.8,"bagging_fraction":0.8,"bagging_freq":1,
                      "lambdarank_truncation_level":6,"seed":42,"verbosity":-1},
                     ds,num_boost_round=700,valid_sets=[vs],callbacks=[lgb.early_stopping(60,verbose=False)])


def metrics(rows, scores):
    grouped=defaultdict(list)
    for r,s in zip(rows,scores):
        grouped[str(r["race_key"])].append((r,float(s)))
    races=top1=top3=exact12=exact123=0
    for items in grouped.values():
        if len(items)<3: continue
        actual=sorted(items,key=lambda x:int(x[0]["actual_finish_position"]))
        pred=sorted(items,key=lambda x:x[1],reverse=True)
        ao=[int(x[0]["horse_no"]) for x in actual[:3]]
        po=[int(x[0]["horse_no"]) for x in pred[:3]]
        races+=1
        top1+=int(po[0]==ao[0])
        top3+=int(ao[0] in set(po[:3]))
        exact12+=int(po[:2]==ao[:2])
        exact123+=int(po[:3]==ao[:3])
    return {"races":races,"winner_top1_hits":top1,"winner_top1_rate":top1/races,
            "winner_in_top3_hits":top3,"winner_in_top3_rate":top3/races,
            "exact_1_2_hits":exact12,"exact_1_2_rate":exact12/races,
            "exact_1_2_3_hits":exact123,"exact_1_2_3_rate":exact123/races}


def main():
    report={"status":"RUNNING","started_at_jst":now()}
    try:
        rows,features=load_rows()
        train,val,start=split_by_date(rows)
        maps=category_maps(train,features)
        xtr=matrix(train,features,maps); xval=matrix(val,features,maps)
        ytr=np.asarray([int(int(r["actual_finish_position"])==1) for r in train],dtype=np.int8)
        yval=np.asarray([int(int(r["actual_finish_position"])==1) for r in val],dtype=np.int8)

        binary=train_binary(xtr,ytr,xval,yval,features)
        base_m=metrics(val,binary.predict(xval))

        rtr=relevance(train); rval=relevance(val); gtr=groups(train); gval=groups(val)
        rank=train_ranker(xtr,rtr,gtr,xval,rval,gval,features)
        rank_m=metrics(val,rank.predict(xval))

        rxtr,rnames=add_race_relative(xtr,train,features)
        rxval,_=add_race_relative(xval,val,features)
        rrank=train_ranker(rxtr,rtr,gtr,rxval,rval,gval,rnames)
        rrank_m=metrics(val,rrank.predict(rxval))

        candidates={"race_ranker":(rank,rank_m,False,features),"race_ranker_relative":(rrank,rrank_m,True,rnames)}
        eligible=[]
        for name,(model,m,relative,names) in candidates.items():
            if m["winner_top1_rate"] >= base_m["winner_top1_rate"] + MIN_TOP1_GAIN and m["winner_in_top3_rate"] >= base_m["winner_in_top3_rate"]:
                eligible.append((m["winner_top1_rate"],m["winner_in_top3_rate"],name,model,relative,names))
        eligible.sort(reverse=True)
        adopted=None
        model_saved=False
        if eligible:
            _,_,adopted,chosen,relative,chosen_names=eligible[0]
            # Retrain the validated architecture on all available history.
            all_maps=category_maps(rows,features)
            xall=matrix(rows,features,all_maps)
            if relative:
                xall,chosen_names=add_race_relative(xall,rows,features)
            rall=relevance(rows); gall=groups(rows)
            import lightgbm as lgb
            ds=lgb.Dataset(xall,label=rall,group=gall,feature_name=chosen_names,free_raw_data=False)
            rounds=max(50,int(chosen.best_iteration or 300))
            final=lgb.train({"objective":"lambdarank","metric":"ndcg","ndcg_eval_at":[1,3],
                             "learning_rate":0.03,"num_leaves":31,"min_data_in_leaf":30,
                             "feature_fraction":0.8,"bagging_fraction":0.8,"bagging_freq":1,
                             "lambdarank_truncation_level":6,"seed":42,"verbosity":-1},
                            ds,num_boost_round=rounds)
            MODEL_DIR.mkdir(parents=True,exist_ok=True)
            final.save_model(str(MODEL_PATH))
            META_PATH.write_text(json.dumps({"model":"winner_ranker_stable","adopted":adopted,
                "trained_at_jst":now(),"validation_start":start,"feature_count":len(chosen_names),
                "relative_features":relative,"num_boost_round":rounds,
                "baseline":base_m,"validated":candidates[adopted][1]},ensure_ascii=False,indent=2),encoding="utf-8")
            model_saved=True

        report={"status":"SUCCESS","validation_start":start,"train_rows":len(train),"validation_rows":len(val),
                "feature_count":len(features),"adoption_rule":{"min_top1_gain":MIN_TOP1_GAIN,"top3_must_not_decline":True},
                "models":{"binary_baseline":base_m,"race_ranker":rank_m,"race_ranker_relative":rrank_m},
                "adopted":adopted,"model_saved":model_saved,"model_path":str(MODEL_PATH) if model_saved else None,
                "finished_at_jst":now()}
        OUT_DIR.mkdir(parents=True,exist_ok=True)
        REPORT_JSON.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        lines=["HorseRacingAI WINNER STRENGTHENING",f"status=SUCCESS validation_start={start}",
               f"baseline top1={base_m['winner_top1_rate']:.2%} top3={base_m['winner_in_top3_rate']:.2%}",
               f"ranker top1={rank_m['winner_top1_rate']:.2%} top3={rank_m['winner_in_top3_rate']:.2%}",
               f"ranker_relative top1={rrank_m['winner_top1_rate']:.2%} top3={rrank_m['winner_in_top3_rate']:.2%}",
               f"adopted={adopted or 'NONE'} model_saved={model_saved}"]
        REPORT_TXT.write_text("\n".join(lines)+"\n",encoding="utf-8")
        print("\n".join(lines),flush=True)
        return 0
    except Exception as exc:
        OUT_DIR.mkdir(parents=True,exist_ok=True)
        report={"status":"FAILED","error":repr(exc),"finished_at_jst":now()}
        REPORT_JSON.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
