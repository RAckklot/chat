#!/usr/bin/env bash
# A single genuine SOCI cold start; invoke only inside a disposable unshare namespace.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$1
out=$2
delay=$3
policy=$4
ip link set lo up
tc qdisc replace dev lo root handle 1: netem limit 10000 delay "${delay}ms" rate 100mbit
tc -s qdisc show dev lo > "$out/qdisc-before.txt"
# Preserve the real resolv.conf target across the private /run tmpfs. Ubuntu hosts
# commonly point /etc/resolv.conf into /run/systemd/resolve.
r1_resolver_target=$(readlink -f /etc/resolv.conf)
cp /etc/resolv.conf /tmp/r1-resolv-conf
mount -t tmpfs tmpfs /run
if [[ "$r1_resolver_target" == /run/* ]]; then
  mkdir -p "$(dirname "$r1_resolver_target")"
  cp /tmp/r1-resolv-conf "$r1_resolver_target"
fi
mkdir -p /run/r1
test -r /etc/resolv.conf
export XDG_RUNTIME_DIR=/run/r1
export XDG_CACHE_HOME=/run/r1
export HOME=/root
cat > "$out/containerd.toml" <<'TOML'
version = 3
disabled_plugins = ["io.containerd.grpc.v1.cri","io.containerd.cri.v1.runtime","io.containerd.cri.v1.images"]
[proxy_plugins.soci]
  type = "snapshot"
  address = "/run/r1/soci.sock"
TOML
cat > "$out/soci.toml" <<'TOML'
disable_verification = false
[background_fetch]
  disable = true
[prefetch]
  enable = false
[pull_modes.soci_v1]
  enable = true
[pull_modes.soci_v2]
  enable = false
[content_store]
  type = "soci"
TOML
./r1-client serve fixture > "$out/http.jsonl" 2>"$out/registry.log" &
soci-snapshotter-grpc -address /run/r1/soci.sock -config "$out/soci.toml" -log-level debug -root "$tmp/soci" > "$out/soci.log" 2>&1 &
soci_pid=$!
containerd -a /run/r1/ct.sock --root "$tmp/ct" --state /run/r1/state -c "$out/containerd.toml" > "$out/containerd.log" 2>&1 &
ct_pid=$!
ready=0
for ((i=0; i<150; ++i)); do
  if [[ -S /run/r1/soci.sock && -S /run/r1/ct.sock ]] && ctr -a /run/r1/ct.sock plugins ls 2>/dev/null | grep -Eq 'snapshotter\.v1[[:space:]]+soci[[:space:]].*[[:space:]]ok[[:space:]]*$'; then
    ready=1;break
  fi
  sleep .1
done
[[ $ready == 1 ]] || { echo 'runtime or SOCI plugin did not become ready';exit 2; }
python3 trial.py "$out" "$policy" "$soci_pid" "$ct_pid"
tc -s qdisc show dev lo > "$out/qdisc-after.txt"
