# N.E.K.O Mini-Game SDK

This directory contains the public mini-game runtime and trusted host helpers.
Game code consumes `NekoMiniGame`; it must not call N.E.K.O REST endpoints,
microphone bridges, logging endpoints, or Avatar engine managers directly.

The host-side SDK companion lives in `main_logic/mini_game_sdk`. Structured LLM
operations use its bounded isolated-attempt policy: invalid provider content may
retry once with a new client, new isolation id, and a fresh message list; games
continue to own their schemas and validators.

## Capability policy

* `logging` is mandatory for every game and must be declared in
  `requiredCapabilities`.
* `runtime`, `dialogue`, `quick-lines`, `voice-input`, `speech-output`, `vision`, `audio`,
  `avatar-renderer`, `leaderboard-local`, and `leaderboard-server` are requested
  only when a game needs them.
* `quick-lines` is a separate optional capability layered on `dialogue`; a
  manifest that requests it must also request `dialogue`. The first-phase
  same-origin host grants it only when the host launch registration allows it
  and the bootstrap injected a quick-line provider. The common host contains no
  game names or dictionaries.
* Once a game uses `voice-input`, `speech-output`, `audio`, or
  `avatar-renderer`, it must use the official SDK implementation. A game cannot
  replace those capabilities with its own microphone, project-voice/TTS route,
  unmanaged Audio/WebAudio, Live2D, or VRM integration.
* Capabilities are granted at `connect()` time and remain fixed for the client
  lifetime.

The current first-phase transport is a trusted same-origin adapter. Every page
that shares the origin is inside the same trust boundary: the fixed
`BroadcastChannel` fallbacks used for voice control and transcripts provide
delivery, not confidentiality or isolation from another same-origin page. Do
not load unreviewed or adversarial game code into this phase-one host. Public
game code does not receive the transport internals, so a future untrusted-game
container can replace them with a private iframe or Electron bridge without
changing game calls. The public entry header, this document, and
`neko-minigame-sdk.d.ts` are the contract locations; games must not infer public
behavior from existing internal game source files.

## Connecting a game

```js
const game = await NekoMiniGame.connect({
  id: 'example-game',
  version: '1.0.0',
  protocolVersion: '1',
  requiredCapabilities: ['runtime', 'logging'],
  optionalCapabilities: [
    'dialogue', 'quick-lines', 'voice-input', 'speech-output', 'audio', 'avatar-renderer',
  ],
}, {
  transport: trustedHostTransport,
});
```

`connect()` always performs a trusted host handshake. The host verifies the
game identity/version, selects protocol v1, reports its host version and either
a `registered` or explicit `development` identity, and grants only reviewed
capabilities. Unknown or disabled formal games are rejected; a game cannot mark
itself as a development build. Before any game bundle, the trusted page
template emits a non-executable JSON script named
`neko-minigame-host-launch`, followed immediately by
`neko-minigame-same-origin-bootstrap.js`. The bootstrap synchronously consumes
and removes that host-owned node; game code receives a readiness promise and
the resulting factory, but no callable registration producer. It then attaches
a bounded non-writable, non-configurable handoff only to the adapter script
element and loads `neko-minigame-same-origin-host.js`. The adapter consumes that
script-scoped handoff once; the bootstrap removes the entire script node after
load instead of leaving a mutable registry property. The adapter seals the
resulting factory against replacement, retains immutable records only in its
closure, and intersects each record's allowlist with locally available
providers. The game factory cannot inject or replace a registration or
capability provider. A future marketplace/isolated host can produce the same
launch registrations after registry, integrity and launch-ticket checks without
changing game code.

Voice control is addressed by route identity alone: the host page accepts a
voice command only when its `game_type`, `session_id` and `sdk_route_instance_id`
match the live route, and the generation is required rather than optional, which
is what keeps the built-in soccer/badminton routes (they mint none) out of it.
None of those three are secrets -- `GET /api/game/route/active` returns them
unauthenticated -- so a reloaded host page recovers voice control on its own.

There is deliberately no bearer credential here. One existed briefly and was
removed: every page sharing this origin is inside the same trust boundary
already, and `POST /api/game/{game_type}/route/start` carries no local-mutation
validation, so anything able to reach this router could mint its own credential
and claim the route outright. A token that cannot be withheld from the party it
is meant to exclude is not a control, and keeping it made the boundary look
stronger than it is.

```html
<script id="neko-minigame-host-launch" type="application/json">
{"registrations":{"example-game":{"mode":"registered","gameId":"example-game","publisherId":"reviewed-publisher","version":"1.0.0","allowedCapabilities":["runtime","logging"]}}}
</script>
<script src="/static/game/sdk/neko-minigame-same-origin-bootstrap.js"></script>
```

Unknown optional capabilities are not granted and appear in
`game.capabilities.unavailable`. Missing required capabilities reject the
connection. Games should call `game.capabilities.require(name)` before starting
a feature that cannot operate without an optional capability. Capability grants
remain fixed for the client lifetime.

The handshake is cancellable and has a bounded timeout. Protocol mismatch,
unregistered/disabled identity, integrity rejection and unavailable required
capabilities use stable public error codes rather than transport-specific data.
Games can inspect the immutable `game.host` result but never receive registry
records, launch tickets, endpoints or credentials.

## Declared event, state, control, command and result contracts

Game-specific protocol names and payloads stay in the game manifest. The SDK
provides only the validated envelope and delivery mechanism:

```js
const game = await NekoMiniGame.connect({
  id: 'example-game',
  version: '1.0.0',
  requiredCapabilities: ['runtime', 'logging'],
  contracts: {
    events: {
      'round-started': {
        type: 'object',
        properties: { round: { type: 'integer', minimum: 1, maximum: 99 } },
        required: ['round'],
      },
    },
    states: {
      score: {
        type: 'object',
        properties: {
          player: { type: 'integer', minimum: 0 },
          opponent: { type: 'integer', minimum: 0 },
        },
        required: ['player', 'opponent'],
      },
    },
    controls: { stance: ['ready', 'paused'] },
    commands: {
      'match:analyze': {
        request: {
          type: 'object',
          properties: { snapshot: { type: 'string', maxLength: 1800000 } },
          required: ['snapshot'],
        },
        response: {
          type: 'object',
          properties: { ok: { type: 'boolean' } },
          required: ['ok'],
        },
      },
    },
    results: {
      match: {
        type: 'object',
        properties: {
          winner: { type: 'string', enum: ['player', 'opponent', 'draw'] },
        },
        required: ['winner'],
      },
    },
  },
}, { transport: trustedHostTransport });

await game.events.emit('round-started', { round: 1 });
await game.state.update('score', { player: 2, opponent: 1 });
await game.results.submit('match', { winner: 'player' });
await game.runtime.start();
const analysis = await game.commands.execute('match:analyze', { snapshot: serializedGameState });

const unsubscribeStance = game.controls.on('stance', ({ payload }) => {
  applyGameStance(payload);
});
```

`event` is a one-way notification whose Promise only confirms host acceptance;
LLM output remains a separate `dialogue.request()`. `state` is a replaceable
snapshot, not a frame-by-frame history. `control` is host-to-game input; the SDK
rejects undeclared names, invalid payloads, another session, incompatible
protocols and replayed/out-of-order sequence numbers. `result` submits a typed
game outcome and does not itself end the runtime.

`command` is a typed request/response operation bound to the active runtime
session and route generation. A trusted launch registration maps each declared
command name to a relative host route and independently caps its request bytes
and timeout; the mapping is not exposed to game code. The SDK and host retain
global ceilings of 2 MiB and six minutes, while the SDK admits at most eight
concurrent command requests. Games without `contracts.commands` do not require
a command transport. A command request schema must have `type: 'object'` because
the trusted host merges route identity into that request body. It must not
declare host-owned route identity, `_csrf_token`, or memory-policy fields; those values are
stripped or replaced at the trust boundary. Response schemas may use any
supported JSON type.

Command and vision responses (including non-2xx bodies) are limited to 2 MiB while reading
the network stream, before JSON parsing. Oversized responses are cancelled and
reject with `invalid_response`; normal response JSON/clone behavior is preserved.
Custom `fetchImpl` implementations must return a standard readable `Response`
for these size-limited requests; JSON-only response subsets are rejected before
calling `json()`. Successful command responses must contain valid JSON, including
when the response schema permits an empty object.

The command deadline covers both transport dispatch and response-body reading.
Cancellation, route end/reset and client disposal settle waiting callers and
discard late results. A transport that ignores abort keeps its bounded raw slot
until the body settles: `pendingCount` may be zero after cancellation while new
commands still receive `busy`. This prevents repeated retries from accumulating
abandoned body reads; capacity is released when those reads actually finish.

The supported schema subset intentionally excludes executable or expensive
keywords such as regex patterns, `$ref`, `oneOf` and custom validators. It
supports scalar types/enums and bounded object/array composition (including
inside command request objects and at the root of command responses). Undeclared
object fields are rejected unless that schema explicitly sets
`additionalProperties: true`. Contract declarations, payload size, payload
complexity, listener count and pending requests all have hard limits. The
machine-readable shape is in `neko-minigame-manifest.schema.json`. That schema
mirrors the structural, declared-type and capability-dependency checks used by
the runtime. `connect()` remains the canonical validator for dynamic
cross-field invariants that JSON Schema cannot express compactly, such as
minimum/maximum ordering, declared required-property names and aggregate
complexity limits.

In particular, `connect()` limits schema depth to 12 (root depth 0), total
schema nodes to 256, and its schema-string accounting budget to 65,536 JavaScript
UTF-16 code units, shared across **all** contract maps including both command
request and response schemas. This accounting is not a serialized JSON byte
limit. A manifest passing JSON Schema alone can still exceed these aggregate
runtime limits; run `connect()` validation during development as well.

## Runtime lifecycle and host events

Games that declare `runtime` configure their route payload and monitoring once,
then use the lifecycle methods instead of scheduling host requests themselves:

```js
game.runtime.configure({
  payload: () => ({
    session_id: game.runtime.session.id,
    currentState: snapshotGameState(),
  }),
  heartbeat: { intervalMs: 2500, timeoutMs: 4500 },
  outputs: { intervalMs: 700, timeoutMs: 8000, limit: 50 },
  pageExit: {
    payload: ({ type }) => ({ ...buildEndPayload(), reason: type }),
  },
});

const unsubscribe = game.events.on('runtime-output', async ({ payload }) => {
  await handleHostOutput(payload);
});

const response = await game.runtime.start(startPayload);
await game.runtime.pulse(true); // optional immediate refresh
await game.runtime.end(endPayload);
unsubscribe();
```

`runtime.start()` applies the host-returned session state and owns heartbeat,
output polling, request cancellation, page visibility, and page-exit listeners. A
rejected or failed start enters `degraded` state and keeps output polling
available without sending heartbeats. `runtime.end()` and `game.dispose()` stop
timers, remove the listener, and abort in-flight lifecycle requests. Games can
inspect `game.runtime.state` and the immutable `game.runtime.session` snapshot.
The session snapshot includes `routeInstanceId` while a route generation is
active, so integrations can correlate work without inventing their own route
identity.

After the host has resolved the session character, `context.read()`,
`dialogue.quickLines()`, `speech.preload()`, `speech.speak()`, and
`speech.mirror()` may be used before `runtime.start()` for opening-screen work.
Such pre-route speech requests carry the trusted session and character but no
route generation. If an active route already exists, speech and mirroring are
bound to that exact route generation; a stale generation is rejected. Standard
`dialogue.request()` remains an active-route operation because its host Prompt,
state and side effects belong to a concrete game round.

`runtime.reset()` also cancels in-flight protocol, context, dialogue, memory and
speech operations that were bound to the previous session, clears local speech
correlation state, and resets memory consent to default-off. Official game
storage is game-version namespaced rather than session-scoped, so storage
operations are not cancelled merely because a round creates a new session.
Reset is accepted only while the runtime is `idle`, `ended`, or `inactive`;
games must await `runtime.end()` before resetting an active, starting, degraded,
or ending route so the host session cannot be abandoned by local-only cleanup.

When `pageExit` is enabled, the SDK emits `page-exit` once so the game can
synchronously release game-owned resources, submits the configured end payload
with beacon fallback, and disposes the client while preserving the in-flight
route-end request. Games must not install duplicate `pagehide` or
`beforeunload` runtime handlers.

Incoming host events use immutable envelopes with `protocolVersion`, monotonic
`sequence`, `type`, `timestamp`, `sessionId`, and `payload`. Current built-in
types are `runtime-state`, `runtime-inactive`, `runtime-error`,
`visibility-change`, `page-exit`, and `runtime-output`. Handlers are bounded to 32 per event;
each event payload is bounded to 256 KiB, and each output poll accepts at most 50
items. Runtime output handlers run sequentially in poll order. A handler that has
not settled after 60 seconds is abandoned so output polling cannot stall
permanently. Abandoning is not cancelling: JavaScript promises cannot be
cancelled, so a timed-out handler keeps running and may still complete and touch
state after later handlers have started. The sequential ordering guarantee
therefore covers handlers that settle within that budget; a handler that exceeds
it forfeits its place in the order (and the SDK logs when that happens).

Host-to-game runtime lifecycle events remain a fixed core list. Game-to-host
events use the separate manifest-declared contract API above; they cannot add
unbounded names to the runtime listener registry.

Capability requests resolve to `{ ok, status, data }`; games do not receive raw
`Response` objects or transport endpoints. Request failures reject with
`NekoMiniGame.Error` and a stable `code`, including `timeout`, `cancelled`,
`disconnected`, `busy`, `disposed`, `session_invalid`, `invalid_contract`,
`game_unregistered`, `incompatible_version`, `network_error`, or
`request_failed`.
`details.operation` identifies the public operation without exposing transport
internals. Callers can pass an `AbortSignal` to request methods; managed runtime
start/end requests also abort on reset, normal disposal, or a superseding
lifecycle transition.

`dialogue.request()` is bounded to four pending requests, injects the trusted
runtime session/character and returns bounded immutable JSON. The default mode
uses the host-registered game Prompt. The experimental `author-managed` mode
accepts 1-32 ordered `system` / `user` / `assistant` messages (16,000 characters
per message, 64,000 total). Their order and stable-prefix/cache strategy belong
to the game author; the host prepends one protected N.E.K.O character/platform
message and does not persist this one-shot message list as host dialogue history.
Provider, model, API key, launch ticket and top-level raw history remain
host-controlled.

When a dialogue response includes a `control` object, every key must be declared
under `manifest.contracts.controls` and every value is validated against that
key's schema before the result reaches game code. Undeclared or invalid controls
reject with `invalid_contract`; the host does not interpret them as built-in
game rules. Each delivered control envelope carries the authoritative
`sessionId` and, once a route is active, `routeInstanceId`; the SDK drops output
from an older route generation before it reaches game handlers.

`dialogue.request()` does not inject request-scoped host context. A game that
needs host context must first use `context.read()` and deliberately place the
sanitized result in its own ordered messages. Provider-specific message ordering
compatibility is the game author's responsibility.

## Host context and game memory

`context-read` is a sensitive optional capability. Games request named scopes;
the host decides which reviewed scopes are available and returns only bounded,
sanitized data. The SDK never exposes raw memory databases, full chat history,
model file paths, provider configuration or credentials:

```js
const context = await game.context.read([
  'character-public',
  'recent-chat-summary',
]);
```

The default dialogue mode keeps generation-only context on the host and injects
it through the registered game Prompt. In experimental `author-managed` mode,
the reviewed game may instead read an allowed scope and place that sanitized
value explicitly in its own sequence. Neither mode can replace the protected
host prefix or remove N.E.K.O character rules and watermarks.

Games that submit anything to long-term memory declare `memory`. Before calling
`runtime.start()`, their opening screen must show a clear, default-off
“include this round in memory” consent control and pass the user's choice to
`game.memory.configureConsent(boolean)`. The SDK locks that choice at the first
start attempt; it cannot be changed during the round. A new runtime session
resets it to disabled.

```js
await game.memory.configureConsent(memoryCheckbox.checked);
await game.runtime.start(startPayload);

await game.memory.submit({
  events: visibleEvents,
  state: currentState,
  result: finalResult,
  summary: optionalGameSummary,
});
```

Without consent, `memory.submit()` rejects with `consent_required`. The game can
submit only visible events, state, result and an optional game summary; the host
still decides whether to write memory and owns the final memory text. A game
that uses only temporary in-game dialogue and never reads or writes persistent
history does not declare `memory` and does not need this switch.

Cross-round game settings and progress use the optional official `storage`
capability instead of raw `localStorage` or internal file paths. The trusted
host namespaces all keys by registered game identity and enforces its own total
quota; the SDK bounds keys, individual JSON values and pending operations:

```js
await game.storage.set('settings/pacing', { level: 3 });
const saved = await game.storage.get('settings/pacing');
const keys = await game.storage.list({ prefix: 'settings/', limit: 50 });
await game.storage.delete('settings/pacing');
```

`storage.clear()` affects only the current game's namespace and requires the
explicit argument `{ confirm: true }`. The local leaderboard persists through
this same namespace under a reserved `leaderboards/` prefix: `storage.get`,
`storage.set` and `storage.delete` reject keys starting with it, while
`storage.list` and `storage.clear` keep their whole-namespace meaning and
therefore do see and do clear local leaderboard state. Storage is not a substitute for memory:
it cannot access N.E.K.O conversations, character memory or another game.

## Local records and reserved server leaderboards

Personal records use the optional `leaderboard-local` capability. Boards are
declared in the manifest so the SDK owns score validation, ordering, retention,
entry limits and the game-scoped storage namespace while each game remains free
to render its own table:

```js
const game = await NekoMiniGame.connect({
  id: 'example-game',
  version: '1.0.0',
  requiredCapabilities: ['logging', 'leaderboard-local'],
  leaderboards: {
    main: {
      scoreField: 'score',
      order: 'descending',
      retention: 'recent',
      maxEntries: 50,
    },
  },
}, { transport: trustedHostTransport });

await game.leaderboard.local.submit('main', { score: 12, mode: 'duel' });
const ranked = await game.leaderboard.local.list('main', {
  sort: 'rank', limit: 20,
});
const best = await game.leaderboard.local.getBest('main');
```

This is local, personal game data. It does not claim to aggregate other N.E.K.O
installations, platform accounts or marketplace players. Each client bounds the
number of boards, retained entries, entry/state byte size and pending requests;
the trusted host additionally enforces per-game key, value and total quotas.
Local mutations for the same board are serialized across trusted game windows by
the host's origin-wide storage lock; overlapping mutation calls from one client
are still rejected with `busy`, and client disposal cancels pending storage work.
The trusted same-origin host grants `leaderboard-local` only when both local
storage and the browser Web Locks API are available, because raw read/write
storage cannot provide a safe cross-window read-modify-write contract.

The matching future service facade is already reserved as
`game.leaderboard.server.submit/list/getMyBest`. It requires the separate
`leaderboard-server` capability, an active trusted server transport, and
`runtime`; score submission is accepted by the SDK only after `runtime.end()`.
The current same-origin host deliberately does not grant this capability, and
the SDK never falls back from a server call to personal local records. A later
platform service can add account identity, reviewed score schemas, ranking,
anti-tamper checks and cross-device persistence behind that unchanged facade.

## Optional standard presentation

Loading masks and dialogue bubbles are optional renderers. A game may mount the
SDK defaults or render the same state with its own DOM, Canvas or engine UI.
The default components do not impose a particular game's layout or art direction: they use
system colors, native progress/checkbox controls, accessible live regions and
CSS custom properties prefixed with `--neko-game-`.

```js
const loading = game.presentation.loading.mount({
  container: document.querySelector('#game-root'),
  title: 'Preparing game',
  message: 'Loading character assets',
});
loading.setStage('avatar');
loading.setProgress(0.6);
loading.setMessage('Loading voice resources');

const bubble = game.presentation.bubble.mount({
  container: document.querySelector('#player-bubble-slot'),
});
bubble.show('Let’s play!', { durationMs: 4000 });
```

Games using `memory` may use the standard opening-screen consent control. It is
default-off, sends changes through `game.memory.configureConsent()`, becomes
disabled when the first start attempt locks the round choice, and resets to off
for a new runtime session. Multiple mounted standard controls stay synchronized
with direct `game.memory.configureConsent()` calls:

```js
const consent = game.presentation.memoryConsent.mount({
  container: document.querySelector('#opening-settings'),
  label: '本局对话进入记忆',
  hint: '仅在本局开始前设置',
  onError: showConfigurationError,
});
```

This renderer does not make consent optional: a game that reads or writes
persistent memory must still expose an equivalent default-off control on its
opening screen. A game with a custom renderer calls
`game.memory.configureConsent()` directly. Mounted presentation controllers are
bounded and are removed by either their own `dispose()` or `game.dispose()`;
temporary bubble timers are cleared on both paths.

## Game audio

Games can stay completely silent. Once a game emits BGM or SFX, it declares the
`audio` capability and mounts the official controller:

```js
const audio = await game.audio.mount({
  slot: 'main',
  resources: {
    bgm: { menu: ['/static/game/example/menu.mp3'] },
    loopedBgm: {
      match: {
        intro: '/static/game/example/match-intro.mp3',
        loop: '/static/game/example/match-loop.mp3',
        outro: '/static/game/example/match-outro.mp3',
      },
    },
    sfx: { kick: ['/static/game/example/kick.mp3'] },
  },
  settings: {
    fadeMs: 800,
    maxConcurrent: 12,
    maxPreloadEntries: 128,
  },
});

audio.preloadBgm('menu');
await audio.playBgm('menu');
await audio.playLoopedBgm('match');
await audio.playSfx('kick');
audio.setBgmVolume(0.45);
audio.setSfxVolume(0.75);
audio.dispose();
```

The game owns its resource table and decides which gameplay event selects each
sound. The SDK and trusted host own playback, persisted per-game volume,
autoplay unlock, concurrency, preload eviction, error normalization, and final
resource disposal. Each SDK client allows at most four mounted audio
controllers. Each controller bounds SFX concurrency, preloaded audio entries,
BGM playlist history, and BGM completion waiters.

`NekoMiniGameAudioHost` and `NekoGameSystem.GameAudioSystem` are trusted host
implementation details, not public game APIs. Attached media, TTS focus, and
reusable synthesized-speech assets require additional host contracts and are
not claimed by this first BGM/SFX stage.

## Project speech output

Games that ask N.E.K.O to speak declare `speech-output` and submit text through
the public speech facade:

```js
const unsubscribeState = game.speech.onState((state) => {
  updateSpeakingIndicator(state.active, state.remainingSeconds);
});
const unsubscribeError = game.speech.onError((error) => {
  showSpeechFailure(error.code);
});

const response = await game.speech.speak({
  text: '这一球很漂亮！',
  requestId: 'goal-7',
  source: 'quick-line',
  eventKey: 'goal:happy',
  priority: 7,
  relativeGain: 1.2,
  interruptExisting: false,
  reuseSynthesizedAudio: true,
  event: { kind: 'goal', score: [2, 1] },
}, { signal: requestAbortController.signal });

const currentState = game.speech.getState();
unsubscribeState();
unsubscribeError();
```

The SDK validates and bounds requests, injects the active session and character,
normalizes errors and responses, limits each client to four pending requests,
and keeps at most 64 request-to-playback metadata entries. The trusted host owns
the project TTS route, provider and key selection, audio delivery, global voice
volume, and playback-state bridge. Games never receive provider credentials,
raw audio chunks, or host endpoints.

The current project TTS worker protocol can emit legacy audio chunks without a
speech identifier. The host therefore serializes accepted game speech per
character and keeps at most four active-plus-waiting requests; excess work
fails with `busy`. This prevents two workers from interleaving untagged chunks
into the same playback/cache stream. `interruptExisting` is applied when the
request reaches the front of that host queue; it is not a safe preemption API
for an already running legacy worker.

When a game must mirror an assistant line into the host conversation without
playing or synthesizing audio, it uses the same official capability through
`game.speech.mirror({ text, event })`. This text-only path shares the bounded
speech request pool and trusted session/character binding; games must not call
the host mirror endpoint directly.

`relativeGain` is a per-utterance multiplier from `0` to `2`; it does not replace
the host's global voice-volume setting. `speech.speak()` and
`speech.preload()` are protected by SDK-owned cancellation and timeout races, so
their promises settle and pending slots are released even if a transport fails
to observe the supplied `AbortSignal`. The first contract has no standalone
command for stopping audio that is already playing; `interruptExisting` only
asks the host to interrupt existing speech while accepting the new utterance.

`eventKey` remains correlation metadata rather than a cache key. A game may opt
an utterance into host audio reuse with `reuseSynthesizedAudio: true`; the
default is `false`, so user-dependent dialogue is not retained accidentally.
The host keys reusable audio by the exact text plus the effective provider,
voice and language, stores only opaque hashes and audio bytes, and applies
bounded LRU/TTL eviction. Per-request `relativeGain` is still applied when a
cached utterance is replayed.

Games may preload known text without playing it:

```js
await game.speech.preload([
  '比赛开始！',
  '漂亮的一球！',
], { signal: loadingAbortController.signal });
```

The caller chooses the text, timing and whether loading should await completion.
Preloading does not play audio, show a bubble, mirror text, create a chat turn,
emit turn-end or write memory. A later `speech.speak()` for the same effective
character voice, language and exact text automatically reuses the host cache.

## Vision: text and image attachments

The optional `vision` capability analyzes supplied images or captures **one
region of the current game tab**, using the project's configured `vision` model. It requires
`runtime` and an active route. The trusted page registration must explicitly
allow `vision`; adding it to a game manifest alone does not grant it. The official
same-origin bootstrap loads `neko-minigame-vision-host.js` for such registrations.
Games must use the public facade, not the capture helper or backend endpoint.

```js
const observation = await game.vision.analyze({
  text: 'Compare the before and after states.',
  attachments: [
    { type: 'image', source: '/assets/before.png', label: 'before' },
    { type: 'image', source: canvasBlob, label: 'after' },
    // Raw bytes require an explicit MIME type:
    // { type: 'image', source: pngBytes, mimeType: 'image/png' },
  ],
}, { signal: roundAbortController.signal, timeoutMs: 60000 });
showObservation(observation.text);
```

`source` accepts a relative/HTTP(S) URL, same-origin blob URL, image data URL,
`Blob`/`File`, `Uint8Array` or `ArrayBuffer`. Supplied images do not open a screen
sharing picker. URL reads happen in the browser with CORS, no credentials,
no redirects and no referrer; resources requiring cookies fail,
never fall back to a server URL proxy. Binary sources require `mimeType`;
untyped Blobs also need it. MIME types must be `image/jpeg`, `image/png` or
`image/webp`. The server verifies the actual static format (no SVG/GIF/animated
images), strips metadata, composites transparency onto white and proportionally
reduces to at most 1280×1280 before sending to the model.

Only pass image URLs you trust. CORS controls reading the response, not whether
a simple GET can reach a local or private-network service. This browser helper
does not provide a destination allowlist or a network sandbox; omitting
credentials is not a promise of zero request-side effects. Local URLs remain
supported for trusted game assets. Do not pass untrusted user/model-generated
URLs or run adversarial games in this first-phase same-origin host. Untrusted
game containers need network policy enforced outside game-controlled code.

The attachment array preserves order in **one model request**, with optional
labels (128 characters each). A bad image fails the whole request, not a partial
analysis. Limits are 1–4 images, 2 MiB bytes per image and 6 MiB total (checked
independently for both the sources and the re-encoded JPEGs),
source dimensions at most 4096 on either axis and 4 megapixels per image,
text at most 16384 characters, and output at most 8192 characters. Results are
`{text}`; input images are not echoed. Only `type: 'image'` works today. Other
modalities are reserved for future implementations and explicitly rejected.
Neither images nor text are automatically written to chat/memory or spoken.

### Trusted backend reuse

Games with server-owned state can use the same service without moving private
rules/answers to the browser or making a preliminary image-description call:

```python
from utils.game_vision import analyze_game_vision

raw_text = await analyze_game_vision(
    text=question,
    attachments=[{"type": "image", "image_data_url": canvas_data_url, "label": "board"}],
    system_prompt=trusted_game_prompt,
    max_completion_tokens=420,
    timeout=30,
    is_current=lambda: round_is_still_current(),
)
```

This is a **trusted Python service**, not a browser system-prompt override. It
accepts validated-data-URL image attachments only, never fetches URLs, and
returns raw model text for the game to parse. Trusted system text is limited to
32768 characters, output budget to 1–4096 tokens, trusted-server timeout to >0
and ≤300 seconds (default 35). The browser HTTP endpoint keeps its fixed
55-second request deadline and default 35-second model budget; browser input
cannot override the trusted-server timeout. Default output budget is 1024 tokens. All callers share four raw
analysis slots with no waiting queue and `max_retries=0`. Image decoding and
encoding run off the event loop after admission. Cancellation settles the caller
promptly, but a running image worker or cancellation-ignoring provider retains
its raw slot until actual settlement; a retired image worker cannot start model
inference. `ValueError` reasons include `busy`, `timeout`, `route_inactive`,
`invalid_payload`, `invalid_image`, `unsupported_attachment`,
`vision_unavailable`, `invalid_model_response` and `vision_failed`.

The game retains session authorization, round/route identity, state locks,
post-await checks, parsing, judgement, attempts and explicit fallback policy.
Use `is_current` and cancel the parent task when its owning request/round ends.
The service does not grant authorization, mutate game state, retry guesses,
write logs/memory, or play speech. A text-only fallback must not be presented
as a successful visual observation.

Regression entry points: `tests/unit/test_game_vision_service.py`,
`tests/unit/test_minigame_vision.py` and
`tests/frontend/test_neko_minigame_vision_runtime.js` (also in the SDK Node pytest
wrapper). These verify contracts/resources, not a specific game's full workflow
or a paid model's answer quality.

### Game-region capture convenience

The existing `{region, prompt}` overload remains compatible and returns
`{text, width, height}`. It is mutually exclusive with `{text, attachments}`
and uses the same model service after capture. Only this overload needs current-tab
capture support and user authorization:

```js
// Add 'vision' to optionalCapabilities (and request 'runtime' as usual).
// Call from a player action: the browser requires transient user activation.
lookButton.addEventListener('click', async () => {
  if (!game.capabilities.has('vision')) return showVisionUnavailable();
  try {
    const observation = await game.vision.analyze({
      region: { kind: 'element', selector: '#game-board' },
      prompt: 'Describe the positions of the pieces on this board.',
    }, { signal: roundAbortController.signal, timeoutMs: 90000 });
    showObservation(observation.text);
  } catch (error) {
    showVisionError(error.code);
  }
});
```

Region forms (all describe an axis-aligned rectangle in the **visible game
viewport**, never physical monitor coordinates):

```js
// CSS pixels from the visible viewport's top-left; device scaling is handled internally.
{ kind: 'rect', unit: 'px', x: 100, y: 50, width: 600, height: 400 }
// Percentages from 0 to 100, independently relative to viewport width/height.
{ kind: 'rect', unit: 'percent', x: 10, y: 10, width: 80, height: 80 }
// Inward offsets from each viewport edge (also accepts unit: 'percent').
{ kind: 'edges', unit: 'px', top: 40, right: 20, bottom: 40, left: 20 }
// Four named corners; polygons and reversed corners are rejected, not expanded.
{ kind: 'corners', unit: 'percent', topLeft: { x: 10, y: 10 },
  topRight: { x: 90, y: 10 }, bottomRight: { x: 90, y: 90 }, bottomLeft: { x: 10, y: 90 } }
```

An element selector must match exactly one connected element. Capture uses its
visible bounding rectangle, **including anything visually covering it**; it is
not an isolated DOM reconstruction. Offscreen/partially clipped regions, empty
regions, ambiguous selectors and non-rectangular corners are rejected. The SDK
does not scroll, hide overlays, expand the region or capture an entire screen
as a fallback. Movement/scrolling/resizing during capture requires a retry.
Pinch-zoomed visual viewports are currently unsupported.

The player must authorize sharing **this game tab** for each request. A fresh
Capture Handle identifies the selected document; `preferCurrentTab` is only a
browser hint. Picking another tab, an application window or a monitor fails
with `capture_source_mismatch` **before pixels are read or uploaded**. This
first implementation requires a secure, top-level Chromium-compatible context
with Capture Handle and video-frame callbacks. Other browsers, embedded frames,
or Electron builds without these APIs reject the capture request with
`capture_unavailable`; supplied-image analysis remains available when browser
binary/fetch APIs are present. Electron
does not silently use native desktop capture; a future authenticated page-only
IPC adapter may implement the same public contract. The helper temporarily owns
the game document's Capture Handle configuration; do not concurrently replace it
from another capture integration.

For the capture overload, only the cropped JPEG is uploaded to the project backend and configured model
provider (normal provider cost/data policies apply). The shared stream stops
immediately after capture, **before** model inference. The SDK does not persist
images, prompts or results, append chat, write memory, speak, or run a background
capture loop. A game decides how to consume `text` using other public APIs.
`width` and `height` describe the compressed image, not the original viewport.

Bounds: one SDK vision request and one raw capture/picker per page, capture at
most 30 seconds, total SDK deadline at most 90 seconds, JPEG at most 1280×720 and
2 MiB as a data URL, prompt at most 4096 characters, result at most 8192
characters. Backend admission is four raw requests with no waiting queue;
request deadline is 55 seconds and the model client timeout is 35 seconds.
The server validates Origin/CSRF, actual image format/dimensions, active session
and route generation. It re-encodes JPEGs to drop uploaded metadata. End, reset,
disposal, disconnect, source changes and timeout discard late results and cancel
work. An uninterruptible permission picker retains its capture slot; a stream
that arrives after cancellation is immediately stopped. A provider that ignores
cancellation retains its backend slot until settlement rather than admitting
unbounded work.

Stable capture errors include `invalid_region`, `invalid_timeout`, `capture_denied`,
`capture_unavailable`, `capture_source_mismatch`, and `capture_changed`; managed
requests also use `busy`, `timeout`, `cancelled`, `disposed` and `request_failed`.
Missing model configuration is a request failure, not a fabricated observation.
Developer tests: `tests/frontend/test_neko_minigame_vision_runtime.js` and
`tests/unit/test_minigame_vision.py` cover geometry, identity, cancellation,
limits and server behavior. They do not replace a real browser/Electron picker
and configured-model acceptance test.

## Avatar renderer

The trusted launch node can register `nekoCapabilityProviders[gameId].avatarHostFactory`
before the same-origin bootstrap consumes it. The factory runs only after host
identity and constructor validation. It returns a fresh synchronous provider with
`mount(config)` and `dispose()`; each host owns exactly one provider. Optional
Avatar initialization failure leaves runtime/logging usable; a game requiring
`avatar-renderer` fails its capability handshake instead.

**New game integrations must use this trusted factory registration and the public
`game.avatar` discovery methods below.** Legacy injection and internal host
character reads are transitional compatibility for existing integrations, not
alternative recommended APIs. The existing soccer integration can continue
unchanged during this transition and will migrate separately; no removal date
is set.

Existing trusted same-origin `createNekoMiniGameSameOriginHost({ avatarHost })`
injection remains supported and takes precedence over a registered factory. No
factory is called in that case. Successful host construction transfers disposal
ownership of the injected provider, as before. The legacy host `getCharacter()`
still returns the original Response and updates its character identity; existing
adapters do not need to migrate immediately. These mechanisms are not isolation
from hostile code sharing the same origin.

The factory receives `windowImpl`, `documentImpl`, `fetchImpl`, a lifetime
`signal`, `onCleanup(fn)` and `characterSource`. Register partial allocations with
`onCleanup` immediately (at most 16 callbacks). They run on failure or disposal,
after signal cancellation. Use these callbacks for resources not already owned
by the returned provider, whose `dispose()` runs once. Factories must not share
provider instances between hosts. Async factories are unsupported; an accidentally
returned promise is observed and its eventual provider is disposed.

Public display-only role discovery uses the same `avatar-renderer` capability:

```js
const current = await game.avatar.getCurrentCharacter({ timeoutMs: 10000 });
const selected = await game.avatar.getCharacter('Neko', { signal });
const names = await game.avatar.listCharacters({ signal });
```

Descriptors contain `{ name, model: { type, path } | null, rendererAvailable }`
and optional `languagePreference: { locale, resolved }` and `fallbackModels`.
The standard source supplies both additions; older custom providers may omit
them. `resolved: true` with an empty locale means the stored preference was read
successfully and is unset; `resolved: false` (or an absent field) means unavailable,
not permission to overwrite the stored preference with a guessed locale.
Locale variants such as `zh-TW` are preserved. `fallbackModels` contains at most
four `{ type, path }` alternatives from the host's canonical model resolution,
excluding the primary model in the standard source. It is not a hard-coded
default character and does not guarantee renderer support. Select an alternative
supported by your registered renderer, or show an unavailable state if none exists.
No automatic renderer retry or global model preference write is performed.
Names are limited to 128 Unicode code points, paths to 2048, and lists to 256 names. Unknown
explicit names return `null`, not the current character. The standard host reads
the existing role registry and canonical model-path endpoints; it keeps no role
data copy and exposes no persona, memory, credentials or raw response fields.
The mount contract accepts Live2D, VRM, MMD and PNGtuber descriptors, but the
registered provider must implement the corresponding renderer. The default
character source discovers all four model types, including MMD/PNGtuber for
providers that implement mounting but delegate discovery to the built-in source.
Its availability flag reports a configured model candidate, not a guarantee that
the registered provider can load that type; mounting still validates renderer support.
Providers may supply their own display-only descriptors and availability flags.

A trusted provider may optionally implement `getCurrentCharacter(options)`,
`getCharacter(name, options)` and `listCharacters(options)`. Missing methods use
the built-in source; factory `characterSource` exposes that same source to
adapters. Forward supplied query options when using it. Each SDK client and host
limits underlying queries to four, with a 10-second default deadline and 30-second
maximum covering the full provider/body read. Cancellation, reset, route exit,
page exit and disposal settle waiting SDK promises and discard late results.
Timers and signal listeners are released; a provider ignoring abort retains its
bounded slot until it settles, preventing retries from accumulating abandoned
work. `pendingQueryCount` includes those still-settling transport calls. Discovery
is available before start, and after exit requires a new/reset lifecycle.

The bundled `NekoMiniGameDrawingAvatarHost` provider also accepts these query
options directly. Its current-role, catalog and canonical-model lookups share
one cancellation scope and total deadline. Independent callers do not share a
cancellable catalog fetch; cancelled lookups never populate its bounded model
descriptor cache or silently become a Live2D fallback result.

Discovery is read-only: looking at another character does **not** change the
game's runtime/voice identity. Bind explicitly before any pregame context,
quick-lines, preload, speech or route request:

```js
const character = await game.runtime.bindCharacter(undefined, { signal }); // current role
// Or bindCharacter(selectedName, { signal }) for an explicit selection.
if (!character) throw new Error('Selected character is unavailable');
if (!character.model || !character.rendererAvailable) {
  throw new Error('Choose a supported canonical fallback or show model unavailable');
}
await game.avatar.mount({ slot: 'opponent', characterName: character.name,
  model: character.model, viewport: { mode: 'container' } });
// Existing pregame operations and runtime.start() now use this same identity.
```

Binding requires `runtime` and `avatar-renderer`, returns the validated descriptor,
and only changes this host's local session selection. It does not start a backend
route, capture voice, or switch the main page's character. Omitted name resolves
the current character; an unknown explicit name returns `null` without mutation.
Bind only in `idle`, before character-scoped requests; after such requests or an
active route, end/reset first. While a binding is pending, another bind/start is
`busy`. Cancellation, reset, end, page exit, disposal or a changed lifecycle discard
late selection results. Binding uses the same bounded discovery request slots and
deadlines. Custom transports may optionally implement synchronous
`bindRuntimeCharacter(name)` and update `getRuntimeState()` atomically; absent
support reports `transport_unavailable`. Existing legacy adapters remain compatible.

The public game mounts an Avatar through `game.avatar`:

```js
const avatar = await game.avatar.mount({
  slot: 'opponent',
  characterName: 'Opponent Neko',
  model: { type: 'mmd', path: '/models/opponent.pmx' },
  viewport: { mode: 'fixed', width: 200, height: 300 },
  fit: {
    mode: 'contain',
    align: 'bottom-center',
    padding: 6,
    scaleMultiplier: 1,
  },
  resize: { mode: 'fixed' },
});

avatar.focus({ x: 320, y: 180 });
avatar.setEmotion('happy');
await avatar.setView({ scale: 190, x: 0, y: 28 });
await avatar.setModel({ type: 'vrm', path: '/models/opponent.vrm' });
avatar.dispose();
```

### Automatic speech mouth motion

Basic `game.speech.speak()` playback automatically drives the mounted character;
games must not maintain a separate `setSpeaking(true/false)` loop. Set
`characterName` from the public character descriptor when mounting. Only models
matching the SDK session character follow its speech. A sole unnamed model is
the default target; multiple unnamed models are not guessed to be speakers.

The SDK uses its own speech correlation and route generation, not HTTP acceptance
or a guessed text duration. The existing player bridge carries at most 256
low-frequency spectrum bytes plus RMS amplitude every 200ms while actually
playing. This works through same-document events, BroadcastChannel and the
existing single latest-state storage fallback; it does not capture the microphone
or retain audio history. It is basic sampled mouth motion, not phoneme-accurate
lip synchronization. Suspended playback, stop, cancellation, route exit, pause
and disposal stop motion; missing playback updates expire after 750ms.

Trusted renderer providers implement the optional internal
`setSpeechPlayback({ active, mouthFrame })` callback. The Avatar host runtime
forwards it; `createSpeechAnalyser()` adapts bounded snapshots for existing
renderer lip-sync engines. Each controller has one in-flight update and at most one
replaceable latest frame, rather than accumulating a playback queue. Unsupported
legacy providers remain usable but do not acquire mouth support automatically;
their renderer adapter must implement this callback once, not the game author
on every utterance. Advanced manual `setSpeaking()` remains available for
compatibility, but is unnecessary for normal SDK speech. A successful manual
`setSpeaking(true)` owns that controller until `setSpeaking(false)`, route exit
or disposal. Unrelated playback and the automatic watchdog do not cancel it;
pause/model replacement preserve the intent and resume/reload reapplies it.
`setSpeaking(false)` releases the override back to automatic SDK playback.
Only one manual update may be pending per controller (`busy` otherwise); older
automatic renderer updates settle before the manual update is applied.
Manual calls have a 10-second deadline and are cancelled on route exit or
controller disposal. Disposal of an already accepted call returns `false`.
An uncooperative renderer keeps its single raw slot until it settles (`busy`
for another manual call); timed-out waiters on automatic playback are removed.
Reapplying manual motion after model replacement/resume is best-effort and
does not delay or change the model/resume result. In particular, `await resume()`
confirms renderer resumption, not completion of an optional manual mouth update.

When the trusted host provides character discovery, games can call
`avatar.listCharacters()`, `avatar.getCurrentCharacter()` and
`avatar.getCharacter(name)` before mounting. Descriptors expose only the
character name, approved model (`live2d`, `vrm`, `mmd` or `pngtuber`) and
renderer availability. Discovery and the optional `setView`/`setSpeaking`
controller operations are feature-detected; older hosts continue to work for
games that do not call them.

Viewport and resize modes have matching values:

* `fixed`: keeps the declared width and height and installs no resize listener.
* `container`: measures the host-registered slot container and uses one bounded
  `ResizeObserver` for that mounted controller.
* `host-window`: follows the host window; all mounted controllers share one
  window resize listener.

`contain` and `cover` refit from the current rendered bounds without cumulative
scale drift. `native` uses a per-model weak baseline. Alignment supports the
nine common positions from `top-left` through `bottom-right`, including `center`.

The public SDK bounds active plus pending Avatar controllers to eight per game
client. The trusted Avatar host also has a configurable hard bound, rejects
duplicate slots, and limits each mounted controller's pending serialized
operations (16 by default). Further operations fail with `busy` instead of
retaining an unbounded promise chain. The host aborts pending mounts and engine
waits on disposal, and releases animation frames, observers, window listeners,
engine controllers, and model resources.

## Ownership and disposal

Managed context, memory, storage, leaderboard, dialogue, speech and protocol
requests include response JSON consumption in their deadlines and cancellation
scope. Pending waiters do not retire when only response headers arrive. The
same-origin REST host buffers complete responses under its fetch signal/deadline
and returns readable Response objects, preserving legacy HTTP status and clone
behavior. A timed-out/cancelled waiter settles immediately; an underlying transport
that ignores abort still occupies a bounded raw-work slot until settlement.
This also prevents repeated retries from accumulating abandoned body readers.
The host's streaming speech bridge is separate and is not buffered by this path.

Games should dispose individual controllers when a slot is permanently removed
and call `game.dispose()` when leaving the page. `game.dispose()` stops managed
runtime monitoring; aborts in-flight lifecycle, protocol, context, dialogue,
memory, storage, leaderboard and speech requests; and releases event listeners, presentation
controllers and timers, speech metadata, audio controllers, and Avatar
controllers before disposing the transport. Host disposal is idempotent,
including page-exit and partially completed mount paths.

`NekoMiniGameAvatarHost` is a trusted host helper, not a public game API. It
owns viewport measurement and resize lifecycle while N.E.K.O-owned engine
adapters provide Live2D/VRM/MMD/PNG-tuber loading, focus, emotion, pause/resume, refit, and
resource disposal for registered slots.

## Public artifacts

* `neko-minigame-sdk.js`: browser runtime and public entry.
* `neko-minigame-sdk.d.ts`: JavaScript/TypeScript public types.
* `neko-minigame-manifest.schema.json`: runtime manifest and contract schema.
* `neko-minigame-avatar-host.js` and `neko-minigame-audio-host.js`: trusted
  N.E.K.O host helpers, not APIs exposed to untrusted games.
# Avatar display sizing

## Stable 3D reference pose

Trusted VRM providers must initialize the engine with `embed: true`, as the
bundled provider does. The embedding host then owns framing: interaction bounds
continue to update, but desktop automatic FOV expansion/relaxation cannot undo
the SDK fit or deliberate cropping. Non-embedded desktop behavior is unchanged;
MMD's corresponding bounds update already leaves the camera projection alone.

VRM/MMD fitting uses a precise, model-root-local reference box rather than the
engine's cached `SkinnedMesh.boundingBox`. Animated engines may leave that box
at the original T-pose even after the arms move down. The reference is measured
once per model, then reused for resize/view changes; it does not follow each
animation frame or change bone/physics scale.

The trusted provider calls
`await NekoMiniGameAvatarHost.preparePerspectiveReference(THREE, manager, {type, signal, isCurrent})`
after loading a VRM/MMD and before the first fit. This runs the bundled `wait03`
reference animation with immediate frame-zero evaluation, updates the engine
pose, and verifies that the wrists are below the upper arms and the head is above
the hips. A name such as `idle` alone is not evidence of an arms-down pose.
The shared provider subsequently restores a separately configured presentation
idle; providers must do the same if their presentation animation differs.
Invoke preparation within the host's bounded model-load lifecycle and pass its
cancellation/identity guard; do not call it each frame or on every resize.

`fitPerspectiveModel(...).reference` reports `source`, `reason`, `animation`, and
`height` (in model-root local units). Successful reference validation reports
`standing-reference`. Missing animations/bone mappings or failed posture checks
use `current-pose-fallback`: the precisely measured pre-preparation pose, not a
claimed arms-down reference. A provider which cannot prepare a reference can
still fit; the first fit captures and freezes its current pose as a fallback.
Explicit `capturePerspectiveReference(THREE, model)` replaces that snapshot;
`releasePerspectiveReference(model, camera)` releases reference/native-camera
state on replacement/disposal. The cache has one entry per weakly held model,
stores no model reference in its value, and follows root transforms without
resampling animated vertices.

The measured height is **geometry height**, including hair and attached geometry,
not anatomical head-to-foot bone height. `height` fitting fills the padded frame
vertically and can crop wide hair/arms; `contain` preserves the whole reference
box and may show different heights for different models. Later poses may extend
beyond the reference and be clipped. This contract does not guarantee identical
visible body heights across assets and does not force games to use height mode.

The required `viewport` defines the maximum **display rectangle** in CSS pixels,
not independent scaling of the model's width and height. Pixels outside it are
clipped. Use `viewport: { mode: 'fixed', width: 200, height: 300 }` for an explicit
maximum. Existing `container` and `host-window` modes remain supported: their
measured dimensions provide the maximum instead.

```js
await game.avatar.mount({
  slot: 'character', characterName: character.name, model: character.model,
  viewport: { mode: 'fixed', width: 200, height: 300 },
  fit: { mode: 'contain', autoScale: true, minHeight: 180,
    align: 'bottom-center', padding: 6 },
});
```

* `autoScale` defaults to `true`. `contain` (default) enlarges or shrinks the
  complete model into the padded rectangle without stretching.
* `mode: 'height'` fills its height; `mode: 'width'` fills its width. The other
  axis may be clipped. `cover` fills both axes and may crop.
* Optional `minWidth` / `minHeight` are soft lower bounds, useful with a reduced
  `scaleMultiplier`. If they conflict with containment/the chosen axis, that
  maximum wins. `getState().layout.minimumSatisfied` reports the result in the
  bundled provider; it does not interrupt loading. Manual mode ignores minima.
* `autoScale: false` (or legacy `mode: 'native'`) preserves the native initial
  projected/pixel size multiplied by `scaleMultiplier`, including across resize.
  Native units differ between models; automatic sizing is recommended.
* `setView({scale: 100, x: 0, y: 0})` is a separate explicit zoom/pan override,
  expressed in percentages, applied after fit. It may intentionally crop. The
  bundled provider starts at this neutral view, not a game's saved zoom.

The trusted host's `NekoMiniGameAvatarHost` exports `fitRectangle`,
`fitLive2DModel`, and `fitPerspectiveModel(THREE, model, camera, viewport, fit, view)`.
Custom providers must execute the same policy. Nested trusted controllers can
forward `resize(viewport, fit)` to the inner host controller; silently accepting
resize without forwarding does not implement this contract. This is a trusted
provider hook, not a new unvalidated game transport operation.

The bundled multi-model provider uses embedded rendering, retaining model
content settings while excluding desktop layout preferences. Perspective fit
uses the model's world-space bounding box, not the entire scene/lights. Image
fit uses the image/canvas dimensions (including transparent padding), not an
alpha silhouette. Fit runs on load, view changes and resize, not on every 3D
animation frame; movements outside the fitted bounds can still clip. Desktop
renderer initialization remains unchanged when embedding is not requested.
