# Runtime memory baseline (2026-07-15)

This document is the reproducible **before** baseline for runtime-memory work in
Xiao8. Measurements were collected from commit
`5ca2f416db2e09b4628b395c51a108ead7fc13a4` (`origin/main` on 2026-07-15) with
`scripts/runtime_memory_baseline.py`.

## Scope and interpretation

- `psutil` samples the complete process tree every 250 ms. A checkpoint is the
  median of a three-second window after the transition has settled.
- RSS includes shared and memory-mapped resident pages. It is useful for peak
  pressure, but adding RSS across processes can double-count shared pages.
- USS is memory unique to a process and is the primary comparison metric here.
- `tracemalloc` runs inside the feature-scenario Python process with 10 frames.
  It cannot attach to an already running child process. `USS - traced current`
  is therefore only an attribution aid: it includes the interpreter, native
  extensions, allocator slack, mappings, and any untraced Python allocation.
- Stack runs use actual launcher, server, Electron, and Chromium processes.
  Scenario runs isolate one lazy transition in one Python process.
- Values are MiB. Except for the three-run development cold-start baseline,
  results are one run with multiple samples per checkpoint. They should be
  compared with the same scenario and host rather than treated as universal
  constants.

## Test environment

| Item | Value |
| --- | --- |
| OS | Windows build 26100 (`Windows-10-10.0.26100-SP0`) |
| CPU / memory | Intel Core Ultra 7 265KF, 20 logical CPUs, 63.625 GiB RAM |
| Python | 3.11.13, always launched through `uv run` |
| psutil / onnxruntime / NumPy | 7.2.2 / 1.25.0 / 1.26.4 |
| tokenizers / rapidocr-onnxruntime | 0.22.2 / 1.4.4 |
| browser-use / Playwright | 0.11.9 / 1.58.0 |
| Embedding model | `jinaai/jina-embeddings-v5-text-nano-retrieval`, revision `ac5d898c8d382b17167c33e5c8af644a3519b47d`, quantized ONNX profile, 256 dimensions |
| Embedding files | tokenizer 16.41 MiB, ONNX 0.13 MiB, external data 235.56 MiB |
| OCR models | PP-OCRv4 mobile detector 4.53 MiB, recognizer 10.35 MiB, classifier 0.56 MiB |
| Electron sample | Packaged N.E.K.O 0.8.3 attached to the measured backend; runtime reported Electron 41.7.1 |

The new worktree did not have enough free disk space to create a second full
environment. This run used `uv run --project <main-checkout> --no-sync` against
an existing Python 3.11.13 environment after verifying that the current and
main-checkout `uv.lock` files had the same SHA-256. Imports of Xiao8 code still
resolved from this worktree. A normal rerun should use `uv sync --locked
--group galgame` in the target worktree first.

## Before baseline

The split below is the primary hand-off for optimization tasks. `Python` does
not include the small `uv` wrapper, which is shown as part of the total.

| Checkpoint | Python RSS / USS | Electron main RSS / USS | Chromium children RSS / USS | Whole measured tree RSS / USS |
| --- | ---: | ---: | ---: | ---: |
| Development cold READY, three-server mode, median of 3 | 722.8 / 546.7 | - | - | 742.7 / 552.8 |
| Merged-server cold READY | 402.8 / 339.6 | - | - | 422.5 / 345.7 |
| First chat after session teardown, three-server mode | 810.3 / 630.3 | - | - | 830.0 / 636.4 |
| Embedding READY | 194.2 / 163.4 | - | - | 194.2 / 163.4 |
| OCR constructed and first blank-image inference complete | 308.2 / 271.0 | - | - | 308.2 / 271.0 |
| browser-use + headed Playwright started | 233.8 / 214.5 | - | 420.3 / 121.8 (7) | 654.1 / 336.3 |
| Packaged Electron attached to merged backend | 410.3 / 346.8 | 233.3 / 107.9 | 1165.0 / 611.6 (6) | 1951.0 / 1110.5 |

The last row also contains 122.6 / 38.1 MiB of packaged-app helpers
(OpenFang, PowerShell, and console host) and a 19.6 / 6.1 MiB `uv` wrapper.
The Electron subtotal (main plus Chromium children) is 1398.3 / 719.5 MiB.
The packaged binary was not rebuilt from the Xiao8 commit above, so use this
row for component attribution and distribution-shaped comparisons, not as a
source-to-binary regression gate.

### Cold start

Three-server mode (`NEKO_MERGED=0`) reached all three ports in 13.797 seconds
on the first cold-cache run and 7.182 / 6.863 seconds on two warm-cache repeats.
After an eight-second settle, its three whole-tree READY checkpoints were:

| Run | RSS | USS |
| --- | ---: | ---: |
| 1 | 749.3 | 559.1 |
| 2 | 742.7 | 552.8 |
| 3 | 741.8 | 552.0 |

The first run's Python services were launcher 65.8 / 33.1, memory server
126.7 / 88.1, main server 339.3 / 285.2, and agent server 193.7 / 146.2 MiB,
plus a 4.1 / 0.4 MiB Python stub. Merged mode (`NEKO_MERGED=1`) was 422.5 /
345.7 MiB for the whole tree. On this host, merging saved about 320.2 MiB RSS
and 207.1 MiB USS versus the three-run median.

### First chat

A fixed synthetic prompt completed in 1.954 seconds. The benchmark JSON retains
only message types, counts, timing, and status codes; it does not retain prompt
or response text.

| Transition | Whole tree RSS delta | Whole tree USS delta |
| --- | ---: | ---: |
| READY to first chat after session teardown | +88.2 | +84.4 |

The memory server accounted for +78.7 / +75.9 MiB of that change, and the main
server for +9.3 / +8.3 MiB. Agent and launcher changes were negligible. Thus
the first-turn lazy allocation is concentrated in the memory service. Finer
Python-versus-native attribution would require starting `tracemalloc` inside
that child service; an external sampler cannot provide it.

The original run sent `end_session` before this checkpoint, so the number is a
conservative after-teardown baseline rather than the retained memory of a live
text session. The probe now records `first_chat_complete_live` before sending
`end_session`. A replacement live-session number requires a rerun with verified
isolated storage; it was not repeated here because the launcher had already
ignored the attempted isolated root and persisted the synthetic turn.

The launcher overrode the attempted isolated storage root during this run and
used the selected existing character store. The synthetic turn was therefore
persisted by normal application behavior. No automatic cleanup was performed.
Startup also ran the application's ordinary memory-maintenance path. A rerun
that must be side-effect-free needs a launcher-supported, verified isolated
storage configuration before sending the chat request.

### Embedding READY

| Checkpoint | RSS | USS | traced current / peak | USS - traced current |
| --- | ---: | ---: | ---: | ---: |
| Minimal Python | 30.7 | 18.0 | 0.0 / 0.1 | 18.0 |
| Embedding imports | 106.5 | 91.0 | 23.9 / 24.0 | 67.1 |
| Model READY | 194.2 | 163.4 | 31.6 / 31.7 | 131.8 |
| First inference | 322.7 | 165.6 | 31.7 / 31.7 | 134.0 |

Imports to READY added +87.7 / +72.5 MiB RSS/USS, but only +7.8 MiB of
current traced allocations. READY to first inference added +128.6 MiB RSS but
only +2.2 MiB USS and almost no traced heap, consistent with file-backed/shared
model pages becoming resident rather than a large unique Python allocation.

### OCR enabled

| Checkpoint | RSS | USS | traced current / peak | USS - traced current |
| --- | ---: | ---: | ---: | ---: |
| Minimal Python | 31.0 | 18.3 | 0.0 / 0.1 | 18.2 |
| OCR imports | 201.9 | 184.0 | 40.8 / 40.8 | 143.3 |
| Runtime + first blank-image inference | 308.2 | 271.0 | 53.2 / 102.8 | 217.9 |

Imports to enabled runtime added +106.2 / +87.0 MiB RSS/USS. Current traced
heap grew by only +12.4 MiB, while the trace peak reached 102.8 MiB during
initialization. The persistent increase is primarily untraced/native runtime
state, with a material temporary Python allocation peak.

### browser-use and Playwright

The measured transition launches a real Chromium process in headed mode against
`about:blank` with no network navigation.

| Checkpoint | Python RSS / USS | Chromium RSS / USS | traced current / peak |
| --- | ---: | ---: | ---: |
| Minimal Python | 30.7 / 18.1 | - | 0.0 / 0.1 |
| browser-use imported | 187.6 / 171.1 | - | 35.8 / 35.9 |
| Playwright started | 233.8 / 214.5 | 420.3 / 121.8 (7) | 46.8 / 46.8 |

At Playwright READY, Python's `USS - traced current` was about 167.9 MiB. The
browser transition from imports added 165.2 MiB total USS: about 43.4 MiB in
Python and 121.8 MiB across Chromium. A headless comparison was also measured
at 668.2 / 350.4 MiB total with 434.4 / 136.0 MiB in Chromium; it is close
enough that headed/headless should be kept consistent within a regression run.

## Attribution and optimization order

1. **Electron renderers are the largest unique component in the measured full
   stack.** Six Chromium children used 611.6 MiB USS; Electron main added 107.9
   MiB. Renderer/window lifetime and count deserve measurement before broad
   Python object rewrites.
2. **Duplicated Python server state is the next clear structural cost.** Merged
   mode reduced the READY baseline by 207.1 MiB USS on this host.
3. **First-chat growth belongs mainly to the memory server.** Its +75.9 MiB USS
   explains 90% of the whole-tree first-turn USS delta.
4. **browser-use has both a heavy import footprint and a browser-process
   footprint.** Importing it added about 152.9 MiB Python USS from the minimal
   process; starting Chromium added another 165.2 MiB total USS.
5. **OCR and embedding READY are mostly not traced Python heap.** Persistent
   optimization should target ONNX Runtime sessions, model mappings, provider
   lifetime, and buffers. OCR initialization also has a temporary traced peak.
6. **RSS-only conclusions can be misleading.** Embedding's first inference is
   the clearest example: +128.6 MiB RSS with only +2.2 MiB USS.

## Reproduction

Create a clean environment and make sure the required local models and browser
binary are already installed. All Python entry points must remain under `uv
run`.

```powershell
uv sync --locked --group galgame

uv run python scripts/runtime_memory_baseline.py --output embedding.json scenario embedding --embedding-root data/embedding_models
uv run python scripts/runtime_memory_baseline.py --output ocr.json scenario ocr
uv run python scripts/runtime_memory_baseline.py --output browser-headed.json scenario browser-use --headed

uv run python scripts/runtime_memory_baseline.py --output cold-multi.json stack `
  --backend-command 'uv|run|python|launcher.py' `
  --env NEKO_MERGED=0 --settle 8

uv run python scripts/runtime_memory_baseline.py --output cold-merged.json stack `
  --backend-command 'uv|run|python|launcher.py' `
  --env NEKO_MERGED=1 --settle 8
```

Electron can be added with `--electron-command '<absolute-path-to-exe>'` and
`--electron-cwd '<distribution-directory>'`. The synthetic first-chat probe is
enabled with `--synthetic-chat --chat-character '<character>'`; it invokes the
configured provider and may persist data, so verify storage isolation first.

Run each comparison at least three times on the same host, use checkpoint
medians, and preserve both RSS and USS. Do not commit the generated JSON or
subprocess logs. Stack logs can contain application data even though the JSON
does not, and should be deleted after extracting aggregate measurements.

## Regression gates for follow-up tasks

Use these stable comparisons rather than a single absolute RSS value:

- three-server READY whole-tree median: **742.7 / 552.8 MiB** RSS/USS;
- merged READY whole tree: **422.5 / 345.7 MiB**;
- legacy first-chat after-teardown delta: **+88.2 / +84.4 MiB**; do not compare
  this with the probe's new `first_chat_complete_live` checkpoint;
- embedding imports to READY: **+87.7 / +72.5 MiB**, traced current **+7.8 MiB**;
- OCR imports to enabled: **+106.2 / +87.0 MiB**, traced current **+12.4 MiB**;
- browser-use imports to Playwright READY: **+466.5 / +165.2 MiB** total,
  including **420.3 / 121.8 MiB** in Chromium;
- merged backend plus packaged Electron: **1951.0 / 1110.5 MiB** whole tree,
  with Electron main plus children at **1398.3 / 719.5 MiB**.

For any optimization, report the same checkpoint, process-category split,
Python traced current/peak when available, and host/build provenance. A change
that lowers RSS but not USS may only change residency rather than durable
unique memory.
