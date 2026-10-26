# Plugin logging ownership and compatibility

## Purpose

Plugin logging has one write backend, one read implementation, and one owner of
each viewer session. The work fixes missing server logs in embedded operation,
plugin records reaching the parent process's file after fork, and competing
history/realtime updates. It does not introduce a logging platform, alternate
storage backend, or a second implementation behind a migration flag.

## Ownership

| Responsibility | Owner |
| --- | --- |
| File layout, writable-directory policy, handlers and rotation | Shared logging utilities |
| Selecting a service identity and installing its logging | Process/service startup |
| Plugin-facing logger methods and historical calling conventions | SDK logging facade |
| File discovery, query, subscription and export selection | Plugin logs application module |
| HTTP/WebSocket serialization and archive delivery | Log routes |
| Query, connection, records and loading/error state for one viewer | Frontend log session |
| Layout and user actions | Shared viewer and its two page hosts |

The shared utilities do not import the plugin host or plugin server. File naming
and target normalization are shared by readers and writers. Reading a directory
or log never installs handlers, creates directories, or cleans up historical data.
Filename generation and exact file-ownership recognition both live in the shared
layout module. SDK records and ordinary Python records use the backend's single
message-formatting policy; adapters do not maintain another interpolation rule.

## Write lifecycle

Standalone and embedded plugin servers explicitly configure the `PluginServer`
namespace. Embedding configures that namespace without taking ownership of the
Agent root logger. A plugin child configures `Plugin_<normalized id>` before
loading plugin code and makes its ordinary Python loggers use the same target.
Only logging handlers owned by this logging system may be replaced or detached.
Initialization and inherited handler cleanup account for process identity.

Importing the SDK or obtaining an SDK logger does not initialize logging.
Existing logger objects continue to work when a parent process forks a plugin
child; their service resolution must follow the explicit runtime configuration.

## Reading and viewing

`LogService` owns file access for the existing query, files, directory and
export endpoints. Each live subscription owns its snapshot and subsequent read
position. Subscriptions do not advance another viewer's progress. File identity
changes and truncation cause an explicit new snapshot rather than silent skips.
The application returns `LogReadResult` data, while routes construct HTTP payloads
and WebSocket message types. The implementation lives in `logs/service.py` because
its responsibility includes subscriptions and file selection as well as queries.

The viewer uses one WebSocket session for both initial history and appends.
Its requested line count and filters are sent when opening or refreshing that
session. Responses from obsolete connections cannot replace its current state.
The outer page's refresh action delegates to that same session. HTTP queries
remain available to existing clients but no longer race a viewer's subscription.

The existing `initial` and `append` message names and query response fields are
retained. Subscription query parameters add line count and filters. Read errors
are distinct from a connected transport and from a successful empty result.
UI counts describe displayed records rather than claiming a whole-file total.

## Compatibility contract

- Preserve published Python exports, including `PluginFileLogger`,
  `enable_plugin_file_logging`, `plugin_file_logger`, SDK logger helpers and
  `enable_file_logging`. Their adapters delegate to the single implementation.
- Keep existing log HTTP paths, download behavior, and ordinary WebSocket clients
  that do not supply the new query parameters.
- Read existing text log formats, date files, error files and rotated files.
  Online viewing excludes separate error copies; export includes them.
- Match exact plugin identities, including normalized long identifiers, so a
  plugin cannot select another plugin's files through a shared prefix.
- Shared historical `Plugin` files may be exposed as legacy server history with
  their real filename. They must never be assigned wholesale to a plugin.
- Do not move, rewrite or delete user logs as a migration step.

Public compatibility is maintained at the SDK and transport edges. Private
helpers, implicit initialization, the global mutable watcher registry, duplicate
frontend loading paths and `plugin.server.logs` are not compatibility contracts:
their callers are migrated and their old implementations are removed.
Unused frontend HTTP helper functions are not published client contracts; removing
them does not remove the server endpoints. ASGI HTTP/archive/WebSocket behavior is
tested under `plugin/tests/integration`, separately from direct application tests.

## Acceptance

Behavior tests cover the actual startup order and the public read/session seams:

1. Standalone and embedded service identities agree, while Agent handlers remain
   intact.
2. Parent initialization followed by a real fork routes standard and SDK child
   records to the plugin, without child business records leaking to the parent.
3. Published plugin imports and calling conventions use that same backend.
4. Queries, export and subscriptions agree on file ownership; read operations are
   free of write-side initialization and cleanup.
5. Requested history is not overwritten by a second initializer; independent
   viewers, refresh and reconnect retain one state owner each.
6. Existing text data and transport entrypoints retain their supported behavior.

Tests of removed private helpers are replaced by observable behavior tests at the
new owning interface. Final review includes an import/reference audit for the
deleted implementation paths, not only green tests.
