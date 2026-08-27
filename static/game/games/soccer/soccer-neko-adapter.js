/**
 * Temporary N.E.K.O host boundary for the soccer game.
 *
 * This is an internal migration adapter, not the public mini-game SDK. It is
 * intentionally allowed to know the current soccer REST endpoints while the
 * game is moved away from direct host requests. Soccer rules, state and UI
 * must stay outside this file and be supplied as plain payloads/callbacks.
 */
(() => {
  'use strict';

  const DEFAULT_GAME_TYPE = 'soccer';
  const DEFAULT_SOURCE = 'soccer_demo';
  const DEFAULT_HEARTBEAT_INTERVAL_MS = 2500;
  const DEFAULT_HEARTBEAT_TIMEOUT_MS = 4500;
  const DEFAULT_LOG_ENABLE_TIMEOUT_MS = 3500;
  const DEFAULT_LOG_QUEUE_LIMIT = 256;
  const DEFAULT_LOG_CONCURRENCY = 2;
  const DEFAULT_LOG_PUMP_INTERVAL_MS = 25;
  const DEFAULT_LOG_REQUEST_TIMEOUT_MS = 8000;
  const DEFAULT_LOG_AGGREGATE_LIMIT = 128;
  const DEFAULT_LOG_SUMMARY_INTERVAL_MS = 5000;
  const DEFAULT_LOG_RECOVERY_QUIET_MS = 5000;
  const DEFAULT_LOG_FLUSH_WAITER_LIMIT = 8;
  const DEFAULT_LOG_OVERFLOW_SIGNATURE_LIMIT = 64;
  const DEFAULT_REQUEST_TIMEOUT_MS = 30000;
  const DEFAULT_PENDING_REQUEST_LIMIT = 64;
  const DEFAULT_SPEECH_RESTART_DELAY_MS = 350;
  const DEFAULT_SPEECH_SLOT_LIMIT = 4;
  // Leave headroom above the host's 12s microphone start/stop confirmation so
  // its explicit failure state wins instead of racing the transport timeout.
  const DEFAULT_VOICE_CONTROL_TIMEOUT_MS = 15000;
  const DEFAULT_VOICE_CONTROL_PENDING_LIMIT = 4;

  class SoccerHostError extends Error {
    constructor(code, message, details = {}) {
      super(message);
      this.name = 'SoccerHostError';
      this.code = String(code || 'request_failed');
      this.status = Number(details.status || 0);
      this.operation = String(details.operation || 'request');
      this.requestId = String(details.requestId || '');
      if (details.cause !== undefined) this.cause = details.cause;
    }
  }

  function csrfTokenFromHeaders(headers = {}) {
    return headers['X-CSRF-Token'] || headers['x-csrf-token'] || '';
  }

  function jsonBody(payload, mutationHeaders = {}) {
    const token = csrfTokenFromHeaders(mutationHeaders);
    const bodyPayload = token ? { ...payload, _csrf_token: token } : payload;
    return JSON.stringify(bodyPayload);
  }

  function boundedPositiveInteger(value, fallback, maximum) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) return fallback;
    return Math.max(1, Math.min(Math.floor(numeric), maximum));
  }

  class SoccerNekoAdapter {
    constructor(options = {}) {
      this.gameType = String(options.gameType || DEFAULT_GAME_TYPE);
      this.source = String(options.source || DEFAULT_SOURCE);
      this._session = {
        id: `${this.gameType}_${Date.now().toString(36)}`,
        lanlanName: '',
      };
      this._fetchImpl = options.fetchImpl || window.fetch.bind(window);
      this._navigator = options.navigatorImpl || window.navigator;
      this._document = options.documentImpl || window.document;
      this._window = options.windowImpl || window;
      this._console = this._window.console || console;
      this._disposed = false;
      this._nextRequestId = 0;
      this._pendingRequestLimit = boundedPositiveInteger(
        options.pendingRequestLimit,
        DEFAULT_PENDING_REQUEST_LIMIT,
        1024,
      );
      this._pendingRequests = new Map();
      this._logTransport = {
        queue: [],
        queueLimit: boundedPositiveInteger(options.logQueueLimit, DEFAULT_LOG_QUEUE_LIMIT, 4096),
        concurrency: boundedPositiveInteger(options.logConcurrency, DEFAULT_LOG_CONCURRENCY, 16),
        pumpIntervalMs: boundedPositiveInteger(
          options.logPumpIntervalMs,
          DEFAULT_LOG_PUMP_INTERVAL_MS,
          60000,
        ),
        requestTimeoutMs: boundedPositiveInteger(
          options.logRequestTimeoutMs,
          DEFAULT_LOG_REQUEST_TIMEOUT_MS,
          60000,
        ),
        pumpTimer: null,
        inFlight: new Map(),
        nextRequestId: 0,
        flushWaiters: [],
        overflowDropped: 0,
        overflowReasons: {},
        overflowSignatures: new Set(),
        overflowContext: null,
        overflowNotified: false,
        disposed: false,
      };
      this._speechSlotLimit = boundedPositiveInteger(options.speechSlotLimit, DEFAULT_SPEECH_SLOT_LIMIT, 16);
      this._speechRecognitionSlots = new Map();
      this._heartbeat = {
        timer: null,
        controller: null,
        inFlight: false,
        startedAt: 0,
        visibilityHandler: null,
        failures: 0,
        payload: null,
        timeoutMs: DEFAULT_HEARTBEAT_TIMEOUT_MS,
        onInactive: null,
        onError: null,
        onRetryAbort: null,
      };
      this._drain = {
        timer: null,
        poll: null,
      };
      this._speechPlaybackBridge = {
        channel: null,
        storageHandler: null,
        windowEventHandler: null,
        windowEventName: '',
        onState: null,
        onError: null,
      };
      this._voiceControlBridge = {
        channel: null,
        storageHandler: null,
        storageKey: '',
        senderId: `game-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`,
        nextRequestId: 0,
        pending: new Map(),
        pendingLimit: boundedPositiveInteger(
          options.voiceControlPendingLimit,
          DEFAULT_VOICE_CONTROL_PENDING_LIMIT,
          16,
        ),
        onState: null,
        onTranscript: null,
        onError: null,
        lastState: null,
      };
      this._logger = {
        enabled: false,
        enableInFlight: false,
        enablePromise: null,
        enableGeneration: 0,
        enableTimeoutId: null,
        enableTimeoutResolve: null,
        mutationHeaders: null,
        contextProvider: null,
        enableTimeoutMs: DEFAULT_LOG_ENABLE_TIMEOUT_MS,
        aggregateLimit: DEFAULT_LOG_AGGREGATE_LIMIT,
        summaryIntervalMs: DEFAULT_LOG_SUMMARY_INTERVAL_MS,
        recoveryQuietMs: DEFAULT_LOG_RECOVERY_QUIET_MS,
        maintenanceTimer: null,
        aggregates: new Map(),
        originalWarn: null,
        originalError: null,
        consoleWarnHandler: null,
        consoleErrorHandler: null,
        windowErrorHandler: null,
        rejectionHandler: null,
      };
      this.logger = Object.freeze({
        log: this.log.bind(this),
        info: (category, event, message, details = {}, sensitivePossible = false, logOptions = {}) => (
          this.log('info', category, event, message, details, sensitivePossible, logOptions)
        ),
        warn: (category, event, message, details = {}, sensitivePossible = false, logOptions = {}) => (
          this.log('warning', category, event, message, details, sensitivePossible, logOptions)
        ),
        error: (category, event, message, details = {}, sensitivePossible = false, logOptions = {}) => (
          this.log('error', category, event, message, details, sensitivePossible, logOptions)
        ),
        enable: this.enableLogger.bind(this),
        enableAfterRouteStart: this.enableLoggerAfterRouteStart.bind(this),
        flush: this.flushLogger.bind(this),
        reset: this.resetLogger.bind(this),
      });
    }

    _gameEndpoint(path) {
      return `/api/game/${encodeURIComponent(this.gameType)}/${path}`;
    }

    _hostError(code, message, details = {}) {
      return new SoccerHostError(code, message, details);
    }

    _requestId(operation = 'request') {
      this._nextRequestId = (this._nextRequestId + 1) % Number.MAX_SAFE_INTEGER;
      return `${operation}-${Date.now().toString(36)}-${this._nextRequestId.toString(36)}`;
    }

    async _request(url, init = {}, options = {}) {
      const operation = String(options.operation || 'request');
      if (this._disposed) {
        throw this._hostError('disposed', 'Soccer host adapter has been disposed', { operation });
      }
      if (this._pendingRequests.size >= this._pendingRequestLimit) {
        throw this._hostError('busy', 'Soccer host pending request limit reached', { operation });
      }

      const requestId = this._requestId(operation);
      const AbortControllerImpl = this._window.AbortController || globalThis.AbortController;
      if (typeof AbortControllerImpl !== 'function') {
        throw this._hostError('unsupported', 'AbortController is unavailable', { operation, requestId });
      }
      const controller = new AbortControllerImpl();
      const externalSignal = options.signal || init.signal || null;
      const timeoutMs = Math.max(1, Number(options.timeoutMs || DEFAULT_REQUEST_TIMEOUT_MS));
      const entry = {
        operation,
        controller,
        timeoutId: null,
        externalSignal,
        externalAbortHandler: null,
        cancelReason: '',
      };

      if (externalSignal?.aborted) {
        throw this._hostError('cancelled', 'Soccer host request was cancelled', { operation, requestId });
      }
      if (externalSignal && typeof externalSignal.addEventListener === 'function') {
        entry.externalAbortHandler = () => {
          if (!entry.cancelReason) entry.cancelReason = 'cancelled';
          try { controller.abort(); } catch (_) { /* already aborted */ }
        };
        externalSignal.addEventListener('abort', entry.externalAbortHandler, { once: true });
      }
      entry.timeoutId = this._window.setTimeout(() => {
        if (!entry.cancelReason) entry.cancelReason = 'timeout';
        try { controller.abort(); } catch (_) { /* already aborted */ }
      }, timeoutMs);
      this._pendingRequests.set(requestId, entry);

      try {
        return await this._fetchImpl(url, { ...init, signal: controller.signal });
      } catch (error) {
        const code = entry.cancelReason || (error?.name === 'AbortError' ? 'cancelled' : 'network_error');
        const message = code === 'timeout'
          ? `Soccer host request timed out after ${timeoutMs}ms`
          : code === 'disposed'
            ? 'Soccer host adapter was disposed during request'
            : code === 'cancelled'
              ? 'Soccer host request was cancelled'
              : 'Soccer host request failed';
        throw this._hostError(code, message, { operation, requestId, cause: error });
      } finally {
        if (entry.timeoutId != null) this._window.clearTimeout(entry.timeoutId);
        if (entry.externalSignal && entry.externalAbortHandler) {
          entry.externalSignal.removeEventListener?.('abort', entry.externalAbortHandler);
        }
        this._pendingRequests.delete(requestId);
      }
    }

    cancelPendingRequests(reason = 'cancelled', options = {}) {
      const normalizedReason = reason === 'disposed' ? 'disposed' : 'cancelled';
      const preserveOperations = options.preserveOperations instanceof Set
        ? options.preserveOperations
        : new Set(options.preserveOperations || []);
      for (const entry of this._pendingRequests.values()) {
        if (preserveOperations.has(entry.operation)) continue;
        entry.cancelReason = normalizedReason;
        try { entry.controller.abort(); } catch (_) { /* already aborted */ }
      }
    }

    get sessionId() {
      return this._session.id;
    }

    get routeLanlanName() {
      return this._session.lanlanName;
    }

    resetSession({ newSession = false } = {}) {
      if (newSession || !this._session.id) {
        this._cancelVoiceControlRequests('cancelled');
        this._session.id = `${this.gameType}_${Date.now().toString(36)}`;
      }
      this._session.lanlanName = '';
      return { sessionId: this.sessionId, lanlanName: this.routeLanlanName };
    }

    applyRouteState(state = {}) {
      const lanlanName = String(state?.lanlan_name || '').trim();
      if (lanlanName) this._session.lanlanName = lanlanName;
      return { sessionId: this.sessionId, lanlanName: this.routeLanlanName };
    }

    _post(path, payload, options = {}) {
      return this._request(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        body: typeof payload === 'string' ? payload : JSON.stringify(payload),
        ...(options.credentials ? { credentials: options.credentials } : {}),
        ...(options.keepalive ? { keepalive: true } : {}),
      }, {
        operation: options.operation || 'post',
        timeoutMs: options.timeoutMs,
        signal: options.signal,
      });
    }

    getCharacter(lanlanName = '') {
      const url = new URL(this._gameEndpoint('character'), this._window.location.origin);
      if (lanlanName && lanlanName !== 'soccer_demo') {
        url.searchParams.set('lanlan_name', lanlanName);
      }
      return this._request(url.toString(), {}, {
        operation: 'character',
        timeoutMs: 10000,
      });
    }

    getQuickLines(payload, options = {}) {
      return this._post(this._gameEndpoint('quick-lines'), payload, {
        timeoutMs: 15000,
        operation: 'quick_lines',
        ...options,
      });
    }

    requestDialogue(payload, options = {}) {
      return this._post(this._gameEndpoint('chat'), payload, {
        timeoutMs: 60000,
        operation: 'dialogue',
        ...options,
      });
    }

    evaluatePassiveGuard(payload, options = {}) {
      return this._post(this._gameEndpoint('passive-guard'), payload, {
        timeoutMs: 15000,
        operation: 'passive_guard',
        ...options,
      });
    }

    start(payload, options = {}) {
      return this._post(this._gameEndpoint('route/start'), payload, {
        timeoutMs: 60000,
        operation: 'route_start',
        ...options,
      });
    }

    heartbeat(payload, options = {}) {
      return this._post(this._gameEndpoint('route/heartbeat'), payload, {
        timeoutMs: DEFAULT_HEARTBEAT_TIMEOUT_MS,
        operation: 'route_heartbeat',
        ...options,
      });
    }

    drain(payload, options = {}) {
      return this._post(this._gameEndpoint('route/drain'), payload, {
        timeoutMs: 8000,
        operation: 'route_drain',
        ...options,
      });
    }

    submitVoiceTranscript(payload, options = {}) {
      return this._post(this._gameEndpoint('route/voice-transcript'), payload, {
        timeoutMs: 15000,
        operation: 'voice_transcript',
        ...options,
      });
    }

    sendRealtimeContext(payload, options = {}) {
      return this._post(this._gameEndpoint('realtime-context'), payload, {
        timeoutMs: 15000,
        operation: 'realtime_context',
        ...options,
        credentials: 'same-origin',
      });
    }

    mirrorAssistant(payload, options = {}) {
      return this._post(this._gameEndpoint('mirror-assistant'), payload, {
        timeoutMs: 15000,
        operation: 'mirror_assistant',
        ...options,
      });
    }

    speak(payload, options = {}) {
      return this._post(this._gameEndpoint('speak'), payload, {
        timeoutMs: 60000,
        operation: 'speak',
        ...options,
      });
    }

    getPageConfig(lanlanName = '') {
      const suffix = lanlanName ? `?lanlan_name=${encodeURIComponent(lanlanName)}` : '';
      return this._request(`/api/config/page_config${suffix}`, {
        credentials: 'same-origin',
        cache: 'no-store',
      }, {
        operation: 'page_config',
        timeoutMs: 10000,
      });
    }

    getMutationHeaders() {
      const headers = { 'Content-Type': 'application/json' };
      const security = this._window.nekoLocalMutationSecurity;
      if (security && typeof security.getMutationHeaders === 'function') {
        return Promise.resolve(security.getMutationHeaders())
          .then((mutationHeaders) => Object.assign(headers, mutationHeaders || {}))
          .catch(() => headers);
      }

      const lanlanName = this._window.lanlan_config?.lanlan_name || '';
      return this.getPageConfig(lanlanName)
        .then((response) => response.ok ? response.json() : null)
        .then((config) => {
          if (config && typeof config.autostart_csrf_token === 'string' && config.autostart_csrf_token) {
            headers['X-CSRF-Token'] = config.autostart_csrf_token;
          }
          return headers;
        })
        .catch(() => headers);
    }

    refreshMutationHeaders() {
      const security = this._window.nekoLocalMutationSecurity;
      if (security && typeof security.refreshToken === 'function') {
        return Promise.resolve(security.refreshToken())
          .then(() => this.getMutationHeaders())
          .catch(() => this.getMutationHeaders());
      }
      return this.getMutationHeaders();
    }

    async withCsrfRetry(requestWithHeaders) {
      let response = await requestWithHeaders(await this.getMutationHeaders());
      if (response.status !== 403) return response;
      const errorPayload = await response.clone().json().catch(() => ({}));
      if (errorPayload?.error_code !== 'csrf_validation_failed') return response;
      response = await requestWithHeaders(await this.refreshMutationHeaders());
      return response;
    }

    sendRealtimeContextWithCsrf(payload) {
      return this.withCsrfRetry((headers) => this.sendRealtimeContext(payload, { headers }));
    }

    startSpeechPlaybackBridge(options = {}) {
      this.stopSpeechPlaybackBridge();
      const bridge = this._speechPlaybackBridge;
      const storageKey = String(options.storageKey || 'neko_speech_playback_state');
      const channelName = String(options.channelName || 'neko_speech_playback_channel');
      const eventName = String(options.eventName || 'neko-speech-playback-state');
      const messageType = String(options.messageType || 'speech_playback_state');
      bridge.onState = typeof options.onState === 'function' ? options.onState : null;
      bridge.onError = typeof options.onError === 'function' ? options.onError : null;

      const acceptState = (data, source) => {
        if (data?.type !== messageType || !bridge.onState) return;
        try {
          bridge.onState(data, source);
        } catch (error) {
          bridge.onError?.(error, source);
        }
      };
      const BroadcastChannelImpl = options.BroadcastChannelImpl || this._window.BroadcastChannel;
      if (typeof BroadcastChannelImpl === 'function') {
        try {
          bridge.channel = new BroadcastChannelImpl(channelName);
          bridge.channel.onmessage = (event) => acceptState(event?.data, 'broadcast_channel');
        } catch (error) {
          bridge.channel = null;
          bridge.onError?.(error, 'broadcast_channel');
        }
      }

      bridge.storageHandler = (event) => {
        if (event.key !== storageKey || !event.newValue) return;
        try {
          acceptState(JSON.parse(event.newValue), 'local_storage');
        } catch (_) { /* ignore malformed state from unrelated/older writers */ }
      };
      bridge.windowEventHandler = (event) => acceptState(event?.detail, 'window_event');
      bridge.windowEventName = eventName;
      this._window.addEventListener('storage', bridge.storageHandler);
      this._window.addEventListener(eventName, bridge.windowEventHandler);
    }

    stopSpeechPlaybackBridge() {
      const bridge = this._speechPlaybackBridge;
      if (bridge.storageHandler) {
        this._window.removeEventListener('storage', bridge.storageHandler);
        bridge.storageHandler = null;
      }
      if (bridge.windowEventHandler) {
        this._window.removeEventListener(bridge.windowEventName, bridge.windowEventHandler);
        bridge.windowEventHandler = null;
        bridge.windowEventName = '';
      }
      if (bridge.channel) {
        bridge.channel.onmessage = null;
        try { bridge.channel.close(); } catch (_) { /* already closed */ }
        bridge.channel = null;
      }
      bridge.onState = null;
      bridge.onError = null;
    }

    startVoiceControlBridge(options = {}) {
      this.stopVoiceControlBridge('restarted');
      if (this._disposed) throw this._hostError('disposed', 'Soccer host adapter has been disposed');
      const bridge = this._voiceControlBridge;
      const channelName = String(options.channelName || 'neko_game_voice_control_channel');
      bridge.storageKey = String(options.storageKey || 'neko_game_voice_control_message');
      bridge.onState = typeof options.onState === 'function' ? options.onState : null;
      bridge.onTranscript = typeof options.onTranscript === 'function' ? options.onTranscript : null;
      bridge.onError = typeof options.onError === 'function' ? options.onError : null;

      const acceptMessage = (data, source) => {
        if (!data || !['game_voice_control_state', 'game_voice_transcript'].includes(data.type)) return;
        if (String(data.game_type || '') !== this.gameType) return;
        if (data.session_id && String(data.session_id) !== this.sessionId) return;
        if (data.type === 'game_voice_transcript') {
          const text = String(data.text || '').trim();
          if (!text) return;
          try {
            bridge.onTranscript?.({ ...data, text }, source);
          } catch (error) {
            bridge.onError?.(error, source);
          }
          return;
        }
        bridge.lastState = data;
        const requestId = String(data.request_id || '');
        const pending = requestId ? bridge.pending.get(requestId) : null;
        if (pending && data.reason !== 'working') {
          this._window.clearTimeout(pending.timeoutId);
          bridge.pending.delete(requestId);
          pending.resolve(data);
        }
        try {
          bridge.onState?.(data, source);
        } catch (error) {
          bridge.onError?.(error, source);
        }
      };

      const BroadcastChannelImpl = options.BroadcastChannelImpl || this._window.BroadcastChannel;
      if (typeof BroadcastChannelImpl === 'function') {
        try {
          bridge.channel = new BroadcastChannelImpl(channelName);
          bridge.channel.onmessage = (event) => acceptMessage(event?.data, 'broadcast_channel');
        } catch (error) {
          bridge.channel = null;
          bridge.onError?.(error, 'broadcast_channel');
        }
      }

      bridge.storageHandler = (event) => {
        if (!event || event.key !== bridge.storageKey || !event.newValue) return;
        try {
          acceptMessage(JSON.parse(event.newValue), 'local_storage');
        } catch (_) { /* ignore malformed coordination messages */ }
      };
      this._window.addEventListener('storage', bridge.storageHandler);
      return !!bridge.channel || typeof this._window.localStorage !== 'undefined';
    }

    _postVoiceControlMessage(payload) {
      const bridge = this._voiceControlBridge;
      if (bridge.channel) {
        bridge.channel.postMessage(payload);
        return true;
      }
      try {
        const serialized = JSON.stringify({
          ...payload,
          storage_nonce: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
        });
        this._window.localStorage.setItem(bridge.storageKey, serialized);
        this._window.setTimeout(() => {
          try {
            if (this._window.localStorage.getItem(bridge.storageKey) === serialized) {
              this._window.localStorage.removeItem(bridge.storageKey);
            }
          } catch (_) { /* best-effort transient message cleanup */ }
        }, 0);
        return true;
      } catch (error) {
        bridge.onError?.(error, 'local_storage');
        return false;
      }
    }

    requestVoiceControl(action = 'query', options = {}) {
      const bridge = this._voiceControlBridge;
      if (this._disposed) {
        return Promise.reject(this._hostError('disposed', 'Soccer host adapter has been disposed', {
          operation: 'voice_control',
        }));
      }
      if (!bridge.channel && !bridge.storageHandler) {
        return Promise.reject(this._hostError('unsupported', 'Voice control bridge is not started', {
          operation: 'voice_control',
        }));
      }
      if (bridge.pending.size >= bridge.pendingLimit) {
        return Promise.reject(this._hostError('busy', 'Voice control request limit reached', {
          operation: 'voice_control',
        }));
      }
      const normalizedAction = String(action || 'query');
      if (!['query', 'start', 'stop', 'toggle'].includes(normalizedAction)) {
        return Promise.reject(this._hostError('invalid_request', 'Unknown voice control action', {
          operation: 'voice_control',
        }));
      }

      bridge.nextRequestId = (bridge.nextRequestId + 1) % Number.MAX_SAFE_INTEGER;
      const requestId = `voice-${Date.now().toString(36)}-${bridge.nextRequestId.toString(36)}`;
      const timeoutMs = Math.max(500, Number(options.timeoutMs || DEFAULT_VOICE_CONTROL_TIMEOUT_MS));
      return new Promise((resolve, reject) => {
        const timeoutId = this._window.setTimeout(() => {
          bridge.pending.delete(requestId);
          reject(this._hostError('timeout', 'Voice control request timed out', {
            operation: 'voice_control',
            requestId,
          }));
        }, timeoutMs);
        bridge.pending.set(requestId, { resolve, reject, timeoutId });
        const posted = this._postVoiceControlMessage({
          type: 'game_voice_control_request',
          sender_id: bridge.senderId,
          request_id: requestId,
          timestamp: Date.now(),
          action: normalizedAction,
          game_type: this.gameType,
          session_id: this.sessionId,
        });
        if (!posted) {
          this._window.clearTimeout(timeoutId);
          bridge.pending.delete(requestId);
          reject(this._hostError('unsupported', 'Voice control transport is unavailable', {
            operation: 'voice_control',
            requestId,
          }));
        }
      });
    }

    _cancelVoiceControlRequests(reason = 'cancelled') {
      const bridge = this._voiceControlBridge;
      for (const [requestId, pending] of bridge.pending.entries()) {
        this._window.clearTimeout(pending.timeoutId);
        pending.reject(this._hostError(reason === 'disposed' ? 'disposed' : 'cancelled', 'Voice control request was cancelled', {
          operation: 'voice_control',
          requestId,
        }));
      }
      bridge.pending.clear();
    }

    stopVoiceControlBridge(reason = 'cancelled') {
      const bridge = this._voiceControlBridge;
      if (bridge.storageHandler) {
        this._window.removeEventListener('storage', bridge.storageHandler);
        bridge.storageHandler = null;
      }
      if (bridge.channel) {
        bridge.channel.onmessage = null;
        try { bridge.channel.close(); } catch (_) { /* already closed */ }
        bridge.channel = null;
      }
      this._cancelVoiceControlRequests(reason);
      bridge.storageKey = '';
      bridge.onState = null;
      bridge.onTranscript = null;
      bridge.onError = null;
      bridge.lastState = null;
    }

    isSpeechRecognitionSupported(options = {}) {
      const RecognitionImpl = options.RecognitionImpl ||
        this._window.SpeechRecognition ||
        this._window.webkitSpeechRecognition;
      return typeof RecognitionImpl === 'function';
    }

    startSpeechRecognition(name, options = {}) {
      const slotName = String(name || '').trim();
      if (!slotName) throw this._hostError('invalid_request', 'Speech recognition slot name is required');
      if (this._disposed) throw this._hostError('disposed', 'Soccer host adapter has been disposed');

      let slot = this._speechRecognitionSlots.get(slotName);
      if (!slot) {
        if (this._speechRecognitionSlots.size >= this._speechSlotLimit) {
          throw this._hostError('busy', 'Soccer host speech recognition slot limit reached', {
            operation: 'speech_recognition',
          });
        }
        const RecognitionImpl = options.RecognitionImpl ||
          this._window.SpeechRecognition ||
          this._window.webkitSpeechRecognition;
        if (typeof RecognitionImpl !== 'function') {
          options.onUnsupported?.();
          return false;
        }
        const recognition = new RecognitionImpl();
        slot = {
          recognition,
          options: {},
          active: false,
          listening: false,
          stopping: false,
          restartTimer: null,
        };
        this._speechRecognitionSlots.set(slotName, slot);

        recognition.onstart = (event) => {
          slot.listening = true;
          slot.stopping = false;
          slot.options.onStart?.(event);
        };
        for (const eventName of ['audiostart', 'soundstart', 'speechstart', 'speechend', 'soundend', 'audioend']) {
          recognition[`on${eventName}`] = (event) => slot.options.onLifecycle?.(eventName, event);
        }
        recognition.onnomatch = (event) => slot.options.onNoMatch?.(event);
        recognition.onresult = (event) => {
          const results = event?.results || [];
          const startIndex = slot.options.finalOnly === false
            ? 0
            : (typeof event?.resultIndex === 'number' ? event.resultIndex : 0);
          let transcript = '';
          for (let i = startIndex; i < results.length; i++) {
            const result = results[i];
            if (!result || (slot.options.finalOnly !== false && result.isFinal === false)) continue;
            transcript += result[0]?.transcript || '';
          }
          transcript = transcript.trim();
          slot.options.onResult?.(event, transcript);
          if (transcript) slot.options.onTranscript?.(transcript, event);
        };
        recognition.onerror = (event) => {
          const errorCode = String(event?.error || 'unknown');
          if (errorCode === 'not-allowed' || errorCode === 'service-not-allowed') {
            slot.active = false;
            slot.stopping = true;
          }
          slot.options.onError?.(errorCode, event);
        };
        recognition.onend = (event) => {
          slot.listening = false;
          if (slot.restartTimer != null) {
            this._window.clearTimeout(slot.restartTimer);
            slot.restartTimer = null;
          }
          slot.options.onEnd?.(event);
          const autoRestart = typeof slot.options.autoRestart === 'function'
            ? !!slot.options.autoRestart()
            : !!slot.options.autoRestart;
          if (slot.active && !this._disposed && !slot.stopping && autoRestart) {
            const delayMs = Math.max(
              0,
              Number(slot.options.restartDelayMs ?? DEFAULT_SPEECH_RESTART_DELAY_MS),
            );
            slot.restartTimer = this._window.setTimeout(() => {
              slot.restartTimer = null;
              if (slot.active && !this._disposed && !slot.stopping) {
                this.startSpeechRecognition(slotName, slot.options);
              }
            }, delayMs);
          }
          slot.stopping = false;
        };
      }

      slot.options = { ...slot.options, ...options };
      slot.active = true;
      const recognition = slot.recognition;
      recognition.lang = String(slot.options.lang || recognition.lang || '');
      recognition.continuous = slot.options.continuous !== false;
      recognition.interimResults = !!slot.options.interimResults;
      recognition.maxAlternatives = Math.max(1, Number(slot.options.maxAlternatives || 1));
      if (slot.listening) {
        slot.options.onAlreadyRunning?.();
        return true;
      }

      try {
        slot.stopping = false;
        recognition.start();
        slot.listening = true;
        slot.options.onStartRequest?.();
        return true;
      } catch (error) {
        if (error?.name === 'InvalidStateError') {
          slot.listening = true;
          slot.options.onAlreadyRunning?.();
          return true;
        }
        slot.listening = false;
        slot.active = false;
        slot.options.onStartError?.(error);
        return false;
      }
    }

    stopSpeechRecognition(name, options = {}) {
      const slotName = String(name || '').trim();
      const slot = this._speechRecognitionSlots.get(slotName);
      if (!slot) return;
      slot.active = false;
      slot.stopping = true;
      if (slot.restartTimer != null) {
        this._window.clearTimeout(slot.restartTimer);
        slot.restartTimer = null;
      }
      if (slot.recognition) {
        try {
          if (options.abort) slot.recognition.abort();
          else slot.recognition.stop();
        } catch (_) {
          try { slot.recognition.abort(); } catch (_) { /* already stopped */ }
        }
      }
      slot.listening = false;
      if (options.release) this.releaseSpeechRecognition(slotName);
    }

    releaseSpeechRecognition(name) {
      const slotName = String(name || '').trim();
      const slot = this._speechRecognitionSlots.get(slotName);
      if (!slot) return;
      if (slot.restartTimer != null) this._window.clearTimeout(slot.restartTimer);
      const recognition = slot.recognition;
      if (recognition) {
        for (const eventName of [
          'start', 'audiostart', 'soundstart', 'speechstart', 'speechend',
          'soundend', 'audioend', 'nomatch', 'result', 'error', 'end',
        ]) {
          recognition[`on${eventName}`] = null;
        }
      }
      slot.options = {};
      slot.recognition = null;
      this._speechRecognitionSlots.delete(slotName);
    }

    stopAllSpeechRecognition() {
      for (const slotName of Array.from(this._speechRecognitionSlots.keys())) {
        this.stopSpeechRecognition(slotName, { abort: true, release: true });
      }
    }

    postLog(payload, mutationHeaders = {}) {
      let body = '';
      try {
        body = jsonBody(payload, mutationHeaders);
      } catch (error) {
        this._recordLogTransportOverflow(payload, 'serialization_failed');
        return Promise.resolve({ ok: false, reason: 'serialization_failed', error });
      }
      return this._enqueueLogRequest({ payload, body, headers: { ...mutationHeaders } });
    }

    _enqueueLogRequest(item) {
      const transport = this._logTransport;
      if (transport.disposed) return Promise.resolve({ ok: false, reason: 'disposed' });
      if (transport.queue.length + transport.inFlight.size >= transport.queueLimit) {
        this._recordLogTransportOverflow(item.payload, 'queue_capacity');
        return Promise.resolve({ ok: false, reason: 'queue_overflow' });
      }
      return new Promise((resolve) => {
        transport.queue.push({ ...item, resolve });
        this._scheduleLogPump();
      });
    }

    _scheduleLogPump(delayMs = this._logTransport.pumpIntervalMs) {
      const transport = this._logTransport;
      if (transport.disposed || transport.pumpTimer != null || !transport.queue.length) return;
      transport.pumpTimer = this._window.setTimeout(() => {
        transport.pumpTimer = null;
        this._pumpLogQueue();
      }, Math.max(0, Number(delayMs || 0)));
    }

    _tryLogBeacon(item) {
      try {
        if (!this._navigator.sendBeacon) return false;
        return !!this._navigator.sendBeacon(
          '/api/game/logs',
          new Blob([item.body], { type: 'application/json' }),
        );
      } catch (_) {
        return false;
      }
    }

    _startLogFetch(item) {
      const transport = this._logTransport;
      transport.nextRequestId = (transport.nextRequestId + 1) % Number.MAX_SAFE_INTEGER;
      const requestId = `log-${Date.now().toString(36)}-${transport.nextRequestId.toString(36)}`;
      const AbortControllerImpl = this._window.AbortController || globalThis.AbortController;
      if (typeof AbortControllerImpl !== 'function') {
        item.resolve({ ok: false, reason: 'unsupported' });
        this._recordLogTransportOverflow(item.payload, 'abort_controller_unavailable');
        this._queueLogOverflowSummary();
        this._resolveLogFlushWaiters();
        return false;
      }
      const controller = new AbortControllerImpl();
      const timeoutId = this._window.setTimeout(() => {
        try { controller.abort(); } catch (_) { /* already aborted */ }
      }, transport.requestTimeoutMs);
      transport.inFlight.set(requestId, { controller, timeoutId, resolve: item.resolve });
      Promise.resolve()
        .then(() => this._fetchImpl('/api/game/logs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(item.headers || {}) },
          body: item.body,
          keepalive: true,
          signal: controller.signal,
        }))
        .then((response) => item.resolve(response))
        .catch((error) => item.resolve({ ok: false, reason: 'request_failed', error }))
        .finally(() => {
          this._window.clearTimeout(timeoutId);
          transport.inFlight.delete(requestId);
          this._queueLogOverflowSummary();
          this._pumpLogQueue({ force: true });
        });
      return true;
    }

    _pumpLogQueue({ force = false } = {}) {
      const transport = this._logTransport;
      if (transport.disposed) return;
      if (transport.pumpTimer != null) {
        this._window.clearTimeout(transport.pumpTimer);
        transport.pumpTimer = null;
      }
      this._queueLogOverflowSummary();
      let sent = 0;
      const budget = force ? transport.queueLimit : 1;
      while (
        transport.queue.length
        && transport.inFlight.size < transport.concurrency
        && sent < budget
      ) {
        const item = transport.queue.shift();
        sent += 1;
        if (this._tryLogBeacon(item)) {
          item.resolve({ ok: true, beacon: true });
          this._queueLogOverflowSummary();
          continue;
        }
        if (!this._startLogFetch(item)) continue;
      }
      if (transport.queue.length) this._scheduleLogPump();
      this._resolveLogFlushWaiters();
    }

    _recordLogTransportOverflow(payload, reason) {
      const transport = this._logTransport;
      transport.overflowDropped = Math.min(Number.MAX_SAFE_INTEGER, transport.overflowDropped + 1);
      transport.overflowReasons[reason] = Math.min(
        Number.MAX_SAFE_INTEGER,
        Number(transport.overflowReasons[reason] || 0) + 1,
      );
      if (!transport.overflowContext && payload && typeof payload === 'object') {
        transport.overflowContext = {
          session_id: String(payload.session_id || this.sessionId || ''),
          game_type: String(payload.game_type || this.gameType),
          lanlan_name: String(payload.lanlan_name || this.routeLanlanName || ''),
          source: String(payload.source || this.source),
        };
      }
      if (transport.overflowSignatures.size < DEFAULT_LOG_OVERFLOW_SIGNATURE_LIMIT) {
        transport.overflowSignatures.add(this._logSignature(payload || {}));
      }
      if (!transport.overflowNotified) {
        transport.overflowNotified = true;
        this._logger.originalWarn?.call(
          this._console,
          '[Soccer] [SessionLog] 联合调试日志队列已满，后续数量将通过 overflow 汇总上报',
        );
      }
    }

    _queueLogOverflowSummary() {
      const transport = this._logTransport;
      if (!transport.overflowDropped || transport.disposed) return;
      if (transport.queue.length + transport.inFlight.size >= transport.queueLimit) return;
      const context = transport.overflowContext || {};
      const droppedCount = transport.overflowDropped;
      const reasons = { ...transport.overflowReasons };
      const distinctSignatureCount = transport.overflowSignatures.size;
      transport.overflowDropped = 0;
      transport.overflowReasons = {};
      transport.overflowSignatures.clear();
      transport.overflowContext = null;
      transport.overflowNotified = false;
      const payload = {
        session_id: context.session_id || this.sessionId,
        game_type: context.game_type || this.gameType,
        lanlan_name: context.lanlan_name || this.routeLanlanName,
        source: context.source || this.source,
        level: 'warning',
        category: 'logger',
        event: 'log_queue_overflow',
        message: `联合调试日志发送队列已满，${droppedCount} 条日志未逐条发送`,
        details: {
          dropped_count: droppedCount,
          distinct_signature_count: distinctSignatureCount,
          reasons,
        },
        sensitive_possible: false,
        preserve_message: false,
        preserve_details: false,
      };
      void this.postLog(payload, this._logger.mutationHeaders || {});
    }

    _resolveLogFlushWaiters() {
      const transport = this._logTransport;
      if (transport.queue.length || transport.inFlight.size) return;
      const waiters = transport.flushWaiters.splice(0);
      for (const resolve of waiters) resolve({ ok: true });
    }

    flushLogger(options = {}) {
      this._flushLoggerAggregates({ final: !!options.final });
      this._queueLogOverflowSummary();
      const transport = this._logTransport;
      if (transport.disposed) return Promise.resolve({ ok: false, reason: 'disposed' });
      if (!transport.queue.length && !transport.inFlight.size) return Promise.resolve({ ok: true });
      if (transport.flushWaiters.length >= DEFAULT_LOG_FLUSH_WAITER_LIMIT) {
        return Promise.resolve({ ok: false, reason: 'flush_busy' });
      }
      const promise = new Promise((resolve) => transport.flushWaiters.push(resolve));
      this._pumpLogQueue({ force: true });
      return promise;
    }

    _disposeLogTransport() {
      const transport = this._logTransport;
      if (transport.disposed) return;
      transport.disposed = true;
      if (transport.pumpTimer != null) {
        this._window.clearTimeout(transport.pumpTimer);
        transport.pumpTimer = null;
      }
      for (const pending of transport.inFlight.values()) {
        this._window.clearTimeout(pending.timeoutId);
        try { pending.controller.abort(); } catch (_) { /* already aborted */ }
        pending.resolve({ ok: false, reason: 'disposed' });
      }
      transport.inFlight.clear();
      for (const item of transport.queue.splice(0)) {
        item.resolve({ ok: false, reason: 'disposed' });
      }
      const waiters = transport.flushWaiters.splice(0);
      for (const resolve of waiters) resolve({ ok: false, reason: 'disposed' });
      transport.overflowDropped = 0;
      transport.overflowReasons = {};
      transport.overflowSignatures.clear();
      transport.overflowContext = null;
    }

    enableLog(payload, mutationHeaders = {}) {
      return this._post('/api/game/logs/enable', jsonBody(payload, mutationHeaders), {
        headers: mutationHeaders,
        keepalive: true,
      });
    }

    configureLogger(options = {}) {
      const logger = this._logger;
      logger.contextProvider = typeof options.contextProvider === 'function' ? options.contextProvider : null;
      logger.enableTimeoutMs = Math.max(1, Number(options.enableTimeoutMs || DEFAULT_LOG_ENABLE_TIMEOUT_MS));
      logger.aggregateLimit = boundedPositiveInteger(options.aggregateLimit, DEFAULT_LOG_AGGREGATE_LIMIT, 1024);
      logger.summaryIntervalMs = Math.max(
        250,
        boundedPositiveInteger(options.summaryIntervalMs, DEFAULT_LOG_SUMMARY_INTERVAL_MS, 60000),
      );
      logger.recoveryQuietMs = Math.max(
        logger.summaryIntervalMs,
        boundedPositiveInteger(options.recoveryQuietMs, DEFAULT_LOG_RECOVERY_QUIET_MS, 300000),
      );
      if (options.captureGlobalErrors !== false) this._installLoggerCapture();
      return this.logger;
    }

    _loggerContext() {
      try {
        const context = this._logger.contextProvider?.() || {};
        return {
          sessionId: String(context.sessionId || context.session_id || this.sessionId || ''),
          lanlanName: String(context.lanlanName || context.lanlan_name || this.routeLanlanName || ''),
        };
      } catch (_) {
        return { sessionId: this.sessionId, lanlanName: this.routeLanlanName };
      }
    }

    _safeLogValue(value, depth = 0, preserve = false) {
      if (preserve) return value;
      if (value == null || typeof value === 'boolean' || typeof value === 'number') return value;
      if (typeof value === 'string') {
        return value.length > 1200 ? `${value.slice(0, 1200)}...<truncated>` : value;
      }
      if (depth >= 3) return String(value).slice(0, 240);
      if (Array.isArray(value)) {
        const result = value.slice(0, 20).map((item) => this._safeLogValue(item, depth + 1));
        if (value.length > 20) result.push({ _truncated: `+${value.length - 20} items` });
        return result;
      }
      if (typeof value === 'object') {
        const result = {};
        const keys = Object.keys(value);
        for (const key of keys.slice(0, 30)) {
          result[key] = this._safeLogValue(value[key], depth + 1);
        }
        if (keys.length > 30) result._truncated = `+${keys.length - 30} keys`;
        return result;
      }
      return String(value).slice(0, 1200);
    }

    log(level, category, event, message, details = {}, sensitivePossible = false, options = {}) {
      const logger = this._logger;
      if (!logger.enabled) return;
      const preserveDetails = !!(options.preserveDetails || options.noTruncate);
      const preserveMessage = !!(options.preserveMessage || options.noTruncate);
      const context = this._loggerContext();
      const payload = {
        session_id: context.sessionId,
        game_type: this.gameType,
        lanlan_name: context.lanlanName,
        source: this.source,
        level,
        category,
        event,
        message: String(message || ''),
        details: this._safeLogValue(details, 0, preserveDetails),
        sensitive_possible: !!sensitivePossible,
        preserve_message: preserveMessage,
        preserve_details: preserveDetails,
      };
      this._recordOrSendLogPayload(payload);
    }

    _logSignature(payload = {}) {
      const details = payload.details && typeof payload.details === 'object' ? payload.details : {};
      let detailHint = details.error || details.reason || details.error_type || details.code || '';
      if (!detailHint) {
        try {
          detailHint = JSON.stringify(details).slice(0, 800);
        } catch (_) {
          detailHint = String(details).slice(0, 800);
        }
      }
      const raw = [
        payload.level || '',
        payload.category || '',
        payload.event || '',
        String(payload.message || '').slice(0, 600),
        String(detailHint || '').slice(0, 400),
      ].join('|');
      let hash = 2166136261;
      for (let index = 0; index < raw.length; index += 1) {
        hash ^= raw.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return `${String(payload.event || 'log').slice(0, 80)}:${(hash >>> 0).toString(16)}`;
    }

    _shouldAggregateLog(payload) {
      const level = String(payload?.level || '').toLowerCase();
      return level === 'warning' || level === 'warn' || level === 'error';
    }

    _recordOrSendLogPayload(payload) {
      if (!this._shouldAggregateLog(payload)) {
        this._sendLogPayload(payload);
        return;
      }
      const logger = this._logger;
      const signature = this._logSignature(payload);
      const now = Date.now();
      const existing = logger.aggregates.get(signature);
      if (existing) {
        existing.count = Math.min(Number.MAX_SAFE_INTEGER, existing.count + 1);
        existing.lastSeen = now;
        if (!existing.stormNotified) {
          existing.stormNotified = true;
          logger.originalWarn?.call(
            this._console,
            `[Soccer] [SessionLog] 检测到重复日志，开始聚合 signature=${signature}`,
          );
        }
        return;
      }
      if (logger.aggregates.size >= logger.aggregateLimit) {
        let oldestKey = '';
        let oldestEntry = null;
        for (const [key, entry] of logger.aggregates.entries()) {
          if (!oldestEntry || entry.lastSeen < oldestEntry.lastSeen) {
            oldestKey = key;
            oldestEntry = entry;
          }
        }
        if (oldestEntry) {
          this._emitLogAggregateSummary(oldestEntry, { final: true, reason: 'aggregate_capacity' });
          logger.aggregates.delete(oldestKey);
        }
      }
      logger.aggregates.set(signature, {
        signature,
        payload,
        count: 1,
        reportedCount: 1,
        firstSeen: now,
        lastSeen: now,
        lastSummaryAt: now,
        stormNotified: false,
      });
      this._sendLogPayload(payload);
      this._startLoggerMaintenance();
    }

    _aggregateLogPayload(entry, event, message, details = {}) {
      const original = entry.payload || {};
      return {
        session_id: original.session_id || this.sessionId,
        game_type: original.game_type || this.gameType,
        lanlan_name: original.lanlan_name || this.routeLanlanName,
        source: original.source || this.source,
        level: event === 'repeated_log_recovered' ? 'info' : 'warning',
        category: 'logger',
        event,
        message,
        details: {
          signature: entry.signature,
          original_level: original.level || '',
          original_category: original.category || '',
          original_event: original.event || '',
          sample_message: String(original.message || '').slice(0, 1200),
          total_count: entry.count,
          first_seen_ms: entry.firstSeen,
          last_seen_ms: entry.lastSeen,
          ...details,
        },
        sensitive_possible: !!original.sensitive_possible,
        preserve_message: false,
        preserve_details: false,
      };
    }

    _emitLogAggregateSummary(entry, options = {}) {
      if (!entry || entry.count <= entry.reportedCount) return false;
      const repeatedSinceLastSummary = entry.count - entry.reportedCount;
      this._sendLogPayload(this._aggregateLogPayload(
        entry,
        'repeated_log_summary',
        `重复日志已聚合：${entry.count} 次`,
        {
          repeated_since_last_summary: repeatedSinceLastSummary,
          final: !!options.final,
          reason: options.reason || 'periodic',
        },
      ));
      entry.reportedCount = entry.count;
      entry.lastSummaryAt = Date.now();
      return true;
    }

    _maintainLoggerAggregates() {
      const logger = this._logger;
      const now = Date.now();
      for (const [signature, entry] of Array.from(logger.aggregates.entries())) {
        const quietMs = Math.max(0, now - entry.lastSeen);
        const summaryAgeMs = Math.max(0, now - entry.lastSummaryAt);
        if (entry.count > entry.reportedCount && summaryAgeMs >= logger.summaryIntervalMs) {
          this._emitLogAggregateSummary(entry);
        }
        if (quietMs < logger.recoveryQuietMs) continue;
        if (entry.count > 1) {
          this._sendLogPayload(this._aggregateLogPayload(
            entry,
            'repeated_log_recovered',
            `重复日志已停止：共 ${entry.count} 次`,
            { quiet_ms: quietMs },
          ));
        }
        logger.aggregates.delete(signature);
      }
      if (!logger.aggregates.size) this._stopLoggerMaintenance();
    }

    _startLoggerMaintenance() {
      const logger = this._logger;
      if (logger.maintenanceTimer != null || !logger.aggregates.size) return;
      logger.maintenanceTimer = this._window.setInterval(
        () => this._maintainLoggerAggregates(),
        logger.summaryIntervalMs,
      );
    }

    _stopLoggerMaintenance() {
      const logger = this._logger;
      if (logger.maintenanceTimer != null) {
        this._window.clearInterval(logger.maintenanceTimer);
        logger.maintenanceTimer = null;
      }
    }

    _flushLoggerAggregates({ final = false } = {}) {
      const logger = this._logger;
      for (const entry of logger.aggregates.values()) {
        this._emitLogAggregateSummary(entry, { final, reason: final ? 'session_flush' : 'manual_flush' });
      }
      if (final) {
        logger.aggregates.clear();
        this._stopLoggerMaintenance();
      }
    }

    _sendLogPayload(payload) {
      const logger = this._logger;
      const security = this._window.nekoLocalMutationSecurity;
      try {
        if (security && typeof security.peekCachedToken === 'function') {
          const token = security.peekCachedToken();
          if (token) {
            void this.postLog(payload, { 'X-CSRF-Token': token });
            return;
          }
        }
      } catch (_) { /* continue with asynchronous credential lookup */ }
      if (security && typeof security.getMutationHeaders === 'function') {
        Promise.resolve(security.getMutationHeaders())
          .then((headers) => this.postLog(payload, headers || {}))
          .catch(() => this.postLog(payload));
        return;
      }
      if (logger.mutationHeaders) {
        void this.postLog(payload, logger.mutationHeaders);
        return;
      }
      void this.postLog(payload);
    }

    _enableLogWithHeaders(reason, mutationHeaders = {}) {
      const logger = this._logger;
      const context = this._loggerContext();
      const debugLogMutationHeaders = { ...mutationHeaders };
      const payload = {
        session_id: context.sessionId,
        game_type: this.gameType,
        lanlan_name: context.lanlanName,
        source: this.source,
        reason,
      };
      return this.enableLog(payload, mutationHeaders)
        .then((response) => response.json().catch(() => ({ ok: false, reason: 'bad_json' })))
        .then((result) => {
          if (result?.ok) logger.mutationHeaders = debugLogMutationHeaders;
          return result;
        });
    }

    _cancelLoggerEnableTimeout(reason = 'stale_enable_result') {
      const logger = this._logger;
      if (logger.enableTimeoutId != null) {
        this._window.clearTimeout(logger.enableTimeoutId);
        logger.enableTimeoutId = null;
      }
      if (logger.enableTimeoutResolve) {
        const resolve = logger.enableTimeoutResolve;
        logger.enableTimeoutResolve = null;
        resolve({ ok: false, reason });
      }
    }

    resetLogger() {
      const logger = this._logger;
      this._stopLoggerMaintenance();
      logger.aggregates.clear();
      logger.enableGeneration += 1;
      logger.enabled = false;
      logger.enableInFlight = false;
      logger.enablePromise = null;
      logger.mutationHeaders = null;
      this._cancelLoggerEnableTimeout();
    }

    _hasLoggerSendCredentials() {
      const logger = this._logger;
      const security = this._window.nekoLocalMutationSecurity;
      return !!(
        logger.mutationHeaders ||
        (security && (
          typeof security.peekCachedToken === 'function' ||
          typeof security.getMutationHeaders === 'function'
        ))
      );
    }

    enableLoggerAfterRouteStart() {
      const logger = this._logger;
      const generation = logger.enableGeneration;
      if (this._hasLoggerSendCredentials()) {
        logger.enabled = true;
        return Promise.resolve({ ok: true, reason: 'route_start_credentials_ready' });
      }
      if (logger.enableInFlight && logger.enablePromise) return logger.enablePromise;
      logger.enableInFlight = true;
      return this._startLoggerEnablePromise(
        this.getMutationHeaders().then((headers) => {
          if (logger.enableGeneration !== generation) return { ok: false, reason: 'stale_enable_result' };
          const debugLogMutationHeaders = { ...(headers || {}) };
          if (!csrfTokenFromHeaders(debugLogMutationHeaders)) {
            return { ok: false, reason: 'missing_csrf_token' };
          }
          logger.mutationHeaders = debugLogMutationHeaders;
          return { ok: true, enableReason: 'route_start_send_gate' };
        }),
        generation,
      );
    }

    _startLoggerEnablePromise(workPromise, generation) {
      const logger = this._logger;
      const isCurrentGeneration = () => logger.enableGeneration === generation;
      this._cancelLoggerEnableTimeout();
      let timeoutId = null;
      const timeoutPromise = new Promise((resolve) => {
        logger.enableTimeoutResolve = resolve;
        timeoutId = this._window.setTimeout(() => {
          if (logger.enableTimeoutId === timeoutId) {
            logger.enableTimeoutId = null;
            logger.enableTimeoutResolve = null;
          }
          resolve({ ok: false, reason: 'enable_timeout' });
        }, logger.enableTimeoutMs);
        logger.enableTimeoutId = timeoutId;
      });
      const guardedWork = Promise.resolve(workPromise)
        .then((result) => {
          if (!isCurrentGeneration()) return { ok: false, reason: 'stale_enable_result' };
          return this._onLoggerEnabled(result);
        })
        .catch((error) => {
          if (!isCurrentGeneration()) return { ok: false, reason: 'stale_enable_error' };
          return this._onLoggerEnableFailed(error);
        });
      const enablePromise = Promise.race([guardedWork, timeoutPromise])
        .then((result) => {
          if (!isCurrentGeneration()) return { ok: false, reason: 'stale_enable_result' };
          if (result?.reason === 'enable_timeout') {
            logger.originalWarn?.call(this._console, '[Soccer] [SessionLog] 小游戏场次诊断日志启用超时，稍后可重试');
          }
          return result;
        })
        .finally(() => {
          if (isCurrentGeneration()) {
            logger.enableInFlight = false;
            if (logger.enablePromise === enablePromise) logger.enablePromise = null;
          }
          if (logger.enableTimeoutId === timeoutId) {
            this._window.clearTimeout(timeoutId);
            logger.enableTimeoutId = null;
            logger.enableTimeoutResolve = null;
          }
        });
      logger.enablePromise = enablePromise;
      return enablePromise;
    }

    _onLoggerEnabled(result) {
      const logger = this._logger;
      if (result?.ok) {
        logger.enabled = true;
        const context = this._loggerContext();
        this._console.log('[Soccer] [SessionLog] 小游戏场次诊断日志已启用', {
          sessionId: context.sessionId,
          reason: result.enableReason || result.reason || 'unknown',
        });
      } else {
        logger.originalWarn?.call(this._console, '[Soccer] [SessionLog] 小游戏场次诊断日志启用失败', result || {});
      }
      return result;
    }

    _onLoggerEnableFailed(error) {
      this._logger.originalWarn?.call(
        this._console,
        '[Soccer] [SessionLog] 小游戏场次诊断日志启用请求失败',
        error,
      );
      return { ok: false, reason: 'request_failed' };
    }

    enableLogger(reason = 'keyboard') {
      const logger = this._logger;
      if (logger.enabled) return Promise.resolve({ ok: true, skipped: 'already_enabled' });
      if (logger.enableInFlight && logger.enablePromise) return logger.enablePromise;
      logger.enableInFlight = true;
      const generation = logger.enableGeneration;
      const security = this._window.nekoLocalMutationSecurity;
      const withEnableReason = (result) => ({ ...(result || {}), enableReason: reason });
      try {
        if (security && typeof security.peekCachedToken === 'function') {
          const token = security.peekCachedToken();
          if (token) {
            return this._startLoggerEnablePromise(
              this._enableLogWithHeaders(reason, { 'X-CSRF-Token': token }).then(withEnableReason),
              generation,
            );
          }
        }
      } catch (_) { /* continue with asynchronous credential lookup */ }
      if (security && typeof security.getMutationHeaders === 'function') {
        return this._startLoggerEnablePromise(
          Promise.resolve(security.getMutationHeaders())
            .then((headers) => this._enableLogWithHeaders(reason, headers || {}))
            .then(withEnableReason),
          generation,
        );
      }
      return this._startLoggerEnablePromise(
        this.getMutationHeaders()
          .then((headers) => this._enableLogWithHeaders(reason, headers || {}))
          .then(withEnableReason),
        generation,
      );
    }

    _installLoggerCapture() {
      const logger = this._logger;
      if (logger.windowErrorHandler || logger.rejectionHandler) return;
      logger.originalWarn = this._console.warn;
      logger.originalError = this._console.error;
      logger.windowErrorHandler = (event) => {
        this.log('error', 'frontend', 'window_error', event.message || '前端脚本错误', {
          filename: event.filename || '',
          lineno: event.lineno || 0,
          colno: event.colno || 0,
          error: event.error && (event.error.stack || event.error.message || String(event.error)),
        });
      };
      logger.rejectionHandler = (event) => {
        const reason = event.reason;
        this.log('error', 'frontend', 'unhandled_rejection', '前端 Promise 未处理异常', {
          reason: reason && (reason.stack || reason.message || String(reason)),
        });
      };
      logger.consoleWarnHandler = (...args) => {
        logger.originalWarn.apply(this._console, args);
        this.log('warning', 'frontend', 'console_warn', args.map((item) => String(item)).join(' '), { args }, true);
      };
      logger.consoleErrorHandler = (...args) => {
        logger.originalError.apply(this._console, args);
        this.log('error', 'frontend', 'console_error', args.map((item) => String(item)).join(' '), { args }, true);
      };
      this._window.addEventListener('error', logger.windowErrorHandler);
      this._window.addEventListener('unhandledrejection', logger.rejectionHandler);
      this._console.warn = logger.consoleWarnHandler;
      this._console.error = logger.consoleErrorHandler;
    }

    _disposeLogger() {
      const logger = this._logger;
      if (logger.windowErrorHandler) {
        this._window.removeEventListener('error', logger.windowErrorHandler);
        logger.windowErrorHandler = null;
      }
      if (logger.rejectionHandler) {
        this._window.removeEventListener('unhandledrejection', logger.rejectionHandler);
        logger.rejectionHandler = null;
      }
      if (logger.consoleWarnHandler && this._console.warn === logger.consoleWarnHandler && logger.originalWarn) {
        this._console.warn = logger.originalWarn;
      }
      if (logger.consoleErrorHandler && this._console.error === logger.consoleErrorHandler && logger.originalError) {
        this._console.error = logger.originalError;
      }
      logger.consoleWarnHandler = null;
      logger.consoleErrorHandler = null;
      logger.originalWarn = null;
      logger.originalError = null;
      logger.contextProvider = null;
      this.resetLogger();
    }

    async end(payload, options = {}) {
      void this.flushLogger({ final: true });
      const body = typeof payload === 'string' ? payload : JSON.stringify(payload);
      if (options.useBeacon && this._navigator.sendBeacon) {
        try {
          const accepted = this._navigator.sendBeacon(
            this._gameEndpoint('end'),
            new Blob([body], { type: 'application/json' }),
          );
          if (accepted) return { ok: true, beacon: true };
        } catch (error) {
          options.onBeaconError?.(error);
        }
      }
      const response = await this._post(this._gameEndpoint('end'), body, {
        keepalive: true,
        operation: 'route_end',
        timeoutMs: 8000,
      });
      return response.json().catch(() => ({ ok: response.ok, status: response.status }));
    }

    startHeartbeat(options = {}) {
      this.stopHeartbeat();
      if (this._disposed) throw this._hostError('disposed', 'Soccer host adapter has been disposed');
      const heartbeat = this._heartbeat;
      heartbeat.payload = typeof options.payload === 'function' ? options.payload : () => options.payload || {};
      heartbeat.timeoutMs = Math.max(1, Number(options.timeoutMs || DEFAULT_HEARTBEAT_TIMEOUT_MS));
      heartbeat.onInactive = typeof options.onInactive === 'function' ? options.onInactive : null;
      heartbeat.onError = typeof options.onError === 'function' ? options.onError : null;
      heartbeat.onRetryAbort = typeof options.onRetryAbort === 'function' ? options.onRetryAbort : null;
      heartbeat.visibilityHandler = () => {
        if (!this._disposed) void this.sendHeartbeat(true);
      };
      this._document.addEventListener('visibilitychange', heartbeat.visibilityHandler);
      void this.sendHeartbeat(false);
      heartbeat.timer = this._window.setInterval(
        () => void this.sendHeartbeat(false),
        Math.max(250, Number(options.intervalMs || DEFAULT_HEARTBEAT_INTERVAL_MS)),
      );
    }

    async sendHeartbeat(force = false) {
      const heartbeat = this._heartbeat;
      if (this._disposed || typeof heartbeat.payload !== 'function') return null;
      if (heartbeat.inFlight) {
        const age = performance.now() - Number(heartbeat.startedAt || 0);
        if (force || age > heartbeat.timeoutMs) {
          try { heartbeat.controller?.abort(); } catch (_) { /* noop */ }
          heartbeat.onRetryAbort?.({ force, age, reason: force ? 'visibilitychange' : 'timeout' });
        } else {
          return null;
        }
      }

      heartbeat.inFlight = true;
      heartbeat.startedAt = performance.now();
      const controller = new AbortController();
      heartbeat.controller = controller;

      try {
        const response = await this.heartbeat(heartbeat.payload(), {
          signal: controller.signal,
          timeoutMs: heartbeat.timeoutMs,
        });
        const data = await response.json().catch(() => ({}));
        if (response.ok && data.ok && data.active) {
          heartbeat.failures = 0;
          return data;
        }
        if (response.ok && data.ok && data.active === false) {
          heartbeat.onInactive?.(data);
          this.stopHeartbeat();
          return data;
        }
        heartbeat.failures += 1;
        heartbeat.onError?.({ data, status: response.status, failures: heartbeat.failures });
        return data;
      } catch (error) {
        heartbeat.failures += 1;
        heartbeat.onError?.({
          error,
          reason: error?.code === 'timeout' ? 'timeout' : error,
          failures: heartbeat.failures,
        });
        return null;
      } finally {
        if (heartbeat.controller === controller) {
          heartbeat.controller = null;
          heartbeat.inFlight = false;
          heartbeat.startedAt = 0;
        }
      }
    }

    stopHeartbeat() {
      const heartbeat = this._heartbeat;
      if (heartbeat.visibilityHandler) {
        this._document.removeEventListener('visibilitychange', heartbeat.visibilityHandler);
        heartbeat.visibilityHandler = null;
      }
      if (heartbeat.controller) {
        try { heartbeat.controller.abort(); } catch (_) { /* noop */ }
        heartbeat.controller = null;
      }
      heartbeat.inFlight = false;
      heartbeat.startedAt = 0;
      if (heartbeat.timer) {
        this._window.clearInterval(heartbeat.timer);
        heartbeat.timer = null;
      }
      heartbeat.payload = null;
      heartbeat.onInactive = null;
      heartbeat.onError = null;
      heartbeat.onRetryAbort = null;
    }

    startDrain(options = {}) {
      this.stopDrain();
      if (this._disposed) throw this._hostError('disposed', 'Soccer host adapter has been disposed');
      const drain = this._drain;
      drain.poll = typeof options.poll === 'function' ? options.poll : null;
      if (!drain.poll) return;
      drain.timer = this._window.setInterval(
        () => {
          if (!this._disposed && drain.poll) void drain.poll();
        },
        Math.max(250, Number(options.intervalMs || 700)),
      );
    }

    stopDrain() {
      const drain = this._drain;
      if (drain.timer) {
        this._window.clearInterval(drain.timer);
        drain.timer = null;
      }
      drain.poll = null;
    }

    dispose(options = {}) {
      if (this._disposed) return;
      this._disposed = true;
      const preserveOperations = new Set(options.preservePendingOperations || []);
      this.cancelPendingRequests('disposed', { preserveOperations });
      this.stopHeartbeat();
      this.stopDrain();
      this.stopAllSpeechRecognition();
      this.stopSpeechPlaybackBridge();
      this.stopVoiceControlBridge('disposed');
      this._disposeLogger();
      this._disposeLogTransport();
    }
  }

  window.createSoccerNekoAdapter = function createSoccerNekoAdapter(options = {}) {
    return new SoccerNekoAdapter(options);
  };
  window.SoccerNekoHostError = SoccerHostError;
})();
