# Plugin logging ownership and compatibility

## Scope

This change fixes plugin log ownership, querying, subscriptions and the plugin
manager viewer. Main, Memory and Agent keep their existing logging defaults.
Agent/brain source and merged-process root ownership are unchanged from upstream.
The launcher only delegates its existing formatting hook to the shared backend.

## Ownership

| Responsibility | Owner |
| --- | --- |
| Existing writable-directory probes, handlers and rotation | Shared logger backend |
| Filename generation and exact service-file matching | Shared logging layout |
| Plugin identity, explicit initialization and child root capture | Plugin logging runtime |
| Query, subscription and export selection | Plugin logs application module |
| HTTP/WebSocket envelopes and archive delivery | Plugin log routes |
| Query, connection and records for one viewer | Frontend log session |

The plugin HTTP app factory initializes the `PluginServer` namespace itself.
Agent does not need a logging-specific call. A standalone entrypoint can claim
root before importing the server; app construction preserves that explicit claim.
Embedding configures only the plugin namespace and preserves host root/shared
handlers and levels. Plugin children explicitly rebind their inherited project
handlers so standard logging and SDK logging use the same plugin file.

Public host `setup_logging()` defaults remain unchanged: no automatic root claim,
no Agent-name policy and no global formatter installation. Plugin initialization
requests optional ownership explicitly. Importing the SDK or obtaining an adapter
never initializes files or handlers.

## Directory and format compatibility

The existing backend retains its original write probes and platform fallback
behavior. The plugin runtime retains the actual directory selected by that writer;
queries and published path inspectors use it rather than guessing with `os.access`.
The pure layout resolver is only a cold inspection fallback before initialization.
Directory queries do not instantiate a writer, create directories or clean logs.

SDK adapters and the launcher's existing stdlib hook share one formatter. Public
SDK helpers, legacy calling conventions and file-logger names remain delegating
interfaces rather than independent implementations.

## Reading and viewing

`LogService` owns file selection, snapshots, independent subscription cursors and
export selection. It returns read data; routes alone construct the existing HTTP
and WebSocket payloads. File replacement/truncation produces a new snapshot.
Snapshots retain bounded raw history before parsing, avoiding whole-file parsing.

Each viewer uses one WebSocket session for initial history, appends and refresh.
Old sockets cannot replace a current session. Both pages reuse the same viewer;
HTTP endpoints remain for existing clients without competing for the page state.

Keep existing endpoint paths, initial/append messages, historical text formats,
error/rotated exports and exact plugin-file isolation. Shared legacy Plugin files
may appear as labelled server history, never as an individual plugin's records.
No migration moves or rewrites user logs. Removed private reader/watcher/store
implementations have no surviving alternate paths.

## Acceptance

Tests cover real forked children, plugin app-factory initialization without Agent
changes, standalone ownership preservation, unchanged host defaults, the launcher
formatter entrypoint, actual writer fallback, HTTP/ZIP/WebSocket compatibility,
independent viewers and bounded snapshot parsing. ASGI transport tests live in
plugin integration tests; unrelated host logging policy is outside this change.
