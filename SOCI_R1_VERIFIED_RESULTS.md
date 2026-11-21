# SOCI R1 — real FUSE cold-start pilot (21 November 2026)

**Execution: COMPLETE. Scientific outcome: startup-time superiority NOT ESTABLISHED.**

[Successful GitHub Actions run and logs](https://github.com/RAckklot/chat/actions/runs/44947609615) — 30 completed randomized cold starts plus one independently gated single-startup validation. Download the run's `soci-r1-native-evidence` artifact before it expires (14-day retention). The self-contained experimental workflow and scripts are on branch [soci-r1-fuse-ci](https://github.com/RAckklot/chat/tree/soci-r1-fuse-ci/soci-r1).

## Setup and validity

- GitHub-hosted Ubuntu 24.04, Linux x86-64, root-owned temporary containerd+SOCI state per trial; FUSE and `tc netem` confirmed.
- Official SOCI snapshotter **v0.16.1**, containerd **v2.2.9**, pinned digest `sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4` for the Linux/amd64 `python:3.12-slim-bookworm` image.
- Genuine lazy SOCI: **two FUSE mounts per measured trial**, **20 successful HTTP 206 range responses per trial**. No overlayfs-only fallback accepted.
- Clock starts before the explicit image pull; stops when the running container prints `42` from `SELECT 6*7` against in-memory SQLite. All 30 trials satisfy the acceptance checks.
- Five randomized paired blocks; low = 1ms one-way loopback latency, higher = 20ms; both at 100 Mbit/s; warm local registry page cache but fresh SOCI and containerd process/storage roots.
- Complete rebuilt index footprint: stock **2,040,737 bytes**; uniform and two-cut **2,106,465 bytes** each. The rebuilt zTOC encodings are **not binary-identical** to the archived R0 artifacts; the stock and two-cut **uncompressed checkpoint positions** match the frozen R0 schedules. These are regenerated, equal-budget experiment indexes, not verbatim R0 blobs.

## Actual median startup time (n=5 each cell; lower is better)

| Policy | Low latency | Higher latency |
| --- | ---: | ---: |
| Default SOCI | 13,468.800 ms | 15,278.397 ms |
| Equal-budget uniform | 13,489.432 ms | **15,110.104 ms** |
| Frozen two-cut optimization | **13,405.057 ms** | 15,624.284 ms |

Unpaired median gaps: two-cut wins by 63.743ms vs stock and 84.375ms vs uniform at 1ms, but **loses by 345.887ms vs stock and 514.180ms vs uniform** at 20ms.

The predeclared block-paired median advantages (baseline minus two-cut) are **+124.853ms / +71.972ms** at low latency (stock/uniform) and **+30.493ms / +53.769ms** at higher latency. Those small positive paired medians should **not** be mistaken for reliable acceleration: individual differences switch sign, higher-latency arithmetic mean against uniform is negative, and the unpaired medians tell a contrary story.

Median compressed layer data per startup:

| Policy | Low latency | Higher latency | HTTP 206 ranges |
| --- | ---: | ---: | ---: |
| Default SOCI | 32.684 MB | 34.057 MB | 20 |
| Equal-budget uniform | 33.825 MB | 31.151 MB | 20 |
| Two-cut | 32.605 MB | 34.283 MB | 20 |

These bytes represent actual container-read demand in this SQLite startup workload, **not** the five-import R0 trace. The original R0 12.35% payload advantage on selected imports does not transfer as a 12.35% startup speedup here.

## Decision and limitations

**Technical infrastructure PASS. Performance superiority UNPROVEN; do not escalate to the three-image, 30-pair-per-condition R2 benchmark on this evidence.** No comparison approaches the original dual threshold of **10% and 250ms**, and n=5 paired blocks is insufficient for a two-sided nonparametric 95% median-effect interval. The original full research GO remains a *hypothesis worth testing*, not a proven breakthrough or commercial advantage.

The equal-budget uniform spacing was selected using the R0 payload oracle, not a newly optimized startup-time oracle; even this comparator was not convincingly beaten. Only one Python image family and one SQLite first-operation endpoint were evaluated. The measured overhead includes image pulling and container startup and the finite-run variance, not a general scientific novelty or commercial claim.

**Recommended next action:** Archive the failure to demonstrate advantage as a valuable research result. Do not invest more in checkpoint optimizer complexity without a workload-specific pilot that first shows an unmistakable, repeatable, application-level speedup. The raw `ci-results/raw.jsonl`, per-trial `measurement.json`, HTTP and daemon logs, randomized plan, and `decision.json` are retained in the linked Actions artifact.
