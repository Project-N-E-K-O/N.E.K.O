# Plugin process creation and concurrent startup

Status: implemented record. The implementation in `utils/process_creation.py`
and the scanner/host integration tests are authoritative.

## Problem and decision

Concurrent plugin autostart mixes metadata `subprocess.Popen` calls and
`multiprocessing.Process.start`. On a fork platform, a sibling plugin can copy
the temporary exec-error pipe writer before Popen closes its parent copy. That
plugin stays alive, so Popen waits forever for EOF. The scanner has not yet
received its Popen object or started its scan timer. The autostart wave then
holds the lifecycle operation lock indefinitely.

Host-owned metadata exec and plugin process creation use one process-local
thread lock through `process_creation.popen` and `process_creation.start`.
The window covers the complete Popen constructor or Process.start call. It
does not cover plugin imports, metadata protocol I/O, startup handshakes,
plugin execution or process termination. Independent plugins still initialize
concurrently; autostart concurrency and dependency ordering do not change.

The module depends only on the standard library. Core does not import server
lifecycle code. It neither patches standard-library process creation nor
changes multiprocessing's start method. Fork children replace their copy of
the lock before running plugin code, so a plugin can create its own processes.
Existing HTTP socket, operation-lock handle and credential fork cleanup remain
owned by their existing modules.

## Deadline, cancellation and ownership

- Waiting creators use an absolute monotonic deadline. A retired or expired
  waiter does not launch later when the lock becomes available.
- Host startup cancellation signals the queued creation worker, then awaits
  the existing cancellation-safe teardown. Once OS creation begins, the
  caller still owns any resulting process and must wait for it and reap it.
- Scan time includes request preparation, lock waiting and OS creation.
  Handshake time includes waiting/creation from the host's creation request.
  Both use the remaining budget instead of restarting a full timeout.
- A process returned after expiry is cleaned up by its owner. The helper must
  not raise after successful creation and lose the returned process handle.
- Process creation itself is not a hard-cancellable OS operation. This change
  removes the identified cooperating fork/exec race; a timeout around a worker
  thread would not make arbitrary OS creation stoppable.
- DEBUG timing records contain only operation kind, lock wait and creation
  duration; command arguments, environment and credentials are not logged.

## Scope and alternatives

This is coordination between cooperating host-owned creators, not a sandbox
for arbitrary third-party forks. New host paths mixing fork and exec in the
same process must use the same creation interface. Direct process creation in
separate plugin processes does not contend with the parent host's lock.

Serializing complete startup would discard import/initialization concurrency.
A global switch to spawn/forkserver changes import, object/context and frozen
executable behavior and needs separate compatibility/performance acceptance.
Closing every inherited pipe would damage multiprocessing's own IPC and
sentinels. None of these is required to close the observed creation window.

## Verification

- A real metadata worker and the actual host creation method are synchronized
  at the CPython fork/exec window. The unmodified code hangs while its sibling
  remains alive; coordinated creation lets the scan finish first.
- A blocked plugin import does not retain the creation window.
- Fork, spawn and forkserver children can create another subprocess where
  those methods are available. Linux CI executes the actual POSIX race;
  Windows retains its full plugin suite.
- Deadline/cancellation tests cover queued creation, post-creation expiry,
  remaining handshake budgets, error recovery and cancellation-safe cleanup.
- Performance comparisons must separate process creation/lock wait from full
  startup. Paired cold/warm runs of identical fixture plugins should record
  total time and peak process/memory use. The race test itself is a behavior
  assertion, not a fragile millisecond performance threshold.

### Local acceptance sample (2026-10-28)

Linux/Python 3.11, warm filesystem: eight real metadata scans followed by eight
concurrent PluginHost/ZMQ startups, each with a 50 ms asynchronous fixture
startup. Three alternating samples per variant gave median total times of
0.5335 s without coordination and 0.5340 s with coordination (about +0.1%).
Both peaked at eight child processes; sampled summed child RSS stayed within
the same approximately 435–447 MiB range (shared pages are counted repeatedly).
This sample is not a fixed throughput guarantee or cold/frozen-platform result.

The unsafe comparison separates scan and fork phases so the known pipe race
cannot invalidate the timing. Logging is disabled equally in both timing
variants: a separate inherited stdio-flush stall was observed with redirected
output in both launch variants. That general fork/stdio problem is not solved
or hidden by this creation-window fix. The normal-logging two/eight-plugin
integration test separately verifies overlapping initialization and operation
lock release under pytest capture.
