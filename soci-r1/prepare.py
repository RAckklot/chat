#!/usr/bin/env python3
"""Rebuild the exact digest-pinned SOCI R1 image fixture without uploaded binary blobs."""
import ctypes as ct
import gzip
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets"
FIXTURE = BASE / "fixture"
PLATFORM = "sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4"
MEDIA = "application/vnd.oci.image.manifest.v1+json"
V1 = "application/vnd.amazon.soci.index.v1+json"
PREFIX = "/v2/r1/python/"
REFINED_STARTS = [0,4210402,8431335,12749569,16949250,18644031,21151098,25417244,29623978,29942862,33863289,38139673]
EXPECTED = {
    "base": ("8043029dc04132bfe51a3fb54697a734bfd3376ba32c7351e1b4b288f2e85af1",1373040),
    "stock": ("60c3cc2ac2b45033033d06460b6533a56602fb86c9126552ccf0a824bcfce77a",689808),
    "uniform": ("d1fbe0ab51ee71d5e0fc355d48a6f773dfabaf60b6890c7cc564fa3544c568d3",755536),
    "two": ("c7267d5662c9645e6906a5966c10a43ae38aeecc75edc15bac38709c06e39cac",755536)
}
def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()
def desc(data, kind):
    return {"digest":digest(data),"size":len(data),"mediaType":kind}
def encode(obj):
    return json.dumps(obj,separators=(",",":")).encode()
def download():
    ASSETS.mkdir(exist_ok=True)
    FIXTURE.mkdir(exist_ok=True)
    with urlopen("https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull",timeout=30) as r:
        token=json.load(r)["token"]
    def fetch(kind, dg):
        dst=FIXTURE/dg
        if dst.exists() and digest(dst.read_bytes())==dg:
            return dst.read_bytes()
        req=Request("https://registry-1.docker.io/v2/library/python/"+kind+"/"+dg,
                    headers={"Authorization":"Bearer "+token,"Accept":MEDIA})
        with urlopen(req, timeout=150) as r: data=r.read()
        if digest(data)!=dg: raise ValueError("original OCI digest mismatch: "+dg)
        dst.write_bytes(data)
        return data
    manifest=fetch("manifests",PLATFORM)
    image=json.loads(manifest)
    (ASSETS/"platform.json").write_bytes(manifest)
    for i,item in enumerate(image["layers"]):
        dg=item["digest"]
        fetch("blobs",dg)
        link=ASSETS/f"layer{i}.gz"
        if link.exists() or link.is_symlink(): link.unlink()
        link.symlink_to(Path("../fixture")/dg)
    cfg=image["config"]
    (ASSETS/"config.json").write_bytes(fetch("blobs",cfg["digest"]))
    print("PASS: pinned image manifest, config and four original layers",flush=True)

def make_nested():
    lib=ct.CDLL(str(BASE/"libzinfo.so"))
    ptr=ct.c_void_p
    fun=lib.generate_zinfo_from_file
    fun.restype=ct.c_int
    fun.argtypes=[ct.c_char_p,ct.c_int64,ct.POINTER(ptr)]
    pack=lib.zinfo_to_blob
    pack.restype=ct.c_int
    pack.argtypes=[ptr,ptr]
    free=lib.free_zinfo
    free.argtypes=[ptr]
    idx=ptr()
    n=fun(str(ASSETS/"layer2.gz").encode(),0,ct.byref(idx))
    if n<=0: raise RuntimeError("upstream DEFLATE checkpoint extraction failed")
    size=12+n*32785
    b=ct.create_string_buffer(size)
    written=pack(idx,b)
    free(idx)
    if written!=size: raise RuntimeError("unexpected upstream checkpoint blob length")
    records=[b.raw[i:i+32785] for i in range(12,size,32785)]
    layer=(ASSETS/"layer2.gz").read_bytes()
    plain=gzip.decompress(layer)
    starts={}
    for record in records:
        c,u,bits=struct.unpack_from("<qqB",record)
        if u<len(plain): starts[u]=(record,c,bits)
    if len(starts)!=666: raise RuntimeError("changed DEFLATE legal checkpoint count")
    if not all(x in starts for x in REFINED_STARTS): raise RuntimeError("frozen checkpoints missing")
    chosen=[starts[x] for x in REFINED_STARTS]
    (BASE/"nested.zinfo").write_bytes(struct.pack("<iq",len(chosen)-1,4<<20)+b"".join(p[0] for p in chosen))
    boundaries=[x[1] for x in chosen]+[len(layer)]
    digests=[]
    for i,(_,c,bits) in enumerate(chosen):
        lo=c-(bits!=0)
        hi=boundaries[i+1]
        digests.append(digest(layer[lo:hi]))
    (BASE/"nested-digests.json").write_text(json.dumps(digests))
    if len(chosen)!=12: raise RuntimeError("wrong number of frozen checkpoints")
    print("PASS: reconstituted exact 12 frozen checkpoints from upstream gzip records",flush=True)

def assemble():
    image=json.loads((ASSETS/"platform.json").read_text())
    manifest=(ASSETS/"platform.json").read_bytes()
    routes={}
    def register(data, expected, kind):
        if digest(data)!=expected: raise ValueError("fixture integrity: "+expected)
        dst=FIXTURE/expected
        if not dst.exists(): dst.write_bytes(data)
        routes[PREFIX+kind+"/"+expected]=expected
    register(manifest,PLATFORM,"manifests")
    register((ASSETS/"config.json").read_bytes(),image["config"]["digest"],"blobs")
    for layer in image["layers"]:
        dg=layer["digest"]
        if not (FIXTURE/dg).exists(): raise ValueError("missing original blob "+dg)
        routes[PREFIX+"blobs/"+dg]=dg
    empty=desc(b"{}",V1)
    register(b"{}",empty["digest"],"blobs")
    modes={}
    for label,step in [("stock",4194304),("uniform",3678459),("two",4194304)]:
        layers=[]
        for i,name,span in [(0,"base",4194304),(2,label,step)]:
            data=(ASSETS/(name+".ztoc")).read_bytes()
            expected,sz=EXPECTED[name]
            if hashlib.sha256(data).hexdigest()!=expected or len(data)!=sz:
                raise ValueError("R0 frozen "+name+" index mismatch")
            d=desc(data,"application/octet-stream")
            layer=image["layers"][i]
            d["annotations"]={
                "com.amazon.soci.image-layer-mediaType":layer["mediaType"],
                "com.amazon.soci.image-layer-digest":layer["digest"],
                "com.amazon.soci.span-size":str(span)}
            register(data,d["digest"],"blobs")
            layers.append(d)
        raw=encode({"schemaVersion":2,"mediaType":MEDIA,"config":empty,"layers":layers,
                   "subject":desc(manifest,MEDIA),
                   "annotations":{"com.amazon.soci.build-tool-identifier":"soci-span-study"}})
        dg=digest(raw)
        register(raw,dg,"manifests")
        register(raw,dg,"blobs")
        modes[label]={"index":dg,"target_ztoc":layers[1]["digest"],"base_ztoc":layers[0]["digest"],
                      "target_bytes":layers[1]["size"],
                      "complete_index_bytes":len(raw)+sum(x["size"] for x in layers)+empty["size"]}
    if modes["uniform"]["complete_index_bytes"]!=modes["two"]["complete_index_bytes"]:
        raise RuntimeError("equal-index-budget assertion failed")
    (FIXTURE/"routes.json").write_text(json.dumps(routes,indent=2))
    (FIXTURE/"modes.json").write_text(json.dumps(modes,indent=2))
    print(json.dumps(modes,indent=2),flush=True)

if __name__=="__main__":
    {"download":download,"nested":make_nested,"assemble":assemble}[sys.argv[1]]()
