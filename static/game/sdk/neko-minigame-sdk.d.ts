export as namespace NekoMiniGame;

declare namespace NekoMiniGame {
  type JsonPrimitive = string | number | boolean | null;
  type JsonValue = JsonPrimitive | readonly JsonValue[] | { readonly [key: string]: JsonValue };
  type Capability =
    | 'runtime'
    | 'dialogue'
    | 'quick-lines'
    | 'logging'
    | 'voice-input'
    | 'avatar-renderer'
    | 'audio'
    | 'media-timeline'
    | 'speech-output'
    | 'vision'
    | 'context-read'
    | 'memory'
    | 'storage'
    | 'leaderboard-local'
    | 'leaderboard-server'
    | (string & {});
  type ContractKind = 'event' | 'state' | 'control' | 'result' | 'command';
  type ContractSchemaType =
    | 'null'
    | 'boolean'
    | 'number'
    | 'integer'
    | 'string'
    | 'array'
    | 'object';

  interface ContractSchema {
    type: ContractSchemaType;
    enum?: readonly JsonPrimitive[];
    minimum?: number;
    maximum?: number;
    minLength?: number;
    maxLength?: number;
    minItems?: number;
    maxItems?: number;
    items?: ContractDeclaration;
    properties?: Readonly<Record<string, ContractDeclaration>>;
    required?: readonly string[];
    additionalProperties?: boolean;
  }

  type ContractDeclaration = ContractSchema | readonly string[];

  interface CommandRequestSchema {
    type: 'object';
    properties?: Readonly<Record<string, ContractDeclaration>>;
    required?: readonly string[];
    additionalProperties?: boolean;
  }

  interface CommandContract {
    request: CommandRequestSchema;
    response: ContractDeclaration;
  }

  interface ManifestContracts {
    events?: Readonly<Record<string, ContractDeclaration>>;
    states?: Readonly<Record<string, ContractDeclaration>>;
    controls?: Readonly<Record<string, ContractDeclaration>>;
    results?: Readonly<Record<string, ContractDeclaration>>;
    commands?: Readonly<Record<string, CommandContract>>;
  }

  interface Manifest {
    id: string;
    version: string;
    protocolVersion?: '1';
    requiredCapabilities: readonly Capability[];
    optionalCapabilities?: readonly Capability[];
    contracts?: ManifestContracts;
    leaderboards?: Readonly<Record<string, LeaderboardDefinition>>;
  }

  interface NormalizedManifest extends Omit<Manifest, 'protocolVersion' | 'optionalCapabilities' | 'contracts' | 'leaderboards'> {
    protocolVersion: '1';
    optionalCapabilities: readonly Capability[];
    contracts: Readonly<Required<ManifestContracts>>;
    leaderboards: Readonly<Record<string, Readonly<LeaderboardDefinition>>>;
  }

  interface LeaderboardDefinition {
    scoreField?: string;
    order?: 'ascending' | 'descending';
    maxEntries?: number;
    retention?: 'best' | 'recent';
  }

  interface RegistrationIdentity {
    mode: 'registered' | 'development';
    gameId: string;
    publisherId: string;
    version: string;
  }

  interface HostInfo {
    version: string;
    protocolVersion: '1';
    registration: RegistrationIdentity;
  }

  interface HostHandshakeRequest {
    sdkVersion: string;
    protocolVersions: readonly ['1'];
    manifest: NormalizedManifest;
  }

  interface HostHandshakeResponse {
    accepted: boolean;
    protocolVersion?: '1' | string;
    hostVersion?: string;
    registration?: RegistrationIdentity;
    grantedCapabilities?: readonly Capability[];
    code?: string;
    message?: string;
  }

  /** Trusted host boundary. Game code must not implement or inspect this transport. */
  interface HostTransport {
    connectGame(
      request: HostHandshakeRequest,
      options: { signal: AbortSignal; timeoutMs: number },
    ): HostHandshakeResponse | Promise<HostHandshakeResponse>;
    dispose?(options?: { preservePendingOperations?: readonly string[] }): void;
    [key: string]: unknown;
  }

  interface ConnectOptions {
    /** Injected by the trusted N.E.K.O host, not created by the game. */
    transport: HostTransport;
    signal?: AbortSignal;
    connectTimeoutMs?: number;
    windowImpl?: Window;
    documentImpl?: Document;
  }

  interface ErrorDetails {
    operation?: string;
    capability?: string;
    [key: string]: unknown;
  }

  class Error extends globalThis.Error {
    readonly code: string;
    readonly details: ErrorDetails;
  }

  interface Response<T = Record<string, unknown>> {
    readonly ok: boolean;
    readonly status: number;
    readonly data: T;
  }

  interface RequestOptions {
    signal?: AbortSignal;
    timeoutMs?: number;
  }

  interface Capabilities {
    readonly granted: readonly Capability[];
    readonly unavailable: readonly Capability[];
    has(capability: Capability): boolean;
    require(capability: Capability): true;
  }

  type RuntimeState = 'idle' | 'starting' | 'running' | 'degraded' | 'inactive' | 'ending' | 'ended' | 'disposed';

  interface RuntimeSession {
    readonly id: string;
    readonly characterName: string;
    readonly routeInstanceId: string;
  }

  interface RuntimeEvent<T = unknown> {
    readonly protocolVersion: '1';
    readonly sequence: number;
    readonly type: string;
    readonly timestamp: number;
    readonly sessionId: string;
    readonly payload: T;
  }

  interface RuntimeConfiguration {
    /**
     * Return a bounded plain JSON object. The object type only excludes primitives;
     * arrays, functions, Date/Map objects and class instances are rejected at runtime.
     */
    payload?: () => object;
    heartbeat?: false | { intervalMs?: number; timeoutMs?: number };
    outputs?: false | { intervalMs?: number; timeoutMs?: number; limit?: number };
    /** The page-exit payload follows the same static/runtime contract as payload. */
    pageExit?: false | true | { payload?: (context: unknown) => object };
  }

  interface Runtime {
    readonly state: RuntimeState;
    readonly session: RuntimeSession;
    configure(config?: RuntimeConfiguration): Readonly<RuntimeConfiguration>;
    /**
     * `keepCharacter` re-applies the currently bound character synchronously, so a
     * replay keeps its mounted Avatars without re-binding. It is not re-resolved, and
     * every mounted or mounting Avatar must declare that same `characterName`.
     */
    reset(options?: { newSession?: boolean; keepCharacter?: boolean }): RuntimeSession;
    /** Resolve and bind locally before pregame requests. Omit name for current character. */
    bindCharacter(name?: string, options?: RequestOptions): Promise<AvatarCharacterDescriptor | null>;
    /**
     * Accepts object interfaces without index signatures. Static checking only
     * excludes primitives; runtime requires a bounded plain JSON object and rejects
     * arrays, functions, Date/Map objects and class instances with invalid_request.
     */
    start(payload?: object, options?: RequestOptions): Promise<Response>;
    /** Uses the same static/runtime payload contract as start. */
    end(payload?: object, options?: RequestOptions & { useBeacon?: boolean }): Promise<Response>;
    pulse(force?: boolean): Promise<unknown>;
    pollOutputs(): Promise<unknown>;
    startMonitoring(options?: { heartbeat?: boolean; outputs?: boolean }): void;
    stopMonitoring(): void;
  }

  interface GameProtocolEnvelope<T = JsonValue> {
    readonly protocolVersion: '1';
    readonly sequence: number;
    readonly kind: Exclude<ContractKind, 'control' | 'command'>;
    readonly type: string;
    readonly timestamp: number;
    readonly sessionId: string;
    readonly payload: T;
  }

  interface ControlEnvelope<T = JsonValue> {
    readonly protocolVersion: '1';
    readonly sequence: number;
    readonly type: string;
    readonly timestamp: number;
    readonly sessionId: string;
    readonly routeInstanceId?: string;
    readonly payload: T;
  }

  interface Events {
    readonly declared: readonly string[];
    on(type: string, handler: (event: RuntimeEvent) => void | Promise<void>): () => void;
    emit(type: string, payload: JsonValue, options?: RequestOptions): Promise<Response>;
  }

  interface StatePublisher {
    readonly declared: readonly string[];
    update(type: string, payload: JsonValue, options?: RequestOptions): Promise<Response>;
  }

  interface ResultPublisher {
    readonly declared: readonly string[];
    submit(type: string, payload: JsonValue, options?: RequestOptions): Promise<Response>;
  }

  interface Controls {
    readonly declared: readonly string[];
    readonly connected: boolean;
    on(type: string, handler: (control: ControlEnvelope) => void): () => void;
    onError(handler: (error: { code: string; message: string }) => void): () => void;
  }

  interface Commands {
    readonly declared: readonly string[];
    readonly pendingCount: number;
    execute<T extends JsonValue = JsonValue>(
      name: string,
      payload: Readonly<Record<string, JsonValue>>,
      options?: RequestOptions,
    ): Promise<Response<T>>;
  }

  interface Dialogue {
    readonly pendingCount: number;
    quickLines(
      payload?: Readonly<Record<string, JsonValue>>,
      options?: RequestOptions,
    ): Promise<Response>;
    request(
      payload?: Readonly<Record<string, JsonValue>> & {
        readonly prompt?: Readonly<{
          mode: 'author-managed';
          messages: readonly Readonly<{
            role: 'system' | 'user' | 'assistant';
            content: string;
          }>[];
        }>;
      },
      options?: RequestOptions,
    ): Promise<Response>;
  }

  interface ContextReader {
    readonly pendingCount: number;
    read(scopes: readonly string[], options?: RequestOptions): Promise<Response<JsonValue>>;
  }

  interface MemoryConsentState {
    readonly enabled: boolean;
    readonly configured: boolean;
    readonly locked: boolean;
  }

  interface MemorySubmission {
    events?: readonly JsonValue[];
    state?: JsonValue;
    result?: JsonValue;
    summary?: JsonValue;
  }

  interface Memory {
    readonly consent: MemoryConsentState;
    readonly pendingCount: number;
    configureConsent(enabled: boolean, options?: RequestOptions): Promise<Response>;
    submit(value: MemorySubmission, options?: RequestOptions): Promise<Response>;
  }

  interface Storage {
    readonly pendingCount: number;
    get(key: string, options?: RequestOptions): Promise<Response>;
    set(key: string, value: JsonValue, options?: RequestOptions): Promise<Response>;
    delete(key: string, options?: RequestOptions): Promise<Response>;
    list(
      options?: { prefix?: string; limit?: number },
      requestOptions?: RequestOptions,
    ): Promise<Response<{ ok?: boolean; keys?: readonly string[] }>>;
    clear(options: { confirm: true }, requestOptions?: RequestOptions): Promise<Response>;
  }

  interface LeaderboardEntry<T extends Readonly<Record<string, JsonValue>> = Readonly<Record<string, JsonValue>>> {
    readonly id: string;
    readonly submittedAt: number;
    readonly score: number;
    readonly data: T;
  }

  interface LeaderboardListOptions {
    sort?: 'rank' | 'recent';
    limit?: number;
    offset?: number;
    /**
     * Server leaderboard only. Forwarded to the host as the request query.
     * The local board defines no matching semantics and rejects this field
     * with `invalid_request` rather than returning an unfiltered page that
     * would look filtered.
     */
    query?: JsonValue;
  }

  interface LocalLeaderboard {
    readonly pendingCount: number;
    submit<T extends Readonly<Record<string, JsonValue>>>(
      boardId: string,
      entry: T,
      options?: RequestOptions,
    ): Promise<Response<{
      boardId: string;
      entry: LeaderboardEntry<T>;
      retained: boolean;
      rank: number | null;
      totalEntries: number;
      isPersonalBest: boolean;
    }>>;
    list<T extends Readonly<Record<string, JsonValue>> = Readonly<Record<string, JsonValue>>>(
      boardId: string,
      options?: LeaderboardListOptions,
      requestOptions?: RequestOptions,
    ): Promise<Response<{
      boardId: string;
      entries: readonly LeaderboardEntry<T>[];
      totalEntries: number;
      limit: number;
      offset: number;
      hasMore: boolean;
    }>>;
    getBest<T extends Readonly<Record<string, JsonValue>> = Readonly<Record<string, JsonValue>>>(
      boardId: string,
      options?: RequestOptions,
    ): Promise<Response<{ boardId: string; entry: LeaderboardEntry<T> | null }>>;
    clear(boardId: string, options: { confirm: true }, requestOptions?: RequestOptions): Promise<Response>;
  }

  interface ServerLeaderboard {
    readonly pendingCount: number;
    submit<T extends Readonly<Record<string, JsonValue>>>(
      boardId: string,
      entry: T,
      options?: RequestOptions,
    ): Promise<Response>;
    list(
      boardId: string,
      options?: LeaderboardListOptions,
      requestOptions?: RequestOptions,
    ): Promise<Response>;
    getMyBest(boardId: string, query?: JsonValue, options?: RequestOptions): Promise<Response>;
  }

  interface Leaderboard {
    readonly local: LocalLeaderboard;
    /** Reserved platform service facade; requires the separately granted leaderboard-server capability. */
    readonly server: ServerLeaderboard;
  }

  interface LoadingPresentationState {
    readonly stage: string;
    readonly progress: number;
    readonly visible: boolean;
    readonly error: string;
  }

  interface LoadingPresentationController {
    readonly element: HTMLElement;
    readonly disposed: boolean;
    readonly state: LoadingPresentationState;
    setStage(value: string): void;
    setProgress(value: number): void;
    setMessage(value: string): void;
    setError(value: string): void;
    show(): void;
    hide(): void;
    dispose(): void;
  }

  interface BubblePresentationController {
    readonly element: HTMLElement;
    readonly disposed: boolean;
    show(value: string, options?: { durationMs?: number }): void;
    hide(): void;
    dispose(): void;
  }

  interface MemoryConsentPresentationController {
    readonly element: HTMLLabelElement;
    readonly input: HTMLInputElement;
    readonly disposed: boolean;
    readonly enabled: boolean;
    sync(): Promise<Response>;
    dispose(): void;
  }

  interface PresentationMount<TController, TConfig> {
    readonly activeCount: number;
    mount(config: TConfig): TController;
  }

  interface Presentation {
    readonly loading: PresentationMount<LoadingPresentationController, {
      container: HTMLElement;
      title: string;
      message?: string;
      visible?: boolean;
    }>;
    readonly bubble: PresentationMount<BubblePresentationController, {
      container: HTMLElement;
      live?: 'polite' | 'assertive' | 'off';
    }>;
    readonly memoryConsent: PresentationMount<MemoryConsentPresentationController, {
      container: HTMLElement;
      label: string;
      hint?: string;
      errorMessage?: string;
      onError?: (error: Error) => void;
    }>;
  }

  interface Logger {
    configure(config?: Record<string, unknown>): unknown;
    log(...args: unknown[]): unknown;
    info(...args: unknown[]): unknown;
    warn(...args: unknown[]): unknown;
    error(...args: unknown[]): unknown;
    enable(reason?: string): Promise<unknown>;
    enableAfterRuntimeStart(): Promise<unknown>;
    flush(options?: Record<string, unknown>): Promise<unknown>;
    reset(): unknown;
  }

  interface VoiceTranscript {
    readonly text: string;
    readonly requestId: string;
    readonly source: string;
    readonly timestamp: number;
  }

  /** How a voice command relates to the host microphone start it reports on. */
  type VoiceStartAttribution = 'command' | 'observed' | 'already_active' | 'none';

  /** How the start issued by this command ended, as far as the host knows at reply time.
   * The host replies once the start has settled, or when its wait runs out; only in the
   * latter case is it `pending` (paired with `reason: 'start_pending'`, or with `started`
   * if capture committed but the start had not finished yet). `pending` is neither a
   * failure nor a cancellation: whether capture is on is `start_committed` /
   * `start_capture_live` / `host_capture_active`.
   * `superseded` means the start finished but its own capture lost to one something else owns.
   */
  type VoiceStartOutcome =
    | 'pending'
    | 'started'
    | 'superseded'
    | 'failed'
    | 'cancelled'
    | 'not_started'
    | 'not_applicable'
    | 'unknown';

  /** Host voice-control state. Command replies (`start`/`stop`/`toggle`) carry `request_id`. */
  interface VoiceControlState {
    readonly type: 'game_voice_control_state';
    /** The command result for this route. `false` with `reason: 'route_superseded'` means the
     * route the command was addressed to ended while it ran; it does NOT mean the microphone
     * is off -- read the `start_*` and `host_capture_*` fields for that. */
    readonly ok?: boolean;
    /** E.g. `started`, `stopped`, `start_failed`, `route_superseded`, `route_mismatch`, `busy`,
     * and `start_pending` (the host was still starting when its wait ran out: unknown, not failed). */
    readonly reason?: string;
    readonly request_id?: string;
    readonly available: boolean;
    readonly route_active: boolean;
    readonly game_type: string;
    readonly session_id: string;
    readonly sdk_route_instance_id: string;
    /** Voice is active for this route (always false once the route is gone). */
    readonly active: boolean;
    readonly starting: boolean;
    readonly muted: boolean;
    readonly busy: boolean;
    readonly capture_owner: 'host';
    readonly transcription_mode: string;
    readonly provider: string;
    readonly ready: boolean;
    readonly transcription_reason: string;
    /** Command replies only. */
    readonly start_attribution?: VoiceStartAttribution;
    readonly start_outcome?: VoiceStartOutcome;
    /** The capture this command started committed at some point (it may have stopped since). */
    readonly start_committed?: boolean;
    /** The capture this command started is the host's live capture right now. */
    readonly start_capture_live?: boolean;
    /** Snapshot of the host microphone, independent of route and of who started it.
     * `host_capture_active` means the host microphone is live right now (its capture pipeline,
     * or the browser recogniser while a game STT gate holds it); while the host is
     * reopening it (e.g. switching device) it reads `host_capture_starting` instead. */
    readonly host_capture_active?: boolean;
    readonly host_capture_starting?: boolean;
    readonly [key: string]: unknown;
  }

  interface VoiceInput {
    readonly connected: boolean;
    request(action: 'query' | 'start' | 'stop' | 'toggle', options?: RequestOptions): Promise<VoiceControlState>;
    query(options?: RequestOptions): Promise<VoiceControlState>;
    start(options?: RequestOptions): Promise<VoiceControlState>;
    stop(options?: RequestOptions): Promise<VoiceControlState>;
    toggle(options?: RequestOptions): Promise<VoiceControlState>;
    /** Replays the current route's verified snapshot synchronously, if available.
     * The SDK queries state after route activation; games need not call query to initialize UI.
     * Snapshots are discarded when the route leaves its active phase. Sync failures reach onError.
     */
    onState(handler: (state: VoiceControlState) => void): () => void;
    onTranscript(handler: (transcript: VoiceTranscript) => void): () => void;
    onError(handler: (error: Readonly<Record<string, unknown>>) => void): () => void;
  }

  interface SpeechRequest {
    text: string;
    requestId?: string;
    source?: string;
    eventKey?: string;
    priority?: number;
    relativeGain?: number;
    interruptExisting?: boolean;
    reuseSynthesizedAudio?: boolean;
    mirrorText?: boolean;
    emitTurnEnd?: boolean;
    reason?: string;
    language?: string;
    renderLanguage?: string;
    event?: Readonly<Record<string, JsonValue>>;
  }

  interface SpeechPreloadOptions extends RequestOptions {
    language?: string;
    renderLanguage?: string;
  }

  interface SpeechMirrorRequest {
    text: string;
    requestId?: string;
    turnId?: string;
    source?: string;
    finalizeTurn?: boolean;
    event?: Readonly<Record<string, JsonValue>>;
  }

  interface SpeechPlaybackState {
    readonly active: boolean;
    readonly speechId: string;
    readonly remainingSeconds: number;
    readonly pendingAudioWork: boolean;
    readonly priority: number | null;
    readonly requestId: string;
    readonly eventKey: string;
    readonly [key: string]: unknown;
  }

  interface SpeechOutput {
    readonly connected: boolean;
    readonly pendingCount: number;
    readonly preloadPendingCount: number;
    speak(request: SpeechRequest, options?: RequestOptions): Promise<Response>;
    mirror(request: SpeechMirrorRequest, options?: RequestOptions): Promise<Response>;
    preload(lines: string | readonly string[], options?: SpeechPreloadOptions): Promise<Response>;
    getState(): SpeechPlaybackState;
    onState(handler: (state: SpeechPlaybackState) => void): () => void;
    onError(handler: (error: Readonly<Record<string, unknown>>) => void): () => void;
  }

  interface AudioMountConfiguration {
    slot: string;
    resources?: Readonly<Record<string, JsonValue>>;
    settings?: Readonly<Record<string, JsonValue>>;
  }

  interface AudioController {
    readonly disposed: boolean;
    readonly config: Readonly<AudioMountConfiguration>;
    configure(resources: Readonly<Record<string, JsonValue>>): unknown;
    playBgm(value: JsonValue, options?: JsonValue): unknown;
    waitForBgmEnd(options?: JsonValue): unknown;
    playLoopedBgm(value: JsonValue, options?: JsonValue): unknown;
    stopLoopedBgm(options?: JsonValue): unknown;
    finishLoopedBgm(): unknown;
    playSfx(value: JsonValue, options?: JsonValue): unknown;
    preloadBgm(value: JsonValue): unknown;
    preloadLoopedBgm(value: JsonValue): unknown;
    preloadSfx(value: JsonValue): unknown;
    unloadBgm(value: JsonValue): unknown;
    unloadLoopedBgm(value: JsonValue): unknown;
    unloadSfx(value: JsonValue): unknown;
    setBgmVolume(value: number): unknown;
    getBgmVolume(): unknown;
    setSfxVolume(value: number): unknown;
    getSfxVolume(): unknown;
    getCurrentBgmSrc(): string;
    isCurrentBgm(value: JsonValue): boolean;
    onError(handler: (error: Readonly<Record<string, unknown>>) => void): () => void;
    pauseBgm(): unknown;
    resumeBgm(): unknown;
    stopBgm(): unknown;
    unlock(): unknown;
    getState(): Readonly<Record<string, unknown>>;
    dispose(): void;
  }

  interface Audio {
    readonly activeCount: number;
    mount(config: AudioMountConfiguration): Promise<AudioController>;
    disposeAll(): void;
  }

  interface AvatarModel { type: 'live2d' | 'vrm' | 'mmd' | 'pngtuber'; path: string }
  interface AvatarCharacterDescriptor {
    readonly name: string;
    readonly model: Readonly<AvatarModel> | null;
    readonly rendererAvailable: boolean;
    /** Empty locale + resolved=true means explicitly no stored preference; false means unavailable. */
    readonly languagePreference?: Readonly<{ locale: string; resolved: boolean }>;
    /** Canonical host-supplied alternatives, at most four. Never invented by the game. */
    readonly fallbackModels?: readonly Readonly<AvatarModel>[];
  }
  interface AvatarMountConfiguration {
    slot: string;
    /** Matching session characters automatically follow SDK speech playback. */
    characterName?: string;
    model: AvatarModel;
    /** Maximum display rectangle. Fixed mode requires both dimensions. */
    viewport: { mode: 'fixed'; width: number; height: number }
      | { mode: 'container' | 'host-window'; width?: number; height?: number };
    fit?: {
      /** contain is the default; width/height can crop the other axis. */
      mode?: 'contain' | 'cover' | 'native' | 'width' | 'height';
      /** Default true. False preserves native size times scaleMultiplier. */
      autoScale?: boolean;
      /** Soft CSS-pixel minima; ignored in manual/native mode. */
      minWidth?: number;
      minHeight?: number;
      align?: string;
      padding?: number;
      scaleMultiplier?: number;
    };
    resize?: { mode: 'fixed' | 'container' | 'host-window' };
  }

  interface AvatarController {
    readonly disposed: boolean;
    readonly config: Readonly<AvatarMountConfiguration>;
    setModel(model: AvatarModel): Promise<unknown>;
    setView(view: { scale: number; x: number; y: number }): Promise<unknown>;
    /** Manual override for custom rendering; not needed for normal SDK speech. */
    setSpeaking(active: boolean): Promise<unknown>;
    focus(point: { x: number; y: number }): unknown;
    setEmotion(name: string): unknown;
    pause(): unknown;
    resume(): unknown;
    getState(): Readonly<Record<string, unknown>>;
    dispose(): void;
  }

  interface Avatar {
    readonly activeCount: number;
    /** At most four queries, including transports still settling after cancellation. */
    readonly pendingQueryCount: number;
    /** Display-only metadata; default deadline 10s, maximum 30s. */
    getCurrentCharacter(options?: RequestOptions): Promise<AvatarCharacterDescriptor | null>;
    getCharacter(name: string, options?: RequestOptions): Promise<AvatarCharacterDescriptor | null>;
    listCharacters(options?: RequestOptions): Promise<readonly string[]>;
    mount(config: AvatarMountConfiguration): Promise<AvatarController>;
    disposeAll(): void;
  }

  type VisionUnit = 'px' | 'percent';
  interface VisionPoint { x: number; y: number }
  type VisionRegion =
    | { kind: 'element'; selector: string }
    | { kind: 'rect'; unit: VisionUnit; x: number; y: number; width: number; height: number }
    | { kind: 'edges'; unit: VisionUnit; top: number; right: number; bottom: number; left: number }
    | { kind: 'corners'; unit: VisionUnit; topLeft: VisionPoint; topRight: VisionPoint;
        bottomRight: VisionPoint; bottomLeft: VisionPoint };
  interface VisionResult { readonly text: string; readonly width: number; readonly height: number }
  type VisionImageMimeType = 'image/jpeg' | 'image/png' | 'image/webp';
  type VisionAttachment = {
    type: 'image'; label?: string;
  } & ({ source: string | Blob; mimeType?: VisionImageMimeType }
    | { source: Uint8Array | ArrayBuffer; mimeType: VisionImageMimeType });
  interface VisionAnalysisResult { readonly text: string }
  interface Vision {
    readonly pendingCount: number;
    /** Ordered images in one call; no screen-sharing permission for supplied images. */
    analyze(input: { text: string; attachments: readonly VisionAttachment[] }, options?: RequestOptions): Promise<VisionAnalysisResult>;
    /** One user-authorized capture, active runtime required. No automatic speech or memory writes. */
    analyze(input: { region: VisionRegion; prompt: string }, options?: RequestOptions): Promise<VisionResult>;
  }

  interface Client {
    readonly media: {
      request(action: 'history' | 'watches' | 'load' | 'watch' | 'prepare' | 'preparation' | 'character' | 'discover' | 'live', payload?: Record<string, JsonValue>): Promise<JsonValue>;
      mount(config: { video: HTMLVideoElement; job: string; version: string; signal?: AbortSignal; onEvent?: (event: any) => void; onCue?: (cue: any) => void }): Promise<{
        play(): Promise<void>; pause(): void; interrupt(): void; dispose(): void;
        /**
         * Speak a line returned by `request('live')`. Resolves 'completed' once it finished,
         * 'interrupted' when it started and a due reaction, pause, seek or disposal cut it off, or
         * 'skipped' when it never started (another line or a reaction was playing, or playback
         * paused, buffered or seeked during the fetch). Only a skipped line is safe to retry.
         */
        say(line: { text: string; audio: string; duration?: number }): Promise<'completed' | 'interrupted' | 'skipped'>;
      }>;
    };
    readonly manifest: NormalizedManifest;
    readonly host: HostInfo;
    readonly capabilities: Capabilities;
    readonly runtime: Runtime;
    readonly events: Events;
    readonly state: StatePublisher;
    readonly controls: Controls;
    readonly commands: Commands;
    readonly vision: Vision;
    readonly results: ResultPublisher;
    readonly context: ContextReader;
    readonly memory: Memory;
    readonly storage: Storage;
    readonly leaderboard: Leaderboard;
    readonly presentation: Presentation;
    readonly dialogue: Dialogue;
    readonly logger: Logger;
    readonly voice: VoiceInput;
    readonly speech: SpeechOutput;
    readonly audio: Audio;
    readonly avatar: Avatar;
    readonly disposed: boolean;
    dispose(options?: { preserveRuntimeEnd?: boolean }): void;
  }

  const version: string;
  const protocolVersion: '1';
  const supportedCapabilities: readonly Capability[];
  const mandatoryCapabilities: readonly ['logging'];
  function connect(manifest: Manifest, options: ConnectOptions): Promise<Client>;
}

export = NekoMiniGame;
