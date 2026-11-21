#!/usr/bin/env python3
"""Frozen randomized paired pilot: one preflight startup, then 30 cold starts."""
import json
from pathlib import Path
import random
import statistics as st
import subprocess as sp
import sys
import tempfile
import time

BASE=Path(__file__).resolve().parent
OUT=BASE/"ci-results"
SEED=20261121
def schedule():
    rng=random.Random(SEED)
    result=[]
    for block in range(1,6):
        conditions=[("low",1),("higher",20)]
        rng.shuffle(conditions)
        for condition,delay in conditions:
            policies=["stock","uniform","two"]
            rng.shuffle(policies)
            result.extend(dict(block=block,condition=condition,delay_ms=delay,policy=p) for p in policies)
    return result
def run_trial(i,item):
    trial=OUT/f'{i:02}-{item["condition"]}-{item["policy"]}'
    trial.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="r1-",dir="/var/tmp") as tmp:
        cmd=["unshare","--mount","--net","--pid","--fork","--kill-child","--mount-proc",
             "--propagation","private","bash","trial.sh",tmp,str(trial.resolve()),
             str(item["delay_ms"]),item["policy"]]
        (trial/"launch.json").write_text(json.dumps(cmd,indent=2))
        with (trial/"trial.log").open("w") as f:
            proc=sp.run(cmd,stdout=f,stderr=sp.STDOUT,timeout=300)
        if proc.returncode:
            raise RuntimeError(f"trial {i} ({item}) failed (exit {proc.returncode}); see {trial}/trial.log")
    return dict(item,run=i,**json.loads((trial/"measurement.json").read_text()))
def summary(rows):
    result={}
    for condition in ("low","higher"):
        part=[x for x in rows if x["condition"]==condition]
        times={p:{x["block"]:x["useful_ms"] for x in part if x["policy"]==p}
               for p in ("stock","uniform","two")}
        comps={}
        for policy in ("stock","uniform"):
            delta=[times[policy][b]-times["two"][b] for b in sorted(times[policy])]
            comps[policy]={"paired_ms_baseline_minus_two":delta,
                            "median_ms":st.median(delta),"min_ms":min(delta),"max_ms":max(delta)}
        result[condition]={"comparisons":comps,"median_ms_by_policy":{p:st.median(v.values()) for p,v in times.items()}}
    positive=all(comp["median_ms"]>0 for v in result.values() for comp in v["comparisons"].values())
    return {"decision":"pilot direction positive; full R1 gate UNTESTED" if positive else "STOP before further expansion",
            "conditions":result,"confidence":"five paired runs per cell: no two-sided nonparametric 95% median interval"}
def main():
    OUT.mkdir(exist_ok=True)
    plan=schedule()
    (OUT/"plan.json").write_text(json.dumps({"seed":SEED,"runs":plan},indent=2))
    with (OUT/"versions.txt").open("w") as f:
        for cmd in (["uname","-a"],["containerd","--version"],["ctr","--version"],
                    ["soci-snapshotter-grpc","--version"],["runc","--version"],["tc","-V"]):
            sp.run(cmd,stdout=f,stderr=sp.STDOUT)
    try:
        if sys.argv[1]=="--single":
            row=run_trial(0,dict(block=0,condition="low",delay_ms=1,policy="stock"))
            (OUT/"single-startup.json").write_text(json.dumps(row,indent=2))
            print("GENUINE_SOCI_COLD_START_PASS",json.dumps({k:row[k] for k in ("useful_ms","compressed_layer_bytes","http_get_ranges","remote_mounts")}),flush=True)
        elif sys.argv[1]=="--full":
            rows=[]
            for i,item in enumerate(plan,1):
                row=run_trial(i,item)
                rows.append(row)
                with (OUT/"raw.jsonl").open("a") as f:f.write(json.dumps(row)+"\n")
                print("R1",i,item["condition"],item["policy"],row["useful_ms"],"ms",flush=True)
            (OUT/"decision.json").write_text(json.dumps(summary(rows),indent=2))
        else: raise ValueError("use --single or --full")
    except Exception as e:
        (OUT/"BLOCKER.txt").write_text(str(e)+"\n")
        raise
if __name__=="__main__":main()
