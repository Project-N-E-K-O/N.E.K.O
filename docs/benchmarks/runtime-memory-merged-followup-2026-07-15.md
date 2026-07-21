# Merged-server memory follow-up (2026-07-15)

This report follows the baseline in
`runtime-memory-baseline-2026-07-15.md` and evaluates whether the merged server
topology is reproducible and safe enough to remain the packaged default.

## Scope and provenance

- Measurement source base: `origin/main` at
  `715cab7fa8143aaf6cce92b8645e8cbdebf7e467`. Before publication, the feature
  commit was rebased onto `46da2117c66602e21422d5a52debffda0ade9b72`;
  intervening main changes did not touch the launcher or benchmark paths.
- Feature worktree: `codex/merged-server-memory-optimization` with the topology
  guards described below applied but not committed.
- Host: Windows build 26100, Intel Core Ultra 7 265KF, 63.625 GiB RAM.
- Python: 3.11.13, invoked through `uv run` only.
- The worktree and the existing main checkout had the same `uv.lock` SHA-256:
  `6AEE77F9064D3503306663F7A10A083C2168E40500BA89FC38F3BA4DC41A9B27`.
  The locked main-checkout environment was reused with `--no-sync`; source
  imports and the benchmark script came from this worktree.
- Each topology ran three times in alternating order. The checkpoint is an
  eight-second-settled, three-second median after all three signed `/health`
  responses reported the expected service and one shared non-empty instance ID.
- Generated JSON and subprocess logs were not committed. No synthetic chat was
  sent, so this follow-up did not add conversation turns.

## Repeated READY result

Values are whole measured tree MiB. The three runs include one colder first
run followed by warm-cache repeats; they are not presented as three cold boots.

| Topology | Signed-health READY, median (range) | RSS, median (range) | USS, median (range) |
| --- | ---: | ---: | ---: |
| Three server | 6.222 s (6.109-6.277) | 711.587 (708.433-712.184) | 524.539 (524.422-525.024) |
| Merged server | 4.098 s (3.835-4.239) | 395.926 (390.168-396.875) | 318.161 (317.786-318.738) |
| Merged saving | 2.124 s | 315.661 | 206.378 (39.3%) |

The #2350 baseline reported a 320.2 MiB RSS / 207.1 MiB USS saving. This rerun
reproduces essentially the same unique-memory reduction, with a very narrow USS
range in both modes. The absolute totals are lower than #2350, but the topology
delta is stable.

Per-service signed-health timing explains the startup difference:

| Topology | Memory | Main | Agent | All-ready |
| --- | ---: | ---: | ---: | ---: |
| Three server, representative median-shaped run | 2.676 s | 5.070 s | 6.222 s | 6.222 s |
| Merged server, representative median-shaped run | 3.571 s | 4.098 s | 3.571 s | 4.098 s |

The merged process pays a slightly later memory-service READY but avoids the
serial multi-process import gate and reaches the complete service set earlier.

## Runtime contracts traced

The launcher negotiates one instance ID and all public/internal ports before it
selects topology. Both modes keep the same external contracts:

- main HTTP/WebSocket: 48911;
- memory HTTP: 48912;
- agent/tool HTTP: 48915;
- embedded plugin HTTP: 48916;
- main-agent ZMQ: 48961, 48962, and 48963.

Three-server mode starts memory, then main, then agent as separate
`multiprocessing.Process` children. Import events serialize the heavy imports;
port readiness plus each service's ready event completes launcher READY. The
launcher treats main and memory as critical, but an agent exit can degrade
without killing the UI and memory service. Shutdown is main, then memory, then
agent so main can release character state and finish cloudsave work while memory
is still reachable.

Merged mode imports memory, agent, and main into one Python process, then runs
three Uvicorn servers on one asyncio loop. HTTP and ZMQ are deliberately kept;
there is no merged-only direct-call path. Each real user plugin is still a
separate non-daemon `multiprocessing.Process`, and the plugin HTTP server keeps
its dedicated thread/loop. Plugin-code crash isolation therefore remains, while
main/memory/agent process and event-loop fault isolation does not.

## Compatibility decision

| Scenario | Default decision | Reason |
| --- | --- | --- |
| Packaged Electron/Steam, one launcher owns the full backend | Merged on the Xiao8 server side; packaged restart remains a release gate | Reproducible 206.4 MiB USS saving; `/`, `/chat`, and `/subtitle` keep main port 48911 and the same HTTP/WebSocket contract. |
| Source development, pytest, debugger, fault injection | Three server | Independent traces and service-level crash isolation are more valuable than the memory saving. |
| Manual service startup or independent supervisor | Three server | Independent ownership must remain explicit; source mode keeps this default. |
| Partial or mixed existing-service footprint | Three server on isolated fallback ports | Neither topology may splice services from different instance IDs or IPC plans; process isolation is retained for recovery from the conflicting footprint. |
| Deployment requiring agent/browser/native failure not to take down chat and memory | Three server | Multi-process mode intentionally treats agent as non-critical; merged has one shared failure domain. |
| User plugins | Separate plugin processes in both modes | Provider/plugin isolation remains a hard contract and was not changed. |

The root page and Electron-shaped `/chat` and `/subtitle` routes returned HTTP
200 with identical content sizes in both topologies. This verifies the Xiao8
server side only. BrowserWindow recreation, preload IPC, updater relaunch, and
packaged-binary exit behavior live in N.E.K.O-PC and were not rebuilt in this
worktree, so packaged update/restart compatibility is not claimed here.

## Safety changes

The packaged/source default split is unchanged: frozen builds default to merged,
source runs default to three-server, and `NEKO_MERGED=0` remains the immediate
rollback. The follow-up adds these guards:

1. Merged READY now requires all three signed health responses, the correct
   service roles, and the current instance ID. Timeout or an early Uvicorn task
   exit cannot emit `startup_ready`.
2. Attach is allowed only when all three default ports identify the expected
   service roles and one shared non-empty instance ID. A partial or mixed
   footprint is never spliced into a new runtime: every conflicting public port
   moves to a fallback, internal IPC ports are planned afresh, and the launcher
   forces one complete three-process topology.
3. Uvicorn 0.38's `capture_signals()` and the legacy
   `install_signal_handlers()` path are both disabled per server so only the
   launcher coordinates signals.
4. Merged shutdown now waits for main, then memory, then agent with the existing
   20/12/8-second per-service budgets. A timeout cancels that service task and
   advances cleanup; the watchdog remains a longer topology-wide last resort.
5. Any merged server task ending unexpectedly requests topology-wide shutdown;
   startup/runtime failures propagate a non-zero launcher exit code.
6. The benchmark now records effective interval/window/topology metadata,
   backend commit plus dirty/status/diff and `uv.lock` hashes, per-service
   signed-health timing, optional route probes, explicit shutdown status, and
   post-force residual process/port checks. A requested graceful-shutdown
   failure makes the CLI exit non-zero after preserving the JSON result.

## Shutdown smoke and remaining gate

A source-mode Ctrl-Break smoke reached the launcher-owned merged signal handler,
closed main before memory and agent, completed character release/cloudsave work,
and released all three public ports. The `uv run` wrapper itself also belongs to
the Windows console process group and exits with Windows status `0xC000013A`, so
the probe correctly records this source-wrapper run as `graceful: false` even
though application cleanup completed. A direct packaged launcher/updater smoke
is still required in N.E.K.O-PC to assert an externally observed exit code of 0
and automatic window reconnection after relaunch.

## Validation

- `ruff check` passed for the launcher, benchmark, and touched tests.
- 54 launcher, benchmark, port, layering, logging, and shared-singleton tests passed.
- The startup lazy-import contract check passed.
- Real three-server and merged launches passed signed-health identity checks.
- `/`, `/chat`, and `/subtitle` returned HTTP 200 in both topologies.
- A final post-change merged smoke reached signed READY in 3.911 s, recorded the
  dirty backend provenance and lock hash, and left no residual PID or port.
- Merged shutdown logs showed main release/cloudsave work before memory shutdown,
  followed by agent shutdown; all public ports closed.

## Reproduction

Run from a clean worktree with the locked environment available:

```powershell
uv run python scripts/runtime_memory_baseline.py --output three.json stack `
  --backend-command 'uv|run|python|launcher.py' `
  --env NEKO_MERGED=0 --settle 8 `
  --probe-path / --probe-path /chat --probe-path /subtitle

uv run python scripts/runtime_memory_baseline.py --output merged.json stack `
  --backend-command 'uv|run|python|launcher.py' `
  --env NEKO_MERGED=1 --settle 8 `
  --probe-path / --probe-path /chat --probe-path /subtitle
```

Alternate the two modes and repeat each at least three times. Compare the same
signed-health checkpoint and report both RSS and USS; do not commit the JSON or
logs.
