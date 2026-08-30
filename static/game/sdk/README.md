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
* `runtime`, `dialogue`, `quick-lines`, `voice-input`, `speech-output`, `audio`,
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

## Declared event, state, control and result contracts

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

The supported schema subset intentionally excludes executable or expensive
keywords such as regex patterns, `$ref`, `oneOf` and custom validators. It
supports scalar types/enums and bounded object/array composition. Undeclared
object fields are rejected unless that schema explicitly sets
`additionalProperties: true`. Contract declarations, payload size, payload
complexity, listener count and pending requests all have hard limits. The
machine-readable shape is in `neko-minigame-manifest.schema.json`. That schema
mirrors the structural, declared-type and capability-dependency checks used by
the runtime. `connect()` remains the canonical validator for dynamic
cross-field invariants that JSON Schema cannot express compactly, such as
minimum/maximum ordering, declared required-property names and aggregate
complexity limits.

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

## Avatar renderer

The public game mounts an Avatar through `game.avatar`:

```js
const avatar = await game.avatar.mount({
  slot: 'opponent',
  model: { type: 'live2d', path: '/models/opponent.model3.json' },
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
await avatar.setModel({ type: 'vrm', path: '/models/opponent.vrm' });
avatar.dispose();
```

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

Games should dispose individual controllers when a slot is permanently removed
and call `game.dispose()` when leaving the page. `game.dispose()` stops managed
runtime monitoring; aborts in-flight lifecycle, protocol, context, dialogue,
memory, storage, leaderboard and speech requests; and releases event listeners, presentation
controllers and timers, speech metadata, audio controllers, and Avatar
controllers before disposing the transport. Host disposal is idempotent,
including page-exit and partially completed mount paths.

`NekoMiniGameAvatarHost` is a trusted host helper, not a public game API. It
owns viewport measurement and resize lifecycle while N.E.K.O-owned engine
adapters provide Live2D/VRM loading, focus, emotion, pause/resume, refit, and
resource disposal for registered slots.

## Public artifacts

* `neko-minigame-sdk.js`: browser runtime and public entry.
* `neko-minigame-sdk.d.ts`: JavaScript/TypeScript public types.
* `neko-minigame-manifest.schema.json`: runtime manifest and contract schema.
* `neko-minigame-avatar-host.js` and `neko-minigame-audio-host.js`: trusted
  N.E.K.O host helpers, not APIs exposed to untrusted games.
