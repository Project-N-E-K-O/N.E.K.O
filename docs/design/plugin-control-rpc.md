# Plugin control requests and entry execution

Status: implemented record. The code and tests under `plugin/core/rpc.py`,
`plugin/core/entry_executor.py` and `plugin/server/messaging/` are authoritative.

## Decision

Control requests use one lifetime implementation, `plugin/core/rpc.py`.
Ordinary entry triggers and cross-plugin entry calls use the same
`plugin/core/entry_executor.py` invocation implementation. The public SDK's
`plugins.call_entry()` and `Result` interface remain unchanged.

The old implementation distributed response ownership across a shared inbox,
an orphan cache, multiprocessing response maps, routing callbacks, and native
command futures. It could lose ownership of concurrent replies, stop the router
on a plugin failure, and block nested requests behind their parents.

## Ownership

| Module | Responsibility |
| --- | --- |
| `RpcPeer` | Register before sending, correlate replies, enforce the total request deadline, propagate cancellation, close outstanding exchanges across event loops |
| `RpcDispatcher` | Admit a bounded number of complete requests, own their tasks, execute handlers concurrently, convert failures, await response delivery, stop tasks |
| `PluginRouter` | Bind host operations to the authenticated plugin queue and downlink connections; translate host domain errors |
| `PluginContext` | Adapt SDK operations and the dedicated image transport to its peer |
| `PluginCommunicationResourceManager` | Adapt native command/result dictionaries to a peer, preserve startup handshake and independent control/message/media consumers |
| `EntryExecutor` | Resolve and refresh entries, prepare parameter models, establish invocation context, execute, persist state, normalize results |

Host operation handlers return values or raise domain errors. They do not own a
`send_response` callback, request table, or fallback response store. New host
operations should be added to the operation registry without implementing any
request waiting or delivery logic.

The entry command codec preserves both `TRIGGER` and `TRIGGER_CUSTOM` wire names.
Both use the executor for `plugin_entry` invocations. Custom events retain their
own event coordinates while sharing lifetime and result handling.

## Invariants

- A request has one reply owner per connection. Replies can arrive out of order,
  from another thread, or before the send coroutine returns.
- Adapter-supplied request IDs must be unique for the connection's lifetime.
  A host-authenticated connection ID scopes disconnect/cancel notifications so
  a retiring plugin instance cannot cancel a new instance with the same name.
- Each request completes once. Late and duplicate replies cannot complete a
  different request and are not retained in an orphan cache.
- Queuing, sending and waiting spend the same deadline. Monotonic deadlines are
  valid across these local host processes; this protocol is not a cross-machine
  monotonic clock protocol.
- Image normalization's event-loop deadline is transferred as a remaining budget
  before entering RPC; an event loop's clock epoch is not a protocol timestamp.
- Nested calls inherit trace identity, parent request identity and the remaining
  deadline. Call depth is bounded and malformed metadata receives an error.
- Host operations from the same plugin run concurrently and can complete in any
  order; the previous router handled them one at a time. A plugin that needs
  one operation's effect before the next (write a value, then read it) must
  await the first call before issuing the second.
- Receiving requests does not wait for their handlers. Admission is fail-fast
  under saturation, including when an existing request is sending its reply.
  A child request can receive `BUSY`; it never waits indefinitely for a slot held
  by its parent.
- Cancellation identifies both the source plugin and its request. Each hop
  propagates cancellation to the next hop. Entry cancellation cooperatively
  unwinds the running task.
- Closing a peer terminates both reply waiting and blocked async sending.
  Closing the dispatcher cancels and joins the tasks it owns before transports
  are closed. Transport implementations must honor cancellation/bounded sends.
- Whole-request deadline expiry, an operation's internal timeout, connection
  closure, plugin failure and serialization failure remain distinguishable.
- Control adapters share the established MessagePack value codec. Integer map
  keys and path values retain their existing behavior. Serialization and
  oversized correlated results become error replies before the control
  connection can be lost to a frame-size rejection.
- A timeout does not roll back committed effects. Configuration writes keep
  their existing commit guard. Non-idempotent operations are not retried by RPC.
- The dispatcher cuts a handler off at the request deadline unless the handler
  is marked `owns_deadline`; such a handler plans against `ctx.deadline` and
  produces its own degraded reply. Configuration writes own their deadline: an
  update that cannot persist in time returns `persisted: false` with the
  in-memory view, and a replace that cannot persist is rejected unapplied.
- Failed `Result` values retain the ordinary entry's state-persistence semantics.
- Logs contain operation identifiers, correlation identifiers, error types and
  stack locations. Exception text and parameter/model values are excluded from
  the new RPC error logs. Error descriptions can still be returned to the caller.

## Transport compatibility

Control requests use authenticated per-plugin uplinks/downlinks. Image data still
uses the dedicated media socket, and messages retain their separate data plane;
sharing reply correlation does not multiplex large data onto the control socket.

The host's former standalone ZMQ request listener (`ZmqIpcServer`, default
`tcp://127.0.0.1:38765`) has been removed, along with its
`NEKO_PLUGIN_ZMQ_IPC_*` settings. Clients must use the authenticated per-plugin
control uplink that the host creates for each plugin process; there is no
anonymous endpoint for out-of-process callers. Current memory reads and bus
mutations use the context's peer, not the former per-thread synchronous IPC
client. Shared response maps,
Manager startup, queue stealing, short polling and fallback dictionary writes
have been removed.

## Verification

Tests cross the peer/dispatcher and executor interfaces. Native process tests
start three plugins with real ZMQ transports and exercise failed results followed
by healthy calls, nested calls, host configuration queries during an invocation,
out-of-order replies, parameter models and timeout recovery. Dedicated media
transport tests verify image uploads after correlation was unified.

Edge tests cover blocked-send closure, a foreign event loop, late/duplicate
replies, malformed depth metadata, bounded admission during reply delivery,
private validation input, failed-result persistence, serialization errors and
internal timeouts. The removed shared-map and callback tests are replaced by
these observable contracts, rather than preserved as tests of deleted internals.
