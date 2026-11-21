package main

import (
  "context"
  "crypto/sha256"
  "encoding/hex"
  "encoding/json"
  "fmt"
  "io"
  "net/http"
  "os"
  "path/filepath"
  "strings"
  "sync"
  "time"

  "github.com/awslabs/soci-snapshotter/fs/source"
  "github.com/awslabs/soci-snapshotter/ztoc"
  "github.com/awslabs/soci-snapshotter/ztoc/compression"
  ct "github.com/containerd/containerd/v2/client"
  "github.com/containerd/containerd/v2/core/remotes/docker"
  "github.com/containerd/containerd/v2/pkg/namespaces"
  snap "github.com/containerd/containerd/v2/pkg/snapshotters"
  "github.com/opencontainers/go-digest"
)

type loggedResponse struct {http.ResponseWriter; bytes int; status int}
func (w *loggedResponse) WriteHeader(status int) {w.status=status;w.ResponseWriter.WriteHeader(status)}
func (w *loggedResponse) Write(b []byte)(int,error){n,e:=w.ResponseWriter.Write(b);w.bytes+=n;return n,e}

func serve(dir string) error {
  paths:=map[string]string{}
  f,e:=os.Open(filepath.Join(dir,"routes.json"));if e!=nil{return e}
  defer f.Close()
  if e=json.NewDecoder(f).Decode(&paths);e!=nil{return e}
  var mu sync.Mutex
  enc:=json.NewEncoder(os.Stdout)
  return http.ListenAndServe("127.0.0.1:5087",http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
    if r.URL.Path=="/v2/"{w.WriteHeader(200);return}
    start:=time.Now().UnixNano()
    meter:=&loggedResponse{ResponseWriter:w,status:200}
    defer func(){mu.Lock();defer mu.Unlock();_ = enc.Encode(map[string]any{
      "start_ns":start,"end_ns":time.Now().UnixNano(),"method":r.Method,"path":r.URL.Path,
      "range":r.Header.Get("Range"),"body_bytes":meter.bytes,"status":meter.status})}()
    name,ok:=paths[r.URL.Path];if !ok{http.NotFound(meter,r);return}
    raw,e:=os.Open(filepath.Join(dir,name));if e!=nil{http.Error(meter,e.Error(),500);return}
    defer raw.Close()
    dg:=filepath.Base(r.URL.Path)
    meter.Header().Set("Docker-Content-Digest",dg)
    if strings.Contains(r.URL.Path,"/manifests/"){
      meter.Header().Set("Content-Type","application/vnd.oci.image.manifest.v1+json")
    }else{meter.Header().Set("Content-Type","application/octet-stream")}
    http.ServeContent(meter,r,dg,time.Time{},raw)
  }))
}
func pull(sock,ref,idx string)error{
  c,e:=ct.New(sock);if e!=nil{return e};defer c.Close()
  ctx:=namespaces.WithNamespace(context.Background(),"r1")
  resolver:=docker.NewResolver(docker.ResolverOptions{Hosts:docker.ConfigureDefaultRegistries(docker.WithPlainHTTP(docker.MatchLocalhost))})
  img,e:=c.Pull(ctx,ref,ct.WithResolver(resolver),ct.WithPullUnpack,ct.WithPullSnapshotter("soci"),
    ct.WithImageHandlerWrapper(source.AppendDefaultLabelsHandlerWrapper(idx,snap.AppendInfoHandlerWrapper(ref))))
  if e!=nil{return e}
  if !strings.HasSuffix(ref,"@"+img.Target().Digest.String()){return fmt.Errorf("platform digest mismatch")}
  return nil
}

type check struct{hash string; bytes int}
var expected=map[string]check{
  "base":{"8043029dc04132bfe51a3fb54697a734bfd3376ba32c7351e1b4b288f2e85af1",1373040},
  "stock":{"60c3cc2ac2b45033033d06460b6533a56602fb86c9126552ccf0a824bcfce77a",689808},
  "uniform":{"d1fbe0ab51ee71d5e0fc355d48a6f773dfabaf60b6890c7cc564fa3544c568d3",755536},
  "two":{"c7267d5662c9645e6906a5966c10a43ae38aeecc75edc15bac38709c06e39cac",755536},
}
func build(root string)error{
  var digs []digest.Digest
  b,e:=os.ReadFile(filepath.Join(root,"nested-digests.json"));if e!=nil{return e}
  if e=json.Unmarshal(b,&digs);e!=nil{return e}
  zinfo,e:=os.ReadFile(filepath.Join(root,"nested.zinfo"));if e!=nil{return e}
  for _,v:=range []struct{name string;layer int;span int64}{
    {"base",0,4194304},{"stock",2,4194304},{"uniform",2,3678459},{"two",2,4194304},
  }{
    file:=filepath.Join(root,"assets",fmt.Sprintf("layer%d.gz",v.layer))
    z,e:=ztoc.NewBuilder("soci-span-study").BuildZtoc(file,v.span)
    if e!=nil{return fmt.Errorf("%s build: %w",v.name,e)}
    if v.name=="two"{
      z.Checkpoints=zinfo
      z.MaxSpanID=compression.SpanID(len(digs)-1)
      z.SpanDigests=digs
    }
    r,_,e:=ztoc.Marshal(z);if e!=nil{return e}
    content,e:=io.ReadAll(r);if e!=nil{return e}
    want:=expected[v.name];sum:=sha256.Sum256(content)
    actual:=hex.EncodeToString(sum[:])
    if actual!=want.hash||len(content)!=want.bytes{
      fmt.Printf("REBUILT_NOT_BYTE_IDENTICAL: %s generated %s (%d bytes); archived %s (%d bytes)\n",
        v.name,actual,len(content),want.hash,want.bytes)
    }else{
      fmt.Printf("ARCHIVE_BYTE_IDENTICAL: %s %s\n",v.name,actual)
    }
    if v.name=="stock"||v.name=="two"{
      expectedOffsets:=[]int64{0,4210402,8431335,12749569,16949250,21151098,25417244,29623978,33863289,38139673}
      if v.name=="two"{
        expectedOffsets=[]int64{0,4210402,8431335,12749569,16949250,18644031,21151098,25417244,29623978,29942862,33863289,38139673}
      }
      zi,err:=z.Zinfo();if err!=nil{return err}
      if int(z.MaxSpanID)+1!=len(expectedOffsets){zi.Close();return fmt.Errorf("%s unexpected span count",v.name)}
      for i,off:=range expectedOffsets{
        actualOff:=int64(zi.StartUncompressedOffset(compression.SpanID(i)))
        if actualOff!=off{zi.Close();return fmt.Errorf("%s frozen checkpoint %d moved: got %d want %d",v.name,i,actualOff,off)}
      }
      zi.Close()
      fmt.Printf("PASS: %s frozen uncompressed checkpoint positions match R0\n",v.name)
    }
    if e=os.WriteFile(filepath.Join(root,"assets",v.name+".ztoc"),content,0644);e!=nil{return e}
  }
  return nil
}
func main(){
  var e error
  switch {
    case len(os.Args)==3 && os.Args[1]=="serve": e=serve(os.Args[2])
    case len(os.Args)==5 && os.Args[1]=="pull": e=pull(os.Args[2],os.Args[3],os.Args[4])
    case len(os.Args)==3 && os.Args[1]=="build-indexes": e=build(os.Args[2])
    default: fmt.Fprintln(os.Stderr,"usage: r1-client serve DIR | pull SOCK IMAGE INDEX | build-indexes ROOT");os.Exit(2)
  }
  if e!=nil{fmt.Fprintln(os.Stderr,e);os.Exit(1)}
}
