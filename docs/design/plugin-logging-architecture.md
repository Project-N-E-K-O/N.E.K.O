# Plugin logging ownership and compatibility

## Scope

This change fixes plugin log ownership, querying, subscriptions and the plugin
manager viewer. Main, Memory and Agent keep their existing logging defaults.
Agent/brain source is unchanged. The launcher delegates its existing brace-format
hook to the shared backend without extending its global formatting rules.
Public host logging keeps upstream replacement, shared-parent, eager-open and
subdirectory normalization semantics. Plugin-specific configuration is opt-in.

## Ownership

| Responsibility | Owner |
| --- | --- |
| Existing writable-directory probes, handlers and rotation | Shared logger backend |
| Filename generation and exact service-file matching | Shared logging layout |
| Plugin identity, initialization, child rebinding and degradation | Plugin logging runtime |
| Query, subscription and export selection | Plugin logs application module |
| HTTP/WebSocket envelopes and archive delivery | Plugin log routes |
| Query, connection and records for one viewer | Frontend log session |

The plugin HTTP app factory initializes the `PluginServer` namespace itself.
It preserves the host's namespace/shared handlers. To retain upstream SDK-era
third-party file collection, it also supplies a root bridge when the host has no
root file output. A host-provided root FileHandler remains authoritative, and
foreign handlers are never removed by this compatibility bridge. The bridge
forwards to the current namespace sinks instead of borrowing their lifetime,
including after public backend reconfiguration. A host
file output added later also takes precedence immediately.
The standalone entrypoint retains upstream's explicit Uvicorn logger bridge,
including when Uvicorn was configured before importing the application.

The standalone server and official metadata worker explicitly own their process;
the plugin runner initializes its child before importing plugin code. These
entrypoints keep their historical INFO level. No process creation, IPC, privilege,
socket-inheritance or shutdown policy is changed.

The common backend exposes `RobustLoggerConfig.iter_handlers()` to construct sinks
without installing them. Its ordinary `setup_logger()` still removes/closes all
previous handlers and installs the replacements as upstream did; public
`setup_logging()` still leaves root untouched and attaches a shared parent only
when that parent is empty. Deferred file opening and complete namespace commits
are selected only by plugin initialization.

`plugin._logging_runtime` owns plugin-only handler replacement and retirement.
It preserves external subscriptions and filters, and does not close borrowed
handlers while registered loggers still use them. In a forked plugin child,
inherited project handlers are rebound to the child's new sinks, including cached
Main/Memory/Agent loggers that stop propagation. Logger objects, levels and
propagation settings and foreign handlers survive; the parent is untouched.

Importing the SDK or obtaining an adapter is side-effect-free. Legacy setup
helpers initialize explicitly. SDK-only workers initialize on their first record
and retain third-party root collection, unless the containing host already owns
a root file output. Inherited project handlers trigger child bootstrap instead
of being mistaken for a preconfigured namespace. Initialization never infers a
plugin identity missing from the worker environment; custom workers using a
prestarted forkserver should pass their identity explicitly. To capture records
emitted before the first SDK call, initialize before those records:

```python
from plugin.logging_config import setup_plugin_logging
from plugin.logging_layout import plugin_log_service_name

setup_plugin_logging(plugin_log_service_name(plugin_id), own_root=True)
```

SDK-only initialization retains `NEKO_LOG_LEVEL`; legacy SDK `level`/`force`
arguments remain ignored. Direct `setup_plugin_logging(..., own_root=False)`
configures only the namespace, while the app factory/legacy SDK paths explicitly
provide compatibility collection. It is not a teardown of root handlers that
were already installed. The factory preserves a prior standalone root claim.

A failure to construct file logging degrades to a project-owned stderr handler.
The plugin can continue and an explicit later bootstrap retries file setup.
Replacing inherited sinks happens after replacement sinks exist, including in
this degraded mode. Ordinary SDK records do not repeatedly retry a failed disk.

## Directory and format compatibility

Writer and cold-reader candidate ordering and platform directory lookup share
one pure implementation in `utils.logging_layout`. The writer retains real
writable-directory probes and the existing fallback order; cold inspection only
estimates availability without writes. Plugin ID rules live in
`plugin.logging_layout`, separate from the generic directory/filename policy.
Queries and published path inspectors inspect the canonical file handler actually
attached to the namespace, including after public backend reconfiguration. They
do not rely on a cached directory receipt or guess with `os.access`.
The pure layout resolver is only a cold inspection fallback before initialization.
Directory queries do not instantiate a writer, create directories or clean logs.

SDK adapters retain legacy brace calls. Standard percent messages retain their
original message and arguments, handler filters and handler error protection.
The launcher's stdlib hook preserves existing getMessage wrappers and the old
brace fallback; it does not reinterpret valid standard messages. Public SDK
helpers and file-logger names delegate to this single persistence implementation.

## Reading and viewing

`LogService` owns file selection, snapshots, independent subscription cursors and
export selection. It returns read data; routes alone construct the existing HTTP
and WebSocket payloads. File replacement/truncation produces a new snapshot.
Snapshots locate the requested nonblank completed lines with reverse 64 KiB
reads, then parse only that window. Byte offsets still identify records and
incomplete UTF-8/lines remain available to subsequent polls. Very long lines or
blank tails can require more blocks, but ordinary tail queries do not traverse
the entire file. Directory discovery scans once, matching ownership before file
metadata access, including canonical and legacy server files in the same pass.

After runtime initialization, SDK records check the process ID without taking
the initialization lock; forked children still reinitialize through the locked
path. Generic service writes do not scan registered loggers. Handler-reference
scans occur only during initialization/reconfiguration. Viewer cursors remain
independent, so polling and parsing appended records still scale with the number
of viewers; there is no shared watcher or unbounded history cache.

Each viewer uses one WebSocket session for initial history, appends and refresh.
Old sockets cannot replace a current session. Both pages reuse the same viewer;
HTTP endpoints remain for existing clients without competing for the page state.
Viewer search includes message, level, raw timestamp and source (`file:line`).
Draft input filters the current window immediately; refresh applies the same
fields to snapshots and appends through explicit `search_scope=all`. Existing
HTTP and WebSocket clients retain message-only search by default. Error frames
retain the legacy `error` text and add a stable `code`; the viewer translates
known codes in all supported locales and uses a localized fallback for older
servers or unknown codes. Export failure cleanup runs outside the event loop.

Keep existing endpoint paths, initial/append messages, historical text formats,
error/rotated exports and exact plugin-file isolation. Shared legacy Plugin files
may appear as labelled server history, never as an individual plugin's records.
No migration moves or rewrites user logs. Removed private reader/watcher/store
implementations have no surviving alternate paths.

## Acceptance

Tests cover real forked children with inherited service-specific loggers, spawned
SDK workers, official metadata imports, degraded bootstrap and recovery, SDK
percent-record filtering and error handling, independent viewers, HTTP/ZIP/WS,
writer fallback and bounded snapshot parsing. Host regressions cover upstream
handler replacement/eager opening, legacy
subdirectory values, fallback ordering, unchanged root/shared-parent defaults and
embedded HTTPX failures. Plugin regressions cover borrowed subscriptions and
filters, concurrent reconfiguration, preconfigured namespaces, active writer
queries across days, preconfigured Uvicorn errors and concurrent standard logging
reconfiguration without handler/global lock inversion. Worker tests cover explicit
and lazy SDK initialization across fork/spawn/forkserver, including a prestarted
forkserver and a parent thread holding the plugin initialization lock.
Tests isolate both user storage and the backend's development-log cleanup root;
the subprocess harness carries that isolation into fresh Python interpreters.
Native Windows/macOS launch and packaging still require platform validation.
