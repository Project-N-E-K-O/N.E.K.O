# Runtime lifecycle soak after PRs #2350/#2351/#2353/#2354/#2355

## Result

A two-hour, 204-cycle lifecycle soak on commit
`715cab7fa8143aaf6cce92b8645e8cbdebf7e467` did **not** find a retained
RapidOCR owner, child process, thread, handle, growing ONNX mapping count, or
monotonically unrecoverable working set that establishes a product leak.

The run did find two distinct effects:

1. Repeated ONNX/browser construction raises the process allocator/working-set
   high-water mark in steps. Large 20-50 MiB drops also occur later, while
   observed RapidOCR owners are already zero. Treat the elevated RSS/USS as
   native allocator retention, not proof of a live session leak.
2. The merged audio lifecycle had one deterministic integration bug:
   `AudioProcessor.set_enabled(False)` closed RNNoise but retained the fixed
   frame buffer, contradicting the lifecycle regression. Releasing it without
   a matching enable path would then break processing. The branch now releases
   the buffer on disable and symmetrically recreates it on enable.

No other product code was changed.

## Scope and privacy

Every workload used explicit synthetic input:

- audio: deterministic generated PCM16 ramp;
- embedding: a fixed synthetic sentence;
- RapidOCR: a generated blank 640x360 image;
- browser-use: local headless Playwright startup/close without an LLM task;
- plugin reload: the repository's synthetic dynamic-entry fixture process;
- chat: **skipped** because no backend with verified isolated storage was
  available. The earlier baseline documents that an unisolated synthetic chat
  can persist into the selected character store, so this soak did not risk it.

The aggregate JSON records process/resource counts, status codes, and exception
types only. It does not record prompts, responses, command lines, environment
variables, microphone data, or API payloads.

## Method

The probe extends `scripts/runtime_memory_baseline.py` and adds
`scripts/runtime_memory_soak.py`. Each cycle performs:

1. `AudioProcessor` create, synthetic processing, disable/enable twice, close.
2. `EmbeddingService` load, one synthetic inference, close.
3. `RapidOcrBackend` acquire, one blank-image inference, close/release owner.
4. `BrowserUseAdapter` and Playwright start, close, verify descendants exit.
5. Synthetic plugin host start, dynamic entry trigger, shutdown.
6. GC plus a released checkpoint after every feature and after the whole cycle.

Samples include the full process tree and aggregate:

- RSS and USS;
- process, thread, and Windows handle counts;
- Chromium child count;
- ONNX-related memory map count and resident bytes;
- embedding session/tokenizer references;
- RapidOCR cache entries and owner counts;
- tracemalloc current/peak and top final growth sites.

The formal command ran through `uv run` with Python 3.11.13 from the existing
main-checkout environment and the model files from that checkout. Imports still
resolved from this worktree. The run lasted 7,203.426 seconds and exited zero.

## Formal two-hour run

All five executable workloads completed in all 204 cycles. Every audio result
recorded `native_denoiser=true`, and every plugin result recorded
`triggered=true`. Chat was explicitly reported as skipped in all 204 cycles.

| Released checkpoint | USS average (MiB) | USS min-max (MiB) | traced current average (MiB) | handles min-max | threads min-max |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cycles 1-10 | 492.493 | 436.539-534.211 | 96.607 | 836-841 | 63-71 |
| Cycles 41-50 | 608.143 | 604.727-610.977 | 98.546 | 837-839 | 63-63 |
| Cycles 91-100 | 623.264 | 620.613-624.953 | 100.865 | 837-843 | 63-65 |
| Cycles 141-150 | 645.685 | 625.219-650.981 | 103.161 | 841-843 | 64-65 |
| Cycles 195-204 | 657.287 | 656.496-657.813 | 105.592 | 841-843 | 64-65 |

The first/last released samples were 436.539/657.391 MiB USS and
500.488/720.719 MiB RSS. The maximum released USS was 658.281 MiB. A naive
whole-run regression is about +54.1 MiB USS/hour, but it is not a leak rate:
the series contains cold imports, allocator warm-up steps, diagnostic result
retention, and multiple large recoveries.

Examples of real recovery:

- cycle 50 to 51: 610.977 -> 588.605 MiB USS (-22.372 MiB);
- cycle 142 to 143: 650.680 -> 625.219 MiB USS (-25.461 MiB).

Released resource invariants across all 204 cycles:

- RapidOCR cache entries: min/max 0/0;
- RapidOCR owners: min/max 0/0;
- Chromium children: min/max 0/0;
- ONNX maps: min/max 2/2;
- ONNX mapped RSS: min/max 16.430/16.430 MiB;
- threads: first/last 69/65, range 62-71;
- handles: first/last 839/843, range 835-843.

The formal run's embedding session/tokenizer counters are not used as release
evidence: that revision inspected only the application singleton while the
probe created direct `EmbeddingService` instances. The probe now weakly
registers direct services, so future active checkpoints observe their session
and tokenizer while released checkpoints can verify that the objects and
references disappeared.

The two stable ONNX mappings remain after the explicit embedding close and
RapidOCR owner release. They are DLL/model residency, not evidence that an
`InferenceSession` is still owned.

Tracemalloc current grew 96.312 -> 105.793 MiB (+9.481 MiB). This run retained
7.66 MiB of aggregate JSON in memory so it could atomically rewrite the report
after each cycle; several of the largest final growth sites are the sampler's
own retained rows. The soak script now drops per-PID rows by default (category
aggregates remain) and exposes `--retain-process-rows` only for investigations
that need every PID at every checkpoint.

## Controls

### Embedding owner instrumentation, post-review

A fresh one-cycle embedding control using the revised weak registry recorded
one service, one session, and one tokenizer at the active checkpoint, followed
by 0/0/0 at both the feature-released and all-released checkpoints. The ONNX
map count remained at two after release, directly separating released Python
owners from stable runtime mappings.

### OCR-only, fresh process

Thirty cycles completed in 114.676 seconds.

| Cycles | USS average (MiB) | USS min-max (MiB) | traced current average (MiB) |
| --- | ---: | ---: | ---: |
| 1-5 | 240.938 | 237.352-242.934 | 52.347 |
| 11-15 | 247.569 | 245.297-253.410 | 52.446 |
| 26-30 | 256.714 | 255.387-258.074 | 52.588 |

The last cycle was 255.570 MiB USS. Tracemalloc grew only 0.286 MiB, handles
grew by 2, threads ended unchanged at 39, maps stayed at 2/16.430 MiB, and all
RapidOCR cache/owner counts were zero after release. The final window is a
plateau with small recoveries rather than a fixed per-cycle loss.

### Audio + embedding + browser-use + plugin, no OCR

Twenty cycles completed in 149.676 seconds. Released USS ranged from 365.215 to
490.101 MiB and ended at 438.375 MiB. Cycle 19 to 20 alone recovered 51.726 MiB.
Tracemalloc grew 0.895 MiB; handles ended one lower and threads five lower.

Embedding close was the transition that sometimes caused a 45-53 MiB recovery,
while browser and plugin release returned to the same level. This independently
shows native allocator/working-set changes outside RapidOCR as well.

### Harness-only

Fifty measurement-only cycles completed in 6.060 seconds. USS grew
16.730 -> 19.633 MiB (+2.903 MiB), tracemalloc current grew 0.177 MiB, and
handles grew by 2. This is diagnostic self-retention and is not application
workload memory. The new default compact checkpoint format reduces this effect
for future 2-8 hour runs.

## Audio lifecycle fix

The merged regression test expected disabling noise reduction to release both
the native denoiser and its owned frame buffer. The fixed-buffer optimization
from #2354 instead only filled the buffer with zeroes, so the #2355 release
contract failed.

The minimal symmetric fix is:

- disable: close RNNoise, clear the reference, release the frame buffer;
- enable: initialize RNNoise and recreate the fixed 480-sample buffer before
  processing;
- preserve the existing AGC and pending-buffer reset semantics.

A regression now enables from the released state and processes one synthetic
480-sample frame, preventing a one-sided lifecycle fix.

## Validation

The focused merged-PR and diagnostic suite passes:

```text
377 passed, 5 pre-existing deprecation warnings in 7.44s
```

Coverage includes time-index batching, topic signals, audio memory and
lifecycle, embedding lifecycle, browser lifecycle, agent shutdown, RapidOCR,
vision, study OCR, and the soak analyzer.

The post-review lifecycle/measurement slice also passes 22 tests, including
unknown-metric preservation, direct embedding owner tracking, workload success
gates, and the isolated chat backend PID contract.

## Re-run

From a Python 3.11 environment:

```powershell
uv run python -m scripts.runtime_memory_soak `
  --output soak-2h.json `
  --duration-hours 2 `
  --cycle-pause 20 `
  --embedding-root data/embedding_models
```

`--duration-hours` accepts values up to 8. Chat remains disabled unless an
operator passes both `--chat-port` and `--chat-pid` for a separately started
backend whose storage isolation has already been verified. Chat checkpoints
sample that backend PID and its descendants rather than the soak driver.
