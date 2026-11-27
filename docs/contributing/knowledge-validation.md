# Knowledge native acceptance and measurements

These opt-in tests use existing native model assets, synthetic documents and new
temporary knowledge roots. They need no saved developer session, prebuilt ZIP,
API key, running application or remote Market account. Run from the checkout
being tested with its existing Python environment; worktrees borrowing an
environment should set `UV_PROJECT_ENVIRONMENT` to that environment first.

## Resources and isolation

Set `NEKO_REAL_EMBEDDING_ROOT` to an existing model root containing:

```text
embedding_models/
  local-text-retrieval-v1/
    tokenizer.json
    onnx/model_quantized.onnx
    onnx/model_quantized.onnx_data
```

The encoder is fixed to 128 dimensions, int8, max-length 1024, and the same
`has_vnni=false` / `has_avx2=false` constructor settings as the existing native
contract. This controls the test profile; it is not a measurement of every
automatic hardware setting or quantization. The report retains the exact model,
tokenizer and external-data fingerprints. Assets are read-only inputs and their
file identities must be unchanged after each test. Nothing downloads assets or
installs dependencies. An unset root skips; an explicitly invalid or incomplete
root fails setup.

The parent test creates fresh knowledge roots, passes `child_process_env()` to
each child and captures its logs. The probe installs `isolate_test_process()`
before any product import. Logs and ConfigManager also use project-owned test
isolation. The recovery stage removes only the derived SQLite database and side
files under this newly created installation; Pack/ZIP and source files are
checked by content hash. Every service and encoder closes before its child exits.
The tests do not start network listeners or touch an existing knowledge root.

## One-command acceptance

On POSIX, supply the existing asset root and an output path:

```bash
NEKO_REAL_EMBEDDING_ROOT=/path/to/embedding_models \
uv run --no-sync python -m pytest \
  tests/unit/test_knowledge_native_acceptance.py::test_native_archive_cross_process_recovery \
  -v -rfE -o junit_family=xunit1 --junitxml=/tmp/knowledge-native.xml
```

The parent runs three independent probe processes and a separate Plugin pytest
process in a fixed sequence, regardless of pytest random ordering:

1. Import two versions of a synthetic source, verify default retrieval and
   automatic-reference switches are off, explicitly load the real encoder, then
   export a v2 package with an automatic version and friendly download name.
   Cold import and export do not run inference; source history is retained.
2. Install that exact ZIP through the actual Memory upload route. Its vectors
   are reused without inference. Explicitly enabling retrieval permits BM25;
   explicitly loading the encoder permits hybrid retrieval.
3. Start a new interpreter after removing only the installation's derived
   index. Recover the vectors with their exact stored model specification,
   preserve switches and immutable inputs, query successfully and verify both
   author source versions and all source-file hashes.
4. Generate a separate version-1.0.0 contract fixture from the new real vectors
   and run the existing native-market installation contract under
   `plugin/tests`' own configuration and isolation. The original automatic
   export remains unchanged. No prebuilt fixture directory is required.

The fourth step uses the real Plugin worker, Main/Memory routes, multipart
forwarding, tasks and SQLite. Remote HTTP, OAuth and the receiving encoder
identity are controlled fixtures; its assertions forbid re-encoding. This is
not a live Market or full production-app middleware/desktop acceptance. A skipped
Plugin contract does not count as a successful parent acceptance.

The existing `tests/unit/test_unified_embedding_real.py` remains a focused
token-budget/native round-trip check. Existing externally built fixtures can
still be tested separately with `NEKO_UNIFIED_PACKAGE_FIXTURES` and
`plugin/tests/unit/server/test_unified_knowledge_native_roundtrip.py`.

## Scale measurements

Performance cases require both the model root and `RUN_PERF_TESTS=true`:

```bash
NEKO_REAL_EMBEDDING_ROOT=/path/to/embedding_models RUN_PERF_TESTS=true \
uv run --no-sync python -m pytest \
  tests/unit/test_knowledge_native_acceptance.py::test_native_scale_measurements \
  -v -rfE -o junit_family=xunit1 --junitxml=/tmp/knowledge-performance.xml
```

PowerShell can run all three cases with the equivalent environment settings:

```powershell
$env:NEKO_REAL_EMBEDDING_ROOT = 'D:\models\embedding_models'
$env:RUN_PERF_TESTS = 'true'
uv run --no-sync python -m pytest tests/unit/test_knowledge_native_acceptance.py -v -rfE -o junit_family=xunit1 --junitxml="$env:TEMP\knowledge-native.xml"
```

Each scale (10 and 100 synthetic JSON entries, one short chunk per entry) gets a
fresh build process and a fresh reader process. The build keeps the original
automatic index loop, batch size, duty pauses and round delays. The existing
local-embedding switch starts backfill after explicit model loading. The reader
opens the persisted index with its model cold and automatic backfill stopped,
then loads the model and reads the vector snapshot explicitly before queries.
Correctness, vector identity and inference ownership are asserted. There are no
speed thresholds; bounded child and operation timeouts prevent hung probes.

JUnit property `knowledge_native_performance` contains JSON for each scale;
`knowledge_native_acceptance` contains the acceptance receipts. Use
`-o junit_family=xunit1` to retain testcase properties without pytest's xunit2
compatibility warning. Stage logs and JSON also remain in pytest's temporary
directory until pytest's normal retention removes them. Timing fields use
milliseconds and RSS fields use bytes.

| Field | What it measures |
| --- | --- |
| `build.empty_library_open_ms` | Encoder/service construction and empty service start, after Python/product imports |
| `build.json_import_ms` | Synthetic JSON construction, import job and observable terminal state; not document parsing |
| `build.index_complete_ms` | Enabling local encoding to all stored vectors ready, including production pauses before readiness; 200ms observation granularity |
| `build.native_batches` / `native_texts` | Actual native document calls and texts; not inferred from elapsed time |
| `read.restart_library_open_ms` | Opening the persisted library in a new interpreter, after module imports; model remains unloaded |
| `model_load_ms` | Explicit native session loading in that stage |
| `read.first_bm25_ms` / `first_hybrid_ms` | First query before model loading / after loading and snapshot preparation |
| `read.snapshot_load_ms` | Explicit vector snapshot loading, separately from the periodic background refresh |
| `read.hybrid_samples_ms` / `hybrid_p50_ms` / `hybrid_p95_ms` | Twenty varied follow-up queries, median and nearest-rank p95 |
| `rss_bytes` | Whole-process RSS sampled at named milestones; neither process peak nor model-only allocation |

Compare reports only with compatible model fingerprints, corpus sizes, platform,
hardware and encoder settings. OS file caches are not flushed. Index readiness
does not wait for the last batch's trailing pause or the periodic snapshot
refresh, and these probes exclude Python startup/import latency. One build per
scale and twenty query samples provide a reproducible baseline, not proof of
large-library, low-memory, concurrent-chat or retrieval-quality performance.

## Remaining manual acceptance

These cases do not validate a packaged desktop shell, live Market/OAuth,
production PostgreSQL/gateway, or Windows/Linux hardware. Use the normal desktop
startup flow with a matching source/build snapshot and an explicitly isolated
runtime for that acceptance. Do not switch or overwrite an unrelated working
checkout, publish a synthetic package remotely, or count a mock/skip as a real
platform result. Existing model-identity and resident-session revocation follow-ups
remain separate from this validation tooling.
