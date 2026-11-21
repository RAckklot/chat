#!/usr/bin/env python3
"""Measure wall time from pinned image pull to first successful SQL result."""
import json
import os
from pathlib import Path
import subprocess as sp
import sys
import time
from urllib.request import urlopen
from prepare import BASE, PLATFORM

out=Path(sys.argv[1])
policy=sys.argv[2]
pids=[int(v) for v in sys.argv[3:]]
mode=json.loads((BASE/"fixture/modes.json").read_text())[policy]
target=json.loads((BASE/"assets/platform.json").read_text())["layers"][2]["digest"]
ref="127.0.0.1:5087/r1/python@"+PLATFORM
sock="/run/r1/ct.sock"
sql="import sqlite3,time; v=sqlite3.connect(':memory:').execute('select 6*7').fetchone()[0]; print('R1',v,time.process_time_ns(),flush=True)"
pull=["./r1-client","pull",sock,ref,mode["index"]]
run=["ctr","-a",sock,"-n","r1","run","--rm","--snapshotter","soci","--net-host",ref,"pilot",
     "/usr/local/bin/python3","-I","-B","-S","-u","-c",sql]
def cpu_ns(pid):
    stat=Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split()
    return (int(stat[11])+int(stat[12]))*1_000_000_000//os.sysconf("SC_CLK_TCK")
def mount_count():
    return sum("/soci/" in line and " - fuse" in line for line in Path("/proc/self/mountinfo").read_text().splitlines())
def measure():
    with urlopen("http://127.0.0.1:5087/v2/",timeout=3) as r: assert r.status==200
    for path in (BASE/"fixture").glob("sha256:*"):
        with path.open("rb") as f:
            while f.read(1<<20): pass
    (out/"commands.json").write_text(json.dumps([pull,run],indent=2))
    cpu0=[cpu_ns(pid) for pid in pids]
    wall0=time.time_ns()
    t0=time.monotonic_ns()
    with (out/"client.stderr").open("wb") as err:
        sp.run(pull,check=True,stdout=err,stderr=err,timeout=150)
        pulled=time.monotonic_ns()
        fuse_after_pull=mount_count()
        proc=sp.Popen(run,stdout=sp.PIPE,stderr=err)
        line=proc.stdout.readline().decode().strip().split()
        t1=time.monotonic_ns()
        wall1=time.time_ns()
        cpu1=[cpu_ns(pid) for pid in pids]
        fuse_after_result=mount_count()
        if line[:2]!=["R1","42"] or len(line)!=3: raise RuntimeError(f"first useful operation failed: {line!r}")
        if proc.wait(timeout=30): raise RuntimeError("ctr failed after SQL result")
    (out/"mountinfo").write_text(Path("/proc/self/mountinfo").read_text())
    fuse_after_stop=mount_count()
    rows=[json.loads(s) for s in (out/"http.jsonl").read_text().splitlines()]
    active=[r for r in rows if wall0<=r["start_ns"]<=wall1]
    if any(r["end_ns"]>wall1 for r in active):
        raise RuntimeError("HTTP transfer crosses result endpoint; bytes cannot be attributed")
    gets=[r for r in active if r["method"]=="GET" and 200<=r["status"]<300]
    hashes={r["path"].split("/")[-1] for r in gets}
    expected={mode[k] for k in ("index","base_ztoc","target_ztoc")}
    if not expected<=hashes:
        raise RuntimeError("selected index or both zTOCs not served")
    if not any(r["status"]==206 and r["path"].endswith(target) and r["range"] for r in gets):
        raise RuntimeError("no target-layer HTTP 206 demand range")
    fuse=max(fuse_after_pull,fuse_after_result,fuse_after_stop)
    if fuse<2:
        raise RuntimeError("two actual FUSE layer mounts were not confirmed")
    blob_digests={d["digest"] for d in json.loads((BASE/"assets/platform.json").read_text())["layers"]}
    return dict(start_ns=wall0,marker_ns=wall1,useful_ms=(t1-t0)/1e6,pull_ms=(pulled-t0)/1e6,
      http_body_bytes=sum(r["body_bytes"] for r in active),
      compressed_layer_bytes=sum(r["body_bytes"] for r in gets if r["path"].split("/")[-1] in blob_digests),
      http_get_ranges=sum(r["method"]=="GET" and bool(r["range"]) for r in active),
      successful_206=sum(r["status"]==206 for r in gets),http_requests=len(active),
      soci_cpu_ms=(cpu1[0]-cpu0[0])/1e6,containerd_cpu_ms=(cpu1[1]-cpu0[1])/1e6,
      app_cpu_ms=int(line[2])/1e6,isolated_decompression_cpu_ms=None,
      client_cache="new containerd and SOCI daemon roots",registry_cache="warm",
      background_fetch=False,policy=policy,remote_mounts=fuse,
      mounts_after_pull=fuse_after_pull,mounts_after_result=fuse_after_result,mounts_after_stop=fuse_after_stop,
      platform=PLATFORM,**mode)
if __name__=="__main__":
    (out/"measurement.json").write_text(json.dumps(measure(),indent=2))
