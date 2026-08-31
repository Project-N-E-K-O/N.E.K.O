/**
 * N.E.K.O Mini-Game SDK public entry.
 *
 * Games that use N.E.K.O host capabilities must use this public SDK instead
 * of calling host REST, microphone, audio, logging, or avatar internals
 * directly. Capabilities are granted once at connect time and remain fixed
 * for the lifetime of the game client.
 */
(function (global) {
  'use strict';

  const SDK_VERSION = '0.1.0';
  const SDK_PROTOCOL_VERSION = '1';
  const DEFAULT_CONNECT_TIMEOUT_MS = 10000;
  const MIN_CONNECT_TIMEOUT_MS = 250;
  const MAX_CONNECT_TIMEOUT_MS = 30000;
  const MAX_REQUEST_TIMEOUT_MS = 120000;
  const MAX_CAPABILITIES = 32;
  const MAX_LISTENERS_PER_EVENT = 32;
  const MAX_CONTRACTS_PER_KIND = 64;
  const MAX_CONTRACT_SCHEMA_NODES = 256;
  const MAX_CONTRACT_SCHEMA_CHARS = 64 * 1024;
  const MAX_CONTRACT_PAYLOAD_NODES = 2048;
  const MAX_CONTRACT_PAYLOAD_BYTES = 256 * 1024;
  const MAX_CONTRACT_PENDING_REQUESTS = 8;
  const MAX_CONTEXT_SCOPES = 16;
  const MAX_CONTEXT_PENDING_REQUESTS = 2;
  const MAX_DIALOGUE_PENDING_REQUESTS = 4;
  const MAX_AUTHOR_PROMPT_MESSAGES = 32;
  const MAX_AUTHOR_PROMPT_CONTENT_CHARS = 16000;
  const MAX_AUTHOR_PROMPT_TOTAL_CHARS = 64000;
  const MAX_MEMORY_PENDING_REQUESTS = 2;
  const MAX_STORAGE_PENDING_REQUESTS = 4;
  const MAX_STORAGE_VALUE_BYTES = 64 * 1024;
  const MAX_LEADERBOARD_BOARDS = 16;
  const MAX_LEADERBOARD_PENDING_REQUESTS = 4;
  const MAX_LEADERBOARD_ENTRY_BYTES = 8 * 1024;
  const MAX_LEADERBOARD_STATE_BYTES = 64 * 1024;
  const MAX_LEADERBOARD_LOCAL_ENTRIES = 100;
  const MAX_LOADING_PRESENTATIONS = 4;
  const MAX_BUBBLE_PRESENTATIONS = 8;
  const MAX_CONSENT_PRESENTATIONS = 4;
  const MAX_AVATAR_RENDERERS = 8;
  const MAX_AUDIO_CONTROLLERS = 4;
  const MAX_AUDIO_RESOURCE_NODES = 2048;
  const MAX_AUDIO_RESOURCE_CHARS = 512 * 1024;
  const MAX_AUDIO_COLLECTION_ITEMS = 256;
  const MAX_SPEECH_PENDING_REQUESTS = 4;
  const MAX_SPEECH_PRELOAD_PENDING_REQUESTS = 2;
  const MAX_SPEECH_PRELOAD_LINES = 32;
  const MAX_SPEECH_PRELOAD_CHARS = 32000;
  const DEFAULT_SPEECH_REQUEST_TIMEOUT_MS = 60000;
  const DEFAULT_SPEECH_PRELOAD_TIMEOUT_MS = 180000;
  const MAX_SPEECH_PRELOAD_TIMEOUT_MS = 5 * 60 * 1000;
  const MAX_SPEECH_REQUEST_METADATA = 64;
  const MAX_SPEECH_TEXT_CHARS = 2000;
  const MAX_SPEECH_EVENT_NODES = 512;
  const MAX_SPEECH_EVENT_CHARS = 64 * 1024;
  const MAX_SPEECH_PLAYBACK_SECONDS = 600;
  const SPEECH_PLAYBACK_STALE_MS = 3500;
  const SPEECH_PLAYBACK_ABSOLUTE_STALE_MS = 15000;
  const MAX_RUNTIME_EVENT_BYTES = 256 * 1024;
  const MAX_RUNTIME_OUTPUTS_PER_POLL = 50;
  const MAX_RUNTIME_ROUTE_INSTANCE_IDS = 4;
  // Deadlock breaker, NOT a latency budget: a handler may legitimately await a
  // cut-scene or a whole spoken line. Only a handler that has been stuck for a
  // full minute is abandoned, and the next output is still delivered.
  const MAX_RUNTIME_HANDLER_MS = 60000;
  const MIN_RUNTIME_INTERVAL_MS = 250;
  const MAX_RUNTIME_INTERVAL_MS = 60000;
  const DEFAULT_HEARTBEAT_INTERVAL_MS = 2500;
  const DEFAULT_HEARTBEAT_TIMEOUT_MS = 4500;
  const DEFAULT_OUTPUT_INTERVAL_MS = 700;
  const DEFAULT_OUTPUT_TIMEOUT_MS = 8000;
  const MANIFEST_TOP_LEVEL_FIELDS = Object.freeze(new Set([
    'id',
    'version',
    'protocolVersion',
    'requiredCapabilities',
    'optionalCapabilities',
    'contracts',
    'leaderboards',
  ]));
  const RUNTIME_EVENT_PATTERN = /^[a-z][a-z0-9:-]{0,63}$/;
  // Every one of these goes through requireActiveRuntimeRoute(), which needs a
  // route only the runtime API can start -- so a grant without runtime is
  // permanently unusable. Declared once because both the manifest-time check and
  // the post-negotiation check below have to mean exactly the same set.
  // `speech-output` is deliberately absent: speech.speak() is accepted pre-route.
  const RUNTIME_DEPENDENT_CAPABILITIES = Object.freeze([
    'memory', 'context-read', 'leaderboard-server', 'voice-input',
  ]);
  const CONTRACT_KINDS = Object.freeze(['events', 'states', 'controls', 'results']);
  const CONTRACT_SCHEMA_TYPES = Object.freeze([
    'null', 'boolean', 'number', 'integer', 'string', 'array', 'object',
  ]);
  const RUNTIME_EVENT_TYPES = Object.freeze([
    'runtime-state',
    'runtime-inactive',
    'runtime-error',
    'runtime-output',
    'visibility-change',
    'page-exit',
  ]);
  const CAPABILITY_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;
  const AVATAR_SLOT_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;
  const AUDIO_SLOT_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;
  const AVATAR_TYPES = Object.freeze(['live2d', 'vrm']);
  const AVATAR_VIEWPORT_MODES = Object.freeze(['fixed', 'container', 'host-window']);
  const AVATAR_FIT_MODES = Object.freeze(['contain', 'cover', 'native']);
  const AVATAR_ALIGNMENTS = Object.freeze([
    'top-left', 'top-center', 'top-right',
    'center-left', 'center', 'center-right',
    'bottom-left', 'bottom-center', 'bottom-right',
  ]);
  const SUPPORTED_CAPABILITIES = Object.freeze([
    'runtime',
    'dialogue',
    'quick-lines',
    'logging',
    'voice-input',
    'avatar-renderer',
    'audio',
    'speech-output',
    'context-read',
    'memory',
    'storage',
    'leaderboard-local',
    'leaderboard-server',
  ]);
  const MANDATORY_CAPABILITIES = Object.freeze(['logging']);
  const PUBLIC_TRANSPORT_ERROR_CODES = Object.freeze([
    'invalid_manifest',
    'invalid_handshake',
    'invalid_contract',
    'incompatible_version',
    'game_unregistered',
    'game_disabled',
    'integrity_failed',
    'capability_unavailable',
    'transport_unavailable',
    'session_invalid',
    'unauthorized',
    'unsupported',
    'consent_required',
    'consent_locked',
    'timeout',
    'cancelled',
    'disconnected',
    'busy',
    'quota_exceeded',
    'disposed',
    'network_error',
    'request_failed',
  ]);

  class NekoMiniGameError extends Error {
    constructor(code, message, details = {}) {
      super(message);
      this.name = 'NekoMiniGameError';
      this.code = String(code || 'unknown');
      this.details = details && typeof details === 'object' ? details : {};
    }
  }

  const presentationStyleDocuments = new WeakSet();

  function ensurePresentationStyles(documentImpl) {
    if (!documentImpl?.createElement || presentationStyleDocuments.has(documentImpl)) return;
    const style = documentImpl.createElement('style');
    style.setAttribute('data-neko-minigame-presentation', 'v1');
    style.textContent = `
.neko-minigame-loading{position:absolute;inset:0;z-index:var(--neko-game-loading-z,1000);display:grid;place-items:center;padding:24px;background:var(--neko-game-surface,Canvas);color:var(--neko-game-text,CanvasText)}
.neko-minigame-loading[hidden],.neko-minigame-bubble[hidden]{display:none}
.neko-minigame-loading__panel{box-sizing:border-box;width:min(100%,560px);padding:24px;border:1px solid var(--neko-game-border,GrayText);background:var(--neko-game-panel,Canvas);color:inherit}
.neko-minigame-loading__title{margin:0 0 16px;font:600 1.125rem/1.4 system-ui,sans-serif}
.neko-minigame-loading__message,.neko-minigame-loading__error{margin:8px 0;font:400 1rem/1.5 system-ui,sans-serif;overflow-wrap:anywhere}
.neko-minigame-loading__progress{display:block;width:100%;height:16px;margin:16px 0 8px;accent-color:var(--neko-game-accent,AccentColor)}
.neko-minigame-loading[data-state="error"] .neko-minigame-loading__error{color:var(--neko-game-error,CanvasText);font-weight:600}
.neko-minigame-bubble{box-sizing:border-box;max-width:min(32rem,calc(100vw - 32px));padding:12px 16px;border:1px solid var(--neko-game-border,GrayText);background:var(--neko-game-panel,Canvas);color:var(--neko-game-text,CanvasText);font:400 1rem/1.5 system-ui,sans-serif;overflow-wrap:anywhere;pointer-events:none}
.neko-minigame-consent{display:grid;grid-template-columns:44px minmax(0,1fr);align-items:center;min-height:44px;color:var(--neko-game-text,CanvasText);font:400 1rem/1.5 system-ui,sans-serif}
.neko-minigame-consent__input{justify-self:center;width:20px;height:20px;margin:0;accent-color:var(--neko-game-accent,AccentColor)}
.neko-minigame-consent__copy{display:grid;gap:4px;padding:8px 0}
.neko-minigame-consent__hint,.neko-minigame-consent__status{font-size:.875rem;opacity:.8;overflow-wrap:anywhere}
`;
    (documentImpl.head || documentImpl.documentElement)?.appendChild?.(style);
    presentationStyleDocuments.add(documentImpl);
  }

  function fail(code, message, details) {
    throw new NekoMiniGameError(code, message, details);
  }

  function normalizeTransportError(error, operation) {
    if (error instanceof NekoMiniGameError) return error;
    const rawCode = String(error?.code || error?.name || '').trim().toLowerCase();
    const mappedCode = rawCode === 'aborterror'
      ? 'cancelled'
      : (rawCode === 'disconnect' ? 'disconnected' : rawCode);
    const code = PUBLIC_TRANSPORT_ERROR_CODES.includes(mappedCode)
      ? mappedCode
      : (error instanceof TypeError ? 'network_error' : 'request_failed');
    const fallbackMessage = code === 'timeout'
      ? 'The host request timed out'
      : code === 'cancelled'
        ? 'The host request was cancelled'
        : code === 'disconnected'
          ? 'The host transport disconnected'
          : 'The host request failed';
    return new NekoMiniGameError(
      code,
      String(error?.message || fallbackMessage).slice(0, 500),
      { operation: String(operation || 'transport') },
    );
  }

  function normalizeCapabilities(value, fieldName) {
    // Only an ABSENT list defaults. The schema types both capability lists as
    // arrays, so an explicit `null` is schema-invalid -- swallowing it let a
    // manifest the schema rejects connect as though nothing was declared.
    if (value === undefined) return [];
    if (!Array.isArray(value)) {
      fail('invalid_manifest', `${fieldName} must be an array`);
    }
    if (value.length > MAX_CAPABILITIES) {
      fail('invalid_manifest', `${fieldName} exceeds the capability limit`, {
        limit: MAX_CAPABILITIES,
      });
    }
    const result = [];
    const seen = new Set();
    for (const item of value) {
      // The published schema declares these lists as `uniqueItems: true` and
      // their items as strings. Silently coercing and de-duplicating meant a
      // manifest the schema rejects still connected -- and a duplicate is far
      // more often a copy-paste slip than an intent worth honouring.
      if (typeof item !== 'string') {
        fail('invalid_manifest', `${fieldName} entries must be strings`, { entry: item });
      }
      // The ORIGINAL string, untrimmed: the schema applies its pattern to what
      // the manifest actually declares, so `' logging '` is schema-invalid --
      // while trimming first silently rewrote it into a real permission request.
      const capability = item;
      if (!CAPABILITY_PATTERN.test(capability)) {
        fail('invalid_manifest', `Invalid capability in ${fieldName}`, { capability });
      }
      if (seen.has(capability)) {
        fail('invalid_manifest', `${fieldName} contains a duplicate entry`, { capability });
      }
      seen.add(capability);
      result.push(capability);
    }
    return result;
  }

  function plainObject(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function contractInteger(value, fieldName, minimum, maximum, fallback) {
    if (value === undefined) return fallback;
    // No coercion. The published schema declares every one of these
    // (minLength/maxLength/minItems/maxItems/maxEntries) as a JSON integer, and
    // `Number()` accepted `'5'` and `true` alike -- so editor/CI validation
    // against the schema and the validation that actually runs disagreed about
    // the same manifest. Same rule the minimum/maximum path now uses.
    if (typeof value !== 'number' || !Number.isInteger(value)
        || value < minimum || value > maximum) {
      fail('invalid_manifest', `${fieldName} must be an integer between ${minimum} and ${maximum}`);
    }
    return value;
  }

  function normalizeContractSchema(schemaInput, fieldName, state, depth = 0) {
    state.nodes += 1;
    if (state.nodes > MAX_CONTRACT_SCHEMA_NODES || depth > 12) {
      fail('invalid_manifest', `${fieldName} exceeds the contract schema complexity limit`, {
        limit: MAX_CONTRACT_SCHEMA_NODES,
      });
    }
    let input = schemaInput;
    if (Array.isArray(input)) {
      // The schema bounds shorthand items at `maxLength: 4096`; the expanded
      // `enum` form carries no such bound, so converting first dropped it and a
      // longer string connected against a schema that rejects it.
      for (const item of input) {
        if (typeof item === 'string' && [...item].length > 4096) {
          fail('invalid_manifest', `${fieldName} enum shorthand value exceeds its length limit`, {
            limit: 4096,
          });
        }
      }
      input = { type: 'string', enum: input };
    }
    if (!plainObject(input)) {
      fail('invalid_manifest', `${fieldName} must be a contract schema object`);
    }
    const allowedKeys = new Set([
      'type', 'enum', 'minimum', 'maximum', 'minLength', 'maxLength',
      'minItems', 'maxItems', 'items', 'properties', 'required', 'additionalProperties',
    ]);
    for (const key of Object.keys(input)) {
      state.characters += key.length;
      if (!allowedKeys.has(key)) {
        fail('invalid_manifest', `${fieldName} contains an unsupported schema keyword`, { key });
      }
    }
    // The ORIGINAL value, untrimmed: the schema requires an exact enum member,
    // so `type: ' string '` is schema-invalid -- while trimming first silently
    // turned it into a supported type.
    const type = input.type;
    if (typeof type !== 'string' || !CONTRACT_SCHEMA_TYPES.includes(type)) {
      fail('invalid_manifest', `${fieldName}.type is unsupported`, { type });
    }
    const schema = { type };
    if (input.enum !== undefined) {
      if (!Array.isArray(input.enum) || !input.enum.length || input.enum.length > 64) {
        fail('invalid_manifest', `${fieldName}.enum must contain between 1 and 64 scalar values`);
      }
      const seen = new Set();
      const values = [];
      for (const value of input.enum) {
        if (value !== null && !['string', 'number', 'boolean'].includes(typeof value)) {
          fail('invalid_manifest', `${fieldName}.enum only supports JSON scalar values`);
        }
        if (typeof value === 'number' && !Number.isFinite(value)) {
          fail('invalid_manifest', `${fieldName}.enum contains a non-finite number`);
        }
        if (!contractPayloadTypeMatches(value, type)) {
          fail('invalid_manifest', `${fieldName}.enum contains a value outside its declared type`);
        }
        // Both schema definitions declare `uniqueItems: true`, so a duplicate
        // is schema-invalid; silently dropping it connected with a rewritten
        // contract. Same rule the capability lists now use.
        const key = `${typeof value}:${JSON.stringify(value)}`;
        if (seen.has(key)) {
          fail('invalid_manifest', `${fieldName}.enum contains a duplicate value`);
        }
        seen.add(key);
        state.characters += String(value ?? '').length;
        values.push(value);
      }
      schema.enum = Object.freeze(values);
    }
    if (type === 'number' || type === 'integer') {
      // `typeof`, not `Number()`: the published schema declares these as
      // numbers, and coercing meant `minimum: '5'` was silently accepted while
      // `minimum: null` became a hard floor of 0 -- the opposite of what an
      // author writing "no minimum" meant, and invisible until a payload was
      // rejected against a bound nobody declared.
      if (input.minimum !== undefined) {
        if (typeof input.minimum !== 'number' || !Number.isFinite(input.minimum)) {
          fail('invalid_manifest', `${fieldName}.minimum must be a finite number`);
        }
        schema.minimum = input.minimum;
      }
      if (input.maximum !== undefined) {
        if (typeof input.maximum !== 'number' || !Number.isFinite(input.maximum)) {
          fail('invalid_manifest', `${fieldName}.maximum must be a finite number`);
        }
        schema.maximum = input.maximum;
      }
      if (schema.minimum !== undefined && schema.maximum !== undefined && schema.minimum > schema.maximum) {
        fail('invalid_manifest', `${fieldName}.minimum must not exceed maximum`);
      }
    }
    if (type === 'string') {
      schema.minLength = contractInteger(input.minLength, `${fieldName}.minLength`, 0, 4096, 0);
      schema.maxLength = contractInteger(input.maxLength, `${fieldName}.maxLength`, 0, 4096, 4096);
      if (schema.minLength > schema.maxLength) {
        fail('invalid_manifest', `${fieldName}.minLength must not exceed maxLength`);
      }
    }
    if (type === 'array') {
      schema.minItems = contractInteger(input.minItems, `${fieldName}.minItems`, 0, 256, 0);
      schema.maxItems = contractInteger(input.maxItems, `${fieldName}.maxItems`, 0, 256, 256);
      if (schema.minItems > schema.maxItems) {
        fail('invalid_manifest', `${fieldName}.minItems must not exceed maxItems`);
      }
      if (!input.items) fail('invalid_manifest', `${fieldName}.items is required for arrays`);
      schema.items = normalizeContractSchema(input.items, `${fieldName}.items`, state, depth + 1);
    }
    if (type === 'object') {
      // Same rule as manifest.contracts: only ABSENT defaults. The schema
      // types `properties` as an object, so `null` must not become `{}`.
      const propertiesInput = input.properties === undefined ? {} : input.properties;
      if (!plainObject(propertiesInput)) {
        fail('invalid_manifest', `${fieldName}.properties must be an object`);
      }
      const entries = Object.entries(propertiesInput);
      if (entries.length > 64) {
        fail('invalid_manifest', `${fieldName}.properties contains too many fields`, { limit: 64 });
      }
      const properties = {};
      for (const [name, child] of entries) {
        // Code points, matching the published schema's `maxLength` (JSON Schema
        // counts code points) -- `name.length` charged two per astral character,
        // so the runtime was stricter than the contract it publishes.
        if (
          !name || [...name].length > 64
          || name === '__proto__' || name === 'prototype' || name === 'constructor'
        ) {
          fail('invalid_manifest', `${fieldName}.properties contains an invalid field`, { name });
        }
        state.characters += name.length;
        properties[name] = normalizeContractSchema(
          child,
          `${fieldName}.properties.${name}`,
          state,
          depth + 1,
        );
      }
      const requiredInput = input.required === undefined ? [] : input.required;
      if (!Array.isArray(requiredInput) || requiredInput.length > entries.length) {
        fail('invalid_manifest', `${fieldName}.required must be an array of declared property names`);
      }
      const required = [];
      const requiredSeen = new Set();
      for (const rawName of requiredInput) {
        // `required` entries are declared property NAMES, and the schema types
        // them as strings. `String(1)` matched a property literally named "1",
        // so a manifest the schema rejects connected with a different shape.
        if (typeof rawName !== 'string') {
          fail('invalid_manifest', `${fieldName}.required entries must be strings`, {
            entry: rawName,
          });
        }
        const name = rawName;
        if (!Object.prototype.hasOwnProperty.call(properties, name) || requiredSeen.has(name)) {
          fail('invalid_manifest', `${fieldName}.required contains an unknown or duplicate field`, { name });
        }
        requiredSeen.add(name);
        required.push(name);
      }
      if (input.additionalProperties !== undefined && typeof input.additionalProperties !== 'boolean') {
        fail('invalid_manifest', `${fieldName}.additionalProperties must be boolean`);
      }
      schema.properties = Object.freeze(properties);
      schema.required = Object.freeze(required);
      schema.additionalProperties = input.additionalProperties === true;
    }
    state.characters += JSON.stringify(schema.enum || []).length;
    if (state.characters > MAX_CONTRACT_SCHEMA_CHARS) {
      fail('invalid_manifest', 'Contract schemas exceed the total character limit', {
        limit: MAX_CONTRACT_SCHEMA_CHARS,
      });
    }
    return Object.freeze(schema);
  }

  function normalizeContracts(value) {
    // `?? {}` also swallowed an explicit `null`, which the schema types as an
    // object and rejects -- so a schema-invalid manifest connected as though the
    // author had declared no contracts at all. Only ABSENT defaults.
    const input = value === undefined ? {} : value;
    if (!plainObject(input)) fail('invalid_manifest', 'manifest.contracts must be an object');
    for (const key of Object.keys(input)) {
      if (!CONTRACT_KINDS.includes(key)) {
        fail('invalid_manifest', 'manifest.contracts contains an unsupported contract kind', { key });
      }
    }
    const state = { nodes: 0, characters: 0 };
    const contracts = {};
    for (const kind of CONTRACT_KINDS) {
      const declarations = input[kind] === undefined ? {} : input[kind];
      if (!plainObject(declarations)) {
        fail('invalid_manifest', `manifest.contracts.${kind} must be an object`);
      }
      const entries = Object.entries(declarations);
      if (entries.length > MAX_CONTRACTS_PER_KIND) {
        fail('invalid_manifest', `manifest.contracts.${kind} contains too many declarations`, {
          limit: MAX_CONTRACTS_PER_KIND,
        });
      }
      const normalized = {};
      for (const [name, schema] of entries) {
        if (!RUNTIME_EVENT_PATTERN.test(name)) {
          fail('invalid_manifest', `Invalid ${kind} contract name`, { name });
        }
        normalized[name] = normalizeContractSchema(
          schema,
          `manifest.contracts.${kind}.${name}`,
          state,
        );
      }
      contracts[kind] = Object.freeze(normalized);
    }
    return Object.freeze(contracts);
  }

  function normalizeLeaderboardDefinitions(value) {
    // Same rule as manifest.contracts: only an ABSENT value defaults.
    const input = value === undefined ? {} : value;
    if (!plainObject(input)) fail('invalid_manifest', 'manifest.leaderboards must be an object');
    const entries = Object.entries(input);
    if (entries.length > MAX_LEADERBOARD_BOARDS) {
      fail('invalid_manifest', 'manifest.leaderboards contains too many boards', {
        limit: MAX_LEADERBOARD_BOARDS,
      });
    }
    const definitions = {};
    for (const [boardId, rawDefinition] of entries) {
      if (!/^[a-z][a-z0-9-]{0,31}$/.test(boardId)) {
        fail('invalid_manifest', 'manifest.leaderboards contains an invalid board id', { boardId });
      }
      if (!plainObject(rawDefinition)) {
        fail('invalid_manifest', `manifest.leaderboards.${boardId} must be an object`);
      }
      const allowed = new Set(['scoreField', 'order', 'maxEntries', 'retention']);
      for (const key of Object.keys(rawDefinition)) {
        if (!allowed.has(key)) {
          fail('invalid_manifest', `manifest.leaderboards.${boardId} contains an unsupported field`, {
            field: key,
          });
        }
      }
      // Type first: `String(true)` is `'true'`, which matches the field-name
      // pattern, so a boolean silently became a board keyed on a field no entry
      // will ever carry. Absent still defaults to 'score'.
      if (rawDefinition.scoreField !== undefined
          && typeof rawDefinition.scoreField !== 'string') {
        fail('invalid_manifest', `manifest.leaderboards.${boardId}.scoreField must be a string`);
      }
      // Only an ABSENT value defaults, and the declared name is checked as
      // written: `''` is schema-invalid yet silently became the default board
      // field, and `' score '` was trimmed into a real field the manifest never
      // declared. Same rule as the other pattern-constrained fields.
      const scoreField = rawDefinition.scoreField === undefined
        ? 'score'
        : rawDefinition.scoreField;
      if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(scoreField)
          || scoreField === 'prototype' || scoreField === 'constructor') {
        // The clone every entry passes through forbids these property names, so
        // a board declared on one connects fine and then rejects every
        // submission; omitting the property instead yields a non-finite score.
        // ('__proto__' cannot reach here -- the pattern requires a leading
        // letter -- but the rule is stated in full so it reads as one set.)
        fail('invalid_manifest', `manifest.leaderboards.${boardId}.scoreField is invalid`);
      }
      // Default only an ABSENT value. `|| 'descending'` also swallowed an
      // explicit `null` / `''` / `false`, so a manifest the schema rejects (it
      // allows only the named enum strings) ran with configuration its author
      // never declared.
      // Untrimmed, like every other enum-valued manifest field: the schema
      // requires an exact member, so `' descending '` is schema-invalid while
      // trimming silently executed it as the real mode.
      const order = rawDefinition.order === undefined ? 'descending' : rawDefinition.order;
      if (typeof order !== 'string' || !['ascending', 'descending'].includes(order)) {
        fail('invalid_manifest', `manifest.leaderboards.${boardId}.order is unsupported`);
      }
      const retention = rawDefinition.retention === undefined ? 'recent' : rawDefinition.retention;
      if (typeof retention !== 'string' || !['best', 'recent'].includes(retention)) {
        fail('invalid_manifest', `manifest.leaderboards.${boardId}.retention is unsupported`);
      }
      const maxEntries = contractInteger(
        rawDefinition.maxEntries,
        `manifest.leaderboards.${boardId}.maxEntries`,
        1,
        MAX_LEADERBOARD_LOCAL_ENTRIES,
        50,
      );
      definitions[boardId] = Object.freeze({ scoreField, order, retention, maxEntries });
    }
    return Object.freeze(definitions);
  }

  function normalizeManifest(manifest) {
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
      fail('invalid_manifest', 'A mini-game manifest object is required');
    }
    for (const key of Object.keys(manifest)) {
      if (!MANIFEST_TOP_LEVEL_FIELDS.has(key)) {
        fail('invalid_manifest', 'manifest contains an unsupported field', { field: key });
      }
    }
    // Type first, coerce second. The published schema declares all three as
    // strings, so `version: 1` (and `protocolVersion: 1`) used to connect while
    // the same manifest failed schema validation in an editor or in CI. Absent
    // fields keep their existing "required" / default handling below.
    for (const field of ['id', 'version', 'protocolVersion']) {
      if (manifest[field] !== undefined && typeof manifest[field] !== 'string') {
        fail('invalid_manifest', `manifest.${field} must be a string`, { field });
      }
    }
    // Untrimmed, like the capability names: the schema pattern applies to the
    // declared string, and trimming silently remapped `' demo '` onto the
    // registration and storage identity of the real `demo`.
    const id = manifest.id === undefined ? '' : manifest.id;
    // Declared as-is. The schema puts no pattern on `version`, so `' 1.0 '` is
    // schema-VALID -- and trimming aliased it onto the distinct, equally valid
    // `'1.0'`, rewriting an identity the author declared and that rides the
    // handshake and registration.
    const version = manifest.version === undefined ? '' : manifest.version;
    // Default only an ABSENT value. `manifest.protocolVersion || SDK_PROTOCOL_VERSION`
    // also swallowed every falsey supplied one -- `0` and `''` both became the
    // current protocol and connected, defeating the compatibility guard and
    // disagreeing with the schema's `const: "1"`. (`0` is now rejected one line
    // above by the type check; `''` reaches here as a string and must fail the
    // identity check below rather than be replaced.)
    // Untrimmed, like `id` and the contract `type`: the schema pins this with
    // `const: "1"`, so `' 1 '` is schema-invalid -- and trimming first turned it
    // into the supported version. Only an ABSENT value defaults.
    const protocolVersion = manifest.protocolVersion === undefined
      ? SDK_PROTOCOL_VERSION
      : manifest.protocolVersion;
    if (!/^[a-z][a-z0-9-]{0,63}$/.test(id)) {
      fail('invalid_manifest', 'manifest.id must be a lowercase game identifier');
    }
    // Code points, matching the schema's `maxLength: 64` (JSON Schema counts
    // code points), so the runtime is not stricter than its published contract.
    if (!version || [...version].length > 64) {
      fail('invalid_manifest', 'manifest.version is required and must not exceed 64 characters');
    }
    if (protocolVersion !== SDK_PROTOCOL_VERSION) {
      fail('incompatible_version', 'The game manifest uses an unsupported SDK protocol', {
        requested: protocolVersion,
        supported: SDK_PROTOCOL_VERSION,
      });
    }
    const requiredCapabilities = normalizeCapabilities(
      manifest.requiredCapabilities,
      'requiredCapabilities',
    );
    const optionalCapabilities = normalizeCapabilities(
      manifest.optionalCapabilities,
      'optionalCapabilities',
    ).filter((capability) => !requiredCapabilities.includes(capability));
    const missingMandatory = MANDATORY_CAPABILITIES.filter(
      (capability) => !requiredCapabilities.includes(capability),
    );
    if (missingMandatory.length) {
      fail('invalid_manifest', 'Mandatory capabilities must be declared as required', {
        missing: missingMandatory,
      });
    }
    const contracts = normalizeContracts(manifest.contracts);
    const leaderboards = normalizeLeaderboardDefinitions(manifest.leaderboards);
    const requestedCapabilities = new Set([...requiredCapabilities, ...optionalCapabilities]);
    const requestsLeaderboard = requestedCapabilities.has('leaderboard-local')
      || requestedCapabilities.has('leaderboard-server');
    if (requestsLeaderboard && !Object.keys(leaderboards).length) {
      fail('invalid_manifest', 'leaderboard capabilities require manifest.leaderboards definitions');
    }
    if (Object.keys(leaderboards).length && !requestsLeaderboard) {
      fail('invalid_manifest', 'manifest.leaderboards requires a leaderboard capability');
    }
    if (requestedCapabilities.has('quick-lines') && !requestedCapabilities.has('dialogue')) {
      fail('invalid_manifest', 'quick-lines requires the dialogue capability');
    }
    // Keep this in step with the `allOf` dependencies in
    // neko-minigame-manifest.schema.json. The schema is the published contract,
    // but THIS is the path an SDK connection actually runs, so a rule that
    // exists only there is not enforced at connect time.
    // `voice-input` only. Every voice.* command goes through
    // requireActiveRuntimeRoute(), which a game with no runtime API can never
    // establish, so the granted capability would be permanently unusable.
    // `speech-output` is deliberately NOT here: speech.speak() does not require
    // an active route (the host accepts it pre-route), so a game may narrate
    // without ever taking over the runtime lifecycle.
    const needsRuntime = RUNTIME_DEPENDENT_CAPABILITIES.some(
      (capability) => requestedCapabilities.has(capability),
    ) || CONTRACT_KINDS.some((kind) => Object.keys(contracts[kind]).length > 0);
    if (needsRuntime && !requestedCapabilities.has('runtime')) {
      fail('invalid_manifest', 'memory, context, server leaderboards, voice input, and game contracts require runtime');
    }
    return Object.freeze({
      id,
      version,
      protocolVersion,
      requiredCapabilities: Object.freeze(requiredCapabilities),
      optionalCapabilities: Object.freeze(optionalCapabilities),
      contracts,
      leaderboards,
    });
  }

  function contractPayloadTypeMatches(value, type) {
    if (type === 'null') return value === null;
    if (type === 'array') return Array.isArray(value);
    if (type === 'object') return plainObject(value);
    if (type === 'integer') return typeof value === 'number' && Number.isInteger(value);
    if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
    return typeof value === type;
  }

  function cloneAdditionalContractValue(value, fieldName, state, depth) {
    state.nodes += 1;
    if (state.nodes > MAX_CONTRACT_PAYLOAD_NODES || depth > 16) {
      fail('invalid_contract', `${fieldName} exceeds the contract payload complexity limit`, {
        limit: MAX_CONTRACT_PAYLOAD_NODES,
      });
    }
    if (value === null || typeof value === 'boolean' || typeof value === 'string') return value;
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) fail('invalid_contract', `${fieldName} contains a non-finite number`);
      return value;
    }
    if (Array.isArray(value)) {
      if (value.length > 256) fail('invalid_contract', `${fieldName} contains too many items`);
      return Object.freeze(value.map((item, index) => (
        cloneAdditionalContractValue(item, `${fieldName}[${index}]`, state, depth + 1)
      )));
    }
    if (!plainObject(value)) fail('invalid_contract', `${fieldName} contains an unsupported value`);
    const entries = Object.entries(value);
    if (entries.length > 128) fail('invalid_contract', `${fieldName} contains too many fields`);
    const result = {};
    for (const [key, item] of entries) {
      if (
        !key || key.length > 128
        || key === '__proto__' || key === 'prototype' || key === 'constructor'
      ) {
        fail('invalid_contract', `${fieldName} contains an invalid field`, { key });
      }
      result[key] = cloneAdditionalContractValue(item, `${fieldName}.${key}`, state, depth + 1);
    }
    return Object.freeze(result);
  }

  function validateContractValue(value, schema, fieldName, state, depth = 0) {
    state.nodes += 1;
    if (state.nodes > MAX_CONTRACT_PAYLOAD_NODES || depth > 16) {
      fail('invalid_contract', `${fieldName} exceeds the contract payload complexity limit`, {
        limit: MAX_CONTRACT_PAYLOAD_NODES,
      });
    }
    if (!contractPayloadTypeMatches(value, schema.type)) {
      fail('invalid_contract', `${fieldName} must match the declared ${schema.type} schema`);
    }
    // `===`, not `Object.is`: JSON has one zero, so a payload `-0` is valid
    // against an enum declaring `0` and serializes identically -- while
    // `Object.is(0, -0)` is false and rejected it. `Object.is` was only ever
    // needed for NaN, and non-finite enum values are already refused at
    // manifest time.
    if (schema.enum && !schema.enum.some((candidate) => candidate === value)) {
      fail('invalid_contract', `${fieldName} is not one of the declared enum values`);
    }
    if (schema.type === 'null' || schema.type === 'boolean') return value;
    if (schema.type === 'number' || schema.type === 'integer') {
      if (schema.minimum !== undefined && value < schema.minimum) {
        fail('invalid_contract', `${fieldName} is below the declared minimum`);
      }
      if (schema.maximum !== undefined && value > schema.maximum) {
        fail('invalid_contract', `${fieldName} exceeds the declared maximum`);
      }
      return value;
    }
    if (schema.type === 'string') {
      // Code points, not UTF-16 units. `minLength`/`maxLength` are declared in
      // the manifest with JSON Schema's vocabulary, where both count code
      // points, so `value.length` charges two units for every astral character
      // -- one emoji in a maxLength:16 field costs the same as two letters.
      // (The byte budgets that actually bound payload size are separate and
      // unchanged; this only affects the author-declared field length.)
      const codePointLength = [...value].length;
      if (codePointLength < schema.minLength || codePointLength > schema.maxLength) {
        fail('invalid_contract', `${fieldName} violates the declared string length`);
      }
      return value;
    }
    if (schema.type === 'array') {
      if (value.length < schema.minItems || value.length > schema.maxItems) {
        fail('invalid_contract', `${fieldName} violates the declared array length`);
      }
      // Index-walk, not `.map()`: map SKIPS holes, so a sparse array
      // (`new Array(3)`, or `a[5] = 1`) passed its declared item schema without
      // a single slot being validated, and the holes then serialize as `null`
      // -- a `null` smuggled past a declared `items: {type: 'string'}`.
      // Reading a hole yields `undefined`, which no declared type matches, so
      // it now fails as the invalid value it is.
      const validatedItems = [];
      for (let index = 0; index < value.length; index += 1) {
        validatedItems.push(validateContractValue(
          value[index],
          schema.items,
          `${fieldName}[${index}]`,
          state,
          depth + 1,
        ));
      }
      return Object.freeze(validatedItems);
    }
    const entries = Object.entries(value);
    const result = {};
    for (const requiredName of schema.required) {
      if (!Object.prototype.hasOwnProperty.call(value, requiredName)) {
        fail('invalid_contract', `${fieldName} is missing a required field`, { field: requiredName });
      }
    }
    for (const [key, item] of entries) {
      if (
        !key || key.length > 128
        || key === '__proto__' || key === 'prototype' || key === 'constructor'
      ) {
        fail('invalid_contract', `${fieldName} contains an invalid field`, { key });
      }
      // Own properties only. `schema.properties` is a plain object, so a
      // payload key named `toString` / `valueOf` / `hasOwnProperty` used to
      // resolve to the inherited Object.prototype method -- truthy, so it
      // was treated as a declared schema, and `additionalProperties: true`
      // never got its chance. The value then failed against an `undefined`
      // declared type with a message naming a schema nobody wrote.
      const propertySchema = Object.prototype.hasOwnProperty.call(schema.properties, key)
        ? schema.properties[key]
        : undefined;
      if (!propertySchema) {
        if (!schema.additionalProperties) {
          fail('invalid_contract', `${fieldName} contains an undeclared field`, { field: key });
        }
        result[key] = cloneAdditionalContractValue(item, `${fieldName}.${key}`, state, depth + 1);
        continue;
      }
      result[key] = validateContractValue(
        item,
        propertySchema,
        `${fieldName}.${key}`,
        state,
        depth + 1,
      );
    }
    return Object.freeze(result);
  }

  function normalizeContractPayload(value, schema, fieldName) {
    let serialized;
    try { serialized = JSON.stringify(value); }
    catch (_) { fail('invalid_contract', `${fieldName} must be JSON-compatible`); }
    if (serialized === undefined) fail('invalid_contract', `${fieldName} must be JSON-compatible`);
    const TextEncoderImpl = globalThis.TextEncoder;
    const byteLength = typeof TextEncoderImpl === 'function'
      ? new TextEncoderImpl().encode(serialized).byteLength
      : unescape(encodeURIComponent(serialized)).length;
    if (byteLength > MAX_CONTRACT_PAYLOAD_BYTES) {
      fail('invalid_contract', `${fieldName} exceeds the contract payload size limit`, {
        bytes: byteLength,
        limit: MAX_CONTRACT_PAYLOAD_BYTES,
      });
    }
    // Same reason as normalizeBoundedJson: the pre-check measured a projection
    // of the input, so re-measure what validation actually produced.
    const validated = validateContractValue(value, schema, fieldName, { nodes: 0 });
    const validatedBytes = jsonByteLength(validated);
    if (validatedBytes > MAX_CONTRACT_PAYLOAD_BYTES) {
      fail('invalid_contract', `${fieldName} exceeds the contract payload size limit`, {
        bytes: validatedBytes,
        limit: MAX_CONTRACT_PAYLOAD_BYTES,
      });
    }
    return validated;
  }

  function jsonByteLength(value) {
    let serialized;
    try { serialized = JSON.stringify(value); }
    catch (_) { return Number.POSITIVE_INFINITY; }
    if (serialized === undefined) return Number.POSITIVE_INFINITY;
    const TextEncoderImpl = globalThis.TextEncoder;
    return typeof TextEncoderImpl === 'function'
      ? new TextEncoderImpl().encode(serialized).byteLength
      : unescape(encodeURIComponent(serialized)).length;
  }

  function normalizeBoundedEnvelope(data, fieldName, maximumBytes, nestedKey = 'value') {
    // The write path measures the game's payload on its own; the read path used
    // to measure the same payload wrapped in a reply envelope against the
    // identical budget. Every unit of wrapper overhead was therefore charged
    // against the payload's own budget -- 33 bytes for {"ok":true,"found":true,
    // "value":...}, three extra clone nodes against the fixed 2048, and one
    // extra level against the fixed depth of 16 -- so a payload that stored
    // fine could be permanently unreadable. Give the nested payload exactly the
    // budget it was written under (fresh node counter, depth 0) and hold the
    // rest of the envelope to its own bound, so a host cannot smuggle bytes in
    // through a sibling field either.
    if (!plainObject(data) || !Object.prototype.hasOwnProperty.call(data, nestedKey)) {
      return normalizeBoundedJson(data, fieldName, maximumBytes);
    }
    const nested = data[nestedKey];
    // `undefined` under a present key is rejected here as `invalid_request`
    // rather than as the clone's `invalid_contract`; deliberate, and either way
    // it is a host that answered with something unrepresentable.
    const boundedNested = normalizeBoundedJson(nested, `${fieldName} ${nestedKey}`, maximumBytes);
    const envelope = { ...data };
    delete envelope[nestedKey];
    const boundedEnvelope = normalizeBoundedJson(envelope, fieldName, maximumBytes);
    return Object.freeze({ ...boundedEnvelope, [nestedKey]: boundedNested });
  }

  function normalizeBoundedJson(value, fieldName, maximumBytes = MAX_CONTRACT_PAYLOAD_BYTES) {
    let serialized;
    try { serialized = JSON.stringify(value); }
    catch (_) { fail('invalid_request', `${fieldName} must be JSON-compatible`); }
    if (serialized === undefined) fail('invalid_request', `${fieldName} must be JSON-compatible`);
    const TextEncoderImpl = globalThis.TextEncoder;
    const byteLength = typeof TextEncoderImpl === 'function'
      ? new TextEncoderImpl().encode(serialized).byteLength
      : unescape(encodeURIComponent(serialized)).length;
    if (byteLength > maximumBytes) {
      fail('invalid_request', `${fieldName} exceeds its size limit`, {
        bytes: byteLength,
        limit: maximumBytes,
      });
    }
    // Measure the CLONE, not just the input. The pre-check above serialises the
    // input, which honours toJSON(); the clone walks own enumerable properties
    // and does not. Any input whose two observations disagree -- a
    // non-enumerable toJSON returning something small, or an enumerable getter
    // that returns different values on successive reads -- would otherwise ship
    // a payload far larger than the advertised limit. Strings carry no length
    // cap of their own in the clone, so this byte gate is the only bound on
    // them. Honest inputs are unaffected: everything the clone would alter
    // (undefined, functions, symbols, non-finite numbers, non-plain objects) it
    // rejects instead, so its serialisation is byte-identical to the input's.
    const cloned = cloneAdditionalContractValue(value, fieldName, { nodes: 0 }, 0);
    const clonedBytes = jsonByteLength(cloned);
    if (clonedBytes > maximumBytes) {
      fail('invalid_request', `${fieldName} exceeds its size limit`, {
        bytes: clonedBytes,
        limit: maximumBytes,
      });
    }
    return cloned;
  }

  function normalizeContextScopes(value) {
    if (!Array.isArray(value) || !value.length || value.length > MAX_CONTEXT_SCOPES) {
      fail('invalid_request', `context scopes must contain between 1 and ${MAX_CONTEXT_SCOPES} items`);
    }
    const seen = new Set();
    const scopes = [];
    for (const rawScope of value) {
      const scope = String(rawScope || '').trim();
      if (!RUNTIME_EVENT_PATTERN.test(scope)) {
        fail('invalid_request', 'context scope is invalid', { scope });
      }
      if (!seen.has(scope)) {
        seen.add(scope);
        scopes.push(scope);
      }
    }
    return Object.freeze(scopes);
  }

  function normalizeMemorySubmission(value) {
    if (!plainObject(value)) fail('invalid_request', 'memory submission must be an object');
    const allowed = new Set(['events', 'state', 'result', 'summary']);
    const inputEntries = Object.entries(value).filter(([, item]) => item !== undefined);
    for (const [key] of inputEntries) {
      if (!allowed.has(key)) fail('invalid_request', 'memory submission contains an unsupported field', { key });
    }
    if (!inputEntries.length) fail('invalid_request', 'memory submission must not be empty');
    if (value.events !== undefined && (!Array.isArray(value.events) || value.events.length > 64)) {
      fail('invalid_request', 'memory events must be an array with at most 64 items');
    }
    return normalizeBoundedJson(value, 'memory submission');
  }

  // The local leaderboard persists through this same storage channel under a
  // `leaderboards/` prefix, so a public storage write can silently overwrite or
  // delete a board's state -- and the public path takes none of the Web Locks
  // that serialise leaderboard read-modify-write, so it can also drop a
  // concurrent submission. Reserve the prefix on the single-key operations.
  // `list`/`clear` intentionally still see it: their documented meaning is "the
  // whole namespace", and pretending otherwise would hide real usage from the
  // game's own quota accounting.
  const RESERVED_STORAGE_PREFIX = 'leaderboards/';

  function normalizeStorageKey(value, fieldName = 'storage key', { allowEmpty = false, allowReserved = false } = {}) {
    const key = String(value || '').trim();
    if ((!allowEmpty && !key) || key.length > 128 || (key && !/^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$/.test(key))) {
      fail('invalid_request', `${fieldName} is invalid`);
    }
    if (!allowReserved && key.startsWith(RESERVED_STORAGE_PREFIX)) {
      fail('invalid_request', `${fieldName} uses the reserved "${RESERVED_STORAGE_PREFIX}" prefix owned by the local leaderboard`, {
        key,
      });
    }
    return key;
  }

  function leaderboardDefinition(manifest, boardIdInput, operation) {
    const boardId = String(boardIdInput || '').trim();
    // Own properties only: `manifest.leaderboards` is a plain object, so a
    // board id of `constructor` (or `toString`, ...) resolved to the inherited
    // Object.prototype member -- truthy, so an undeclared board passed this
    // check and every field read off that "definition" was nonsense.
    const declared = Object.prototype.hasOwnProperty.call(
      manifest.leaderboards, boardId,
    );
    const definition = declared ? manifest.leaderboards[boardId] : undefined;
    if (!definition) {
      fail('invalid_request', 'Leaderboard board is not declared by the game manifest', {
        operation,
        boardId,
      });
    }
    return { boardId, definition };
  }

  function normalizeLeaderboardEntryData(value, definition, fieldName = 'leaderboard entry') {
    if (!plainObject(value)) fail('invalid_request', `${fieldName} must be an object`);
    const data = normalizeBoundedJson(value, fieldName, MAX_LEADERBOARD_ENTRY_BYTES);
    // The declared score field must BE a number. `Number(null)`, `Number(false)`
    // and `Number('')` are all 0, so a malformed entry used to be persisted and
    // ranked as a legitimate zero instead of rejected -- and the server facade
    // forwards the original non-numeric value on past this same check.
    const rawScore = data[definition.scoreField];
    const score = typeof rawScore === 'number' ? rawScore : Number.NaN;
    if (!Number.isFinite(score) || Math.abs(score) > 1e12) {
      fail('invalid_request', `${fieldName}.${definition.scoreField} must be a finite score`);
    }
    return Object.freeze({ data, score });
  }

  function leaderboardEntryCompare(left, right, definition, sort = 'rank') {
    if (sort === 'recent') {
      return Number(right.submittedAt || 0) - Number(left.submittedAt || 0)
        || String(right.id || '').localeCompare(String(left.id || ''));
    }
    const scoreDelta = Number(left.score || 0) - Number(right.score || 0);
    if (scoreDelta) return definition.order === 'ascending' ? scoreDelta : -scoreDelta;
    return Number(left.submittedAt || 0) - Number(right.submittedAt || 0)
      || String(left.id || '').localeCompare(String(right.id || ''));
  }

  function normalizeStoredLeaderboardEntry(value, definition) {
    if (!plainObject(value) || !plainObject(value.data)) return null;
    const id = String(value.id || '').trim();
    const submittedAt = Number(value.submittedAt);
    const score = Number(value.score);
    if (!id || id.length > 128 || !Number.isFinite(submittedAt) || submittedAt < 0
        || !Number.isFinite(score) || Math.abs(score) > 1e12) return null;
    try {
      const normalized = normalizeLeaderboardEntryData(value.data, definition, 'stored leaderboard entry');
      if (normalized.score !== score) return null;
      return Object.freeze({ id, submittedAt, score, data: normalized.data });
    } catch (_) {
      return null;
    }
  }

  function normalizeStoredLeaderboardState(value, definition) {
    const rawEntries = plainObject(value) && Array.isArray(value.entries) ? value.entries : [];
    const entries = [];
    const seen = new Set();
    for (const rawEntry of rawEntries.slice(0, definition.maxEntries)) {
      const entry = normalizeStoredLeaderboardEntry(rawEntry, definition);
      if (!entry || seen.has(entry.id)) continue;
      seen.add(entry.id);
      entries.push(entry);
    }
    return Object.freeze({ version: 1, entries: Object.freeze(entries) });
  }

  function normalizeLeaderboardListOptions(value, { allowQuery = true } = {}) {
    const input = value ?? {};
    if (!plainObject(input)) fail('invalid_request', 'leaderboard list options must be an object');
    // `query` is shared with the server leaderboard, where it is forwarded to
    // the host as the request payload. The local board has no matching
    // semantics defined anywhere -- not in the .d.ts, the README or the
    // manifest schema -- so accepting it locally and dropping it would return
    // an unfiltered page that looks filtered. Reject instead of inventing a
    // meaning that a future server implementation would then have to match.
    const allowed = new Set(allowQuery ? ['sort', 'limit', 'offset', 'query'] : ['sort', 'limit', 'offset']);
    for (const key of Object.keys(input)) {
      if (!allowed.has(key)) fail('invalid_request', 'leaderboard list options contain an unsupported field', { key });
    }
    const sort = String(input.sort || 'rank').trim();
    if (!['rank', 'recent'].includes(sort)) fail('invalid_request', 'leaderboard list sort is unsupported');
    const limit = input.limit === undefined ? 20 : Number(input.limit);
    const offset = input.offset === undefined ? 0 : Number(input.offset);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      fail('invalid_request', 'leaderboard list limit must be an integer between 1 and 100');
    }
    if (!Number.isInteger(offset) || offset < 0 || offset > 100000) {
      fail('invalid_request', 'leaderboard list offset must be an integer between 0 and 100000');
    }
    const query = input.query === undefined
      ? Object.freeze({})
      : normalizeBoundedJson(input.query, 'leaderboard query', MAX_LEADERBOARD_ENTRY_BYTES);
    return Object.freeze({ sort, limit, offset, query });
  }

  function assertNoForbiddenDialogueFields(value, fieldName = 'dialogue payload', depth = 0) {
    if (depth > 16 || value == null || typeof value !== 'object') return;
    const forbidden = new Set(depth === 0
      ? [
        'system_prompt', 'systemPrompt', 'prompt', 'messages', 'history',
        'api_key', 'apiKey', 'provider', 'model', 'launch_ticket', 'launchTicket',
      ]
      : ['system_prompt', 'systemPrompt', 'api_key', 'apiKey', 'launch_ticket', 'launchTicket']);
    for (const [key, item] of Object.entries(value)) {
      if (forbidden.has(key)) {
        fail('invalid_request', `${fieldName} cannot provide host-controlled dialogue fields`, {
          field: key,
        });
      }
      assertNoForbiddenDialogueFields(item, `${fieldName}.${key}`, depth + 1);
    }
  }

  function normalizeAuthorManagedDialoguePrompt(value) {
    if (!plainObject(value)) {
      fail('invalid_request', 'dialogue prompt must be an object');
    }
    const allowedPromptFields = new Set(['mode', 'messages']);
    for (const key of Object.keys(value)) {
      if (!allowedPromptFields.has(key)) {
        fail('invalid_request', 'dialogue prompt contains an unsupported field', { field: key });
      }
    }
    if (value.mode !== 'author-managed') {
      fail('invalid_request', 'dialogue prompt.mode must be author-managed');
    }
    if (
      !Array.isArray(value.messages)
      || !value.messages.length
      || value.messages.length > MAX_AUTHOR_PROMPT_MESSAGES
    ) {
      fail(
        'invalid_request',
        `dialogue prompt.messages must contain between 1 and ${MAX_AUTHOR_PROMPT_MESSAGES} items`,
      );
    }

    const allowedRoles = new Set(['system', 'user', 'assistant']);
    let totalChars = 0;
    // Index-walk, not `.map()`: map SKIPS holes, so a sparse array
    // (`new Array(1)`) was accepted and frozen without a single slot being
    // validated, and JSON transport then turned the hole into `null` -- the
    // backend rejected with HTTP 400 what the SDK had just admitted locally.
    const messages = Array.from({ length: value.messages.length }, (_unused, index) => {
      const message = value.messages[index];
      if (!plainObject(message)) {
        fail('invalid_request', `dialogue prompt.messages[${index}] must be an object`);
      }
      const fields = Object.keys(message);
      if (fields.some((field) => field !== 'role' && field !== 'content')) {
        fail('invalid_request', 'dialogue prompt message contains an unsupported field', {
          index,
        });
      }
      const role = String(message.role || '');
      if (!allowedRoles.has(role)) {
        fail('invalid_request', 'dialogue prompt message role is invalid', { index, role });
      }
      if (typeof message.content !== 'string' || !message.content.trim()) {
        fail('invalid_request', 'dialogue prompt message content must be a non-empty string', {
          index,
        });
      }
      if (message.content.length > MAX_AUTHOR_PROMPT_CONTENT_CHARS) {
        fail('invalid_request', 'dialogue prompt message content exceeds its size limit', {
          index,
          limit: MAX_AUTHOR_PROMPT_CONTENT_CHARS,
        });
      }
      totalChars += message.content.length;
      if (totalChars > MAX_AUTHOR_PROMPT_TOTAL_CHARS) {
        fail('invalid_request', 'dialogue prompt messages exceed their total size limit', {
          limit: MAX_AUTHOR_PROMPT_TOTAL_CHARS,
        });
      }
      return Object.freeze({ role, content: message.content });
    });

    return Object.freeze({
      mode: 'author-managed',
      messages: Object.freeze(messages),
    });
  }

  function normalizedRequestTimeout(
    value,
    fieldName = 'timeoutMs',
    maximum = MAX_REQUEST_TIMEOUT_MS,
  ) {
    if (value === undefined) return DEFAULT_CONNECT_TIMEOUT_MS;
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric < MIN_CONNECT_TIMEOUT_MS || numeric > maximum) {
      fail(
        'invalid_request',
        `${fieldName} must be between ${MIN_CONNECT_TIMEOUT_MS} and ${maximum}`,
      );
    }
    return Math.floor(numeric);
  }

  function normalizeHandshakeRegistration(value, manifest) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_handshake', 'The host did not provide a registration result');
    }
    const mode = String(value.mode || '').trim();
    if (mode !== 'registered' && mode !== 'development') {
      fail('invalid_handshake', 'The host registration mode is invalid', { mode });
    }
    // Compared EXACTLY against the manifest, so neither side is trimmed: this
    // check exists to catch a host minting a registration for a different game
    // or version than the manifest asked for, and normalizing one side first is
    // precisely how a mismatch becomes invisible. (`manifest.version` is now
    // kept as declared, so trimming here would also break the round-trip for a
    // schema-valid padded version.)
    const gameId = String(value.gameId || value.game_id || '');
    const version = String(value.version || '');
    const publisherId = String(value.publisherId || value.publisher_id || '').trim();
    if (gameId !== manifest.id || version !== manifest.version) {
      fail('invalid_handshake', 'The host registration does not match the game manifest', {
        requestedGameId: manifest.id,
        registeredGameId: gameId,
        requestedVersion: manifest.version,
        registeredVersion: version,
      });
    }
    // 128, matching what the trusted host actually hands back: both the
    // bootstrap (neko-minigame-same-origin-bootstrap.js) and the host
    // (neko-minigame-same-origin-host.js) clamp publisherId to 128, so a
    // stricter bound here rejects a registration the host already accepted and
    // fails the handshake on a value the game never chose.
    if (publisherId.length > 128) {
      fail('invalid_handshake', 'The host publisher identifier is too long');
    }
    return Object.freeze({ mode, gameId, version, publisherId });
  }

  function normalizeHandshakeResponse(value, manifest) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_handshake', 'The host returned an invalid connection handshake');
    }
    if (value.accepted === false || value.ok === false) {
      const requestedCode = String(value.code || value.reason || 'request_failed').trim();
      const code = PUBLIC_TRANSPORT_ERROR_CODES.includes(requestedCode)
        ? requestedCode
        : 'request_failed';
      fail(code, String(value.message || 'The host rejected the mini-game connection').slice(0, 500), {
        operation: 'connect',
      });
    }
    const protocolVersion = String(value.protocolVersion || value.protocol_version || '').trim();
    if (protocolVersion !== SDK_PROTOCOL_VERSION || protocolVersion !== manifest.protocolVersion) {
      fail('incompatible_version', 'The host selected an incompatible SDK protocol', {
        requested: manifest.protocolVersion,
        selected: protocolVersion,
      });
    }
    const hostVersion = String(value.hostVersion || value.host_version || '').trim();
    if (!hostVersion || hostVersion.length > 64) {
      fail('invalid_handshake', 'The host version is missing or invalid');
    }
    const grantedCapabilities = normalizeCapabilities(
      value.grantedCapabilities ?? value.granted_capabilities,
      'host grantedCapabilities',
    );
    return Object.freeze({
      protocolVersion,
      hostVersion,
      registration: normalizeHandshakeRegistration(value.registration, manifest),
      grantedCapabilities: Object.freeze(grantedCapabilities),
    });
  }

  async function negotiateConnection(manifest, transport, options, windowImpl) {
    if (typeof transport.connectGame !== 'function') {
      fail('transport_unavailable', 'The host transport does not support the required handshake');
    }
    const AbortControllerImpl = windowImpl.AbortController || globalThis.AbortController;
    if (typeof AbortControllerImpl !== 'function') {
      fail('transport_unavailable', 'AbortController is unavailable for the host handshake');
    }
    const timeoutMs = normalizedRequestTimeout(
      options.connectTimeoutMs,
      'connectTimeoutMs',
      MAX_CONNECT_TIMEOUT_MS,
    );
    const externalSignal = options.signal || null;
    if (externalSignal?.aborted) {
      fail('cancelled', 'The host handshake was cancelled', { operation: 'connect' });
    }
    const controller = new AbortControllerImpl();
    const setTimer = windowImpl.setTimeout?.bind(windowImpl) || globalThis.setTimeout;
    const clearTimer = windowImpl.clearTimeout?.bind(windowImpl) || globalThis.clearTimeout;
    const request = Object.freeze({
      sdkVersion: SDK_VERSION,
      protocolVersions: Object.freeze([SDK_PROTOCOL_VERSION]),
      manifest,
    });

    return new Promise((resolve, reject) => {
      let settled = false;
      let timer = null;
      const cleanup = () => {
        if (timer != null) clearTimer(timer);
        externalSignal?.removeEventListener?.('abort', onExternalAbort);
      };
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        cleanup();
        callback(value);
      };
      const onExternalAbort = () => {
        try { controller.abort(); } catch (_) { /* already aborted */ }
        finish(reject, new NekoMiniGameError(
          'cancelled',
          'The host handshake was cancelled',
          { operation: 'connect' },
        ));
      };
      externalSignal?.addEventListener?.('abort', onExternalAbort, { once: true });
      timer = setTimer(() => {
        try { controller.abort(); } catch (_) { /* already aborted */ }
        finish(reject, new NekoMiniGameError(
          'timeout',
          'The host handshake timed out',
          { operation: 'connect' },
        ));
      }, timeoutMs);
      Promise.resolve()
        .then(() => transport.connectGame(request, { signal: controller.signal, timeoutMs }))
        .then(
          (response) => {
            try { finish(resolve, normalizeHandshakeResponse(response, manifest)); }
            catch (error) { finish(reject, error); }
          },
          (error) => finish(reject, normalizeTransportError(error, 'connect')),
        );
    });
  }

  function supportedByTransport(transport, capability) {
    switch (capability) {
      case 'runtime':
        return [
          'start', 'end', 'heartbeat', 'drain',
          'resetRuntime', 'getRuntimeState', 'applyRuntimeState',
        ].every((method) => typeof transport[method] === 'function');
      case 'dialogue':
        return typeof transport.requestDialogue === 'function';
      case 'quick-lines':
        return typeof transport.getQuickLines === 'function';
      case 'logging':
        return !!transport.logger && [
          'log', 'info', 'warn', 'error', 'enable',
          'enableAfterRouteStart', 'flush', 'reset',
        ].every((method) => typeof transport.logger[method] === 'function');
      case 'voice-input':
        return typeof transport.startVoiceControlBridge === 'function'
          && typeof transport.requestVoiceControl === 'function'
          && typeof transport.stopVoiceControlBridge === 'function';
      case 'avatar-renderer':
        return typeof transport.mountAvatar === 'function';
      case 'audio':
        return typeof transport.mountAudio === 'function';
      case 'speech-output':
        // `speech.mirror()` is on the public SpeechOutput interface unconditionally,
        // so a transport that implements speaking, preloading and the playback
        // bridge but not mirroring used to satisfy even a REQUIRED grant -- and
        // then every mirror call on that connected client failed
        // `transport_unavailable`. Negotiate on the whole surface the capability
        // hands out, not part of it.
        return typeof transport.requestSpeechOutput === 'function'
          && typeof transport.preloadSpeechOutput === 'function'
          && typeof transport.mirrorSpeechOutput === 'function'
          && typeof transport.startSpeechOutputBridge === 'function'
          && typeof transport.stopSpeechOutputBridge === 'function';
      case 'context-read':
        return typeof transport.readGameContext === 'function';
      case 'memory':
        return typeof transport.configureGameMemoryConsent === 'function'
          && typeof transport.submitGameMemory === 'function';
      case 'storage':
        return typeof transport.requestGameStorage === 'function';
      case 'leaderboard-local':
        return typeof transport.requestGameStorage === 'function'
          && typeof transport.runGameStorageExclusive === 'function';
      case 'leaderboard-server':
        return typeof transport.submitServerLeaderboard === 'function'
          && typeof transport.listServerLeaderboard === 'function'
          && typeof transport.getServerLeaderboardBest === 'function';
      default:
        return false;
    }
  }

  function finiteNumber(value, fieldName, { minimum, maximum }) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric < minimum || numeric > maximum) {
      fail('invalid_request', `${fieldName} must be between ${minimum} and ${maximum}`);
    }
    return numeric;
  }

  function normalizeAvatarModel(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_request', 'avatar model must be an object');
    }
    const type = String(value.type || '').trim().toLowerCase();
    const path = String(value.path || '').trim();
    if (!AVATAR_TYPES.includes(type)) {
      fail('invalid_request', 'avatar model.type must be live2d or vrm', { type });
    }
    if (!path || path.length > 2048) {
      fail('invalid_request', 'avatar model.path is required and must not exceed 2048 characters');
    }
    return Object.freeze({ type, path });
  }

  function normalizeAvatarConfig(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_request', 'avatar mount config must be an object');
    }
    const slot = String(value.slot || '').trim();
    if (!AVATAR_SLOT_PATTERN.test(slot)) {
      fail('invalid_request', 'avatar slot must be a lowercase identifier');
    }

    const viewportInput = value.viewport || {};
    const viewportMode = String(viewportInput.mode || '').trim();
    if (!AVATAR_VIEWPORT_MODES.includes(viewportMode)) {
      fail('invalid_request', 'avatar viewport.mode is invalid', { mode: viewportMode });
    }
    const viewport = { mode: viewportMode };
    if (viewportMode === 'fixed') {
      viewport.width = finiteNumber(viewportInput.width, 'avatar viewport.width', {
        minimum: 16,
        maximum: 8192,
      });
      viewport.height = finiteNumber(viewportInput.height, 'avatar viewport.height', {
        minimum: 16,
        maximum: 8192,
      });
    }

    const fitInput = value.fit || {};
    const fitMode = String(fitInput.mode || 'contain').trim();
    const align = String(fitInput.align || 'center').trim();
    if (!AVATAR_FIT_MODES.includes(fitMode)) {
      fail('invalid_request', 'avatar fit.mode is invalid', { mode: fitMode });
    }
    if (!AVATAR_ALIGNMENTS.includes(align)) {
      fail('invalid_request', 'avatar fit.align is invalid', { align });
    }
    const fit = Object.freeze({
      mode: fitMode,
      align,
      padding: finiteNumber(fitInput.padding ?? 0, 'avatar fit.padding', {
        minimum: 0,
        maximum: 1024,
      }),
      scaleMultiplier: finiteNumber(
        fitInput.scaleMultiplier ?? 1,
        'avatar fit.scaleMultiplier',
        { minimum: 0.05, maximum: 8 },
      ),
    });

    const resizeInput = value.resize || {};
    const resizeMode = String(resizeInput.mode || viewportMode).trim();
    if (!AVATAR_VIEWPORT_MODES.includes(resizeMode)) {
      fail('invalid_request', 'avatar resize.mode is invalid', { mode: resizeMode });
    }
    if (resizeMode !== viewportMode) {
      fail('invalid_request', 'avatar viewport.mode and resize.mode must match', {
        viewportMode,
        resizeMode,
      });
    }

    return Object.freeze({
      slot,
      model: normalizeAvatarModel(value.model),
      viewport: Object.freeze(viewport),
      fit,
      resize: Object.freeze({ mode: resizeMode }),
    });
  }

  function normalizeAvatarFocus(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_request', 'avatar focus point must be an object');
    }
    return Object.freeze({
      x: finiteNumber(value.x, 'avatar focus.x', { minimum: -100000, maximum: 100000 }),
      y: finiteNumber(value.y, 'avatar focus.y', { minimum: -100000, maximum: 100000 }),
    });
  }

  function cloneAudioContractValue(value, fieldName, state, depth = 0) {
    state.nodes += 1;
    if (state.nodes > state.maxNodes || depth > 12) {
      fail('invalid_request', `${fieldName} exceeds the audio resource complexity limit`, {
        limit: state.maxNodes,
      });
    }
    if (value == null || typeof value === 'boolean') return value;
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) fail('invalid_request', `${fieldName} contains a non-finite number`);
      return value;
    }
    if (typeof value === 'string') {
      state.characters += value.length;
      if (value.length > 2048 || state.characters > state.maxCharacters) {
        fail('invalid_request', `${fieldName} exceeds the audio string size limit`, {
          limit: state.maxCharacters,
        });
      }
      return value;
    }
    if (Array.isArray(value)) {
      if (value.length > MAX_AUDIO_COLLECTION_ITEMS) {
        fail('invalid_request', `${fieldName} contains too many audio items`, {
          limit: MAX_AUDIO_COLLECTION_ITEMS,
        });
      }
      return Object.freeze(value.map((item, index) => (
        cloneAudioContractValue(item, `${fieldName}[${index}]`, state, depth + 1)
      )));
    }
    if (typeof value !== 'object') {
      fail('invalid_request', `${fieldName} must contain only JSON-compatible audio data`);
    }
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      fail('invalid_request', `${fieldName} contains an unsupported object type`);
    }
    const entries = Object.entries(value);
    if (entries.length > MAX_AUDIO_COLLECTION_ITEMS) {
      fail('invalid_request', `${fieldName} contains too many audio fields`, {
        limit: MAX_AUDIO_COLLECTION_ITEMS,
      });
    }
    const result = {};
    for (const [key, item] of entries) {
      if (key === '__proto__' || key === 'prototype' || key === 'constructor' || key.length > 128) {
        fail('invalid_request', `${fieldName} contains an invalid audio field`, { key });
      }
      if (item === undefined) continue;
      state.characters += key.length;
      result[key] = cloneAudioContractValue(item, `${fieldName}.${key}`, state, depth + 1);
    }
    return Object.freeze(result);
  }

  function normalizeAudioContractValue(value, fieldName, options = {}) {
    return cloneAudioContractValue(value, fieldName, {
      nodes: 0,
      characters: 0,
      maxNodes: options.maxNodes || MAX_AUDIO_RESOURCE_NODES,
      maxCharacters: options.maxCharacters || MAX_AUDIO_RESOURCE_CHARS,
    });
  }

  function normalizeAudioResources(value, fieldName = 'audio resources') {
    const normalized = normalizeAudioContractValue(value || {}, fieldName);
    if (!normalized || typeof normalized !== 'object' || Array.isArray(normalized)) {
      fail('invalid_request', `${fieldName} must be an object`);
    }
    return normalized;
  }

  function normalizeAudioValue(value, fieldName) {
    return normalizeAudioContractValue(value, fieldName, {
      maxNodes: 512,
      maxCharacters: 128 * 1024,
    });
  }

  function optionalBoundedNumber(value, fieldName, minimum, maximum) {
    if (value === undefined) return undefined;
    return finiteNumber(value, fieldName, { minimum, maximum });
  }

  function normalizeAudioMountConfig(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_request', 'audio mount config must be an object');
    }
    const slot = String(value.slot || '').trim();
    if (!AUDIO_SLOT_PATTERN.test(slot)) {
      fail('invalid_request', 'audio slot must be a lowercase identifier');
    }
    const settingsInput = value.settings || {};
    if (!settingsInput || typeof settingsInput !== 'object' || Array.isArray(settingsInput)) {
      fail('invalid_request', 'audio settings must be an object');
    }
    const settings = {};
    const numericSettings = [
      ['fadeMs', 0, 10000],
      ['bgmVolume', 0, 1],
      ['sfxVolume', 0, 1],
      ['maxConcurrent', 1, 64],
      ['maxPreloadEntries', 1, 512],
      ['maxPlaylistHistory', 1, 256],
      ['maxEndWaiters', 1, 64],
    ];
    for (const [name, minimum, maximum] of numericSettings) {
      const normalized = optionalBoundedNumber(
        settingsInput[name],
        `audio settings.${name}`,
        minimum,
        maximum,
      );
      if (normalized !== undefined) settings[name] = normalized;
    }
    if (settingsInput.persistVolume !== undefined) {
      settings.persistVolume = settingsInput.persistVolume !== false;
    }
    return Object.freeze({
      slot,
      resources: normalizeAudioResources(value.resources || {}),
      settings: Object.freeze(settings),
    });
  }

  function cloneSpeechEventValue(value, fieldName, state, depth = 0) {
    state.nodes += 1;
    if (state.nodes > MAX_SPEECH_EVENT_NODES || depth > 10) {
      fail('invalid_request', `${fieldName} exceeds the speech event complexity limit`, {
        limit: MAX_SPEECH_EVENT_NODES,
      });
    }
    if (value == null || typeof value === 'boolean') return value;
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) fail('invalid_request', `${fieldName} contains a non-finite number`);
      return value;
    }
    if (typeof value === 'string') {
      state.characters += value.length;
      if (value.length > 2048 || state.characters > MAX_SPEECH_EVENT_CHARS) {
        fail('invalid_request', `${fieldName} exceeds the speech event size limit`, {
          limit: MAX_SPEECH_EVENT_CHARS,
        });
      }
      return value;
    }
    if (Array.isArray(value)) {
      if (value.length > 128) {
        fail('invalid_request', `${fieldName} contains too many speech event items`, { limit: 128 });
      }
      return Object.freeze(value.map((item, index) => (
        cloneSpeechEventValue(item, `${fieldName}[${index}]`, state, depth + 1)
      )));
    }
    if (typeof value !== 'object') {
      fail('invalid_request', `${fieldName} must contain only JSON-compatible speech event data`);
    }
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      fail('invalid_request', `${fieldName} contains an unsupported object type`);
    }
    const entries = Object.entries(value);
    if (entries.length > 128) {
      fail('invalid_request', `${fieldName} contains too many speech event fields`, { limit: 128 });
    }
    const result = {};
    for (const [key, item] of entries) {
      if (key === '__proto__' || key === 'prototype' || key === 'constructor' || key.length > 128) {
        fail('invalid_request', `${fieldName} contains an invalid speech event field`, { key });
      }
      if (item === undefined) continue;
      state.characters += key.length;
      result[key] = cloneSpeechEventValue(item, `${fieldName}.${key}`, state, depth + 1);
    }
    return Object.freeze(result);
  }

  function normalizeSpeechEvent(value) {
    if (value === undefined || value === null) return Object.freeze({});
    const normalized = cloneSpeechEventValue(value, 'speech event', {
      nodes: 0,
      characters: 0,
    });
    if (!normalized || typeof normalized !== 'object' || Array.isArray(normalized)) {
      fail('invalid_request', 'speech event must be an object');
    }
    return normalized;
  }

  function boundedSpeechString(value, fieldName, maximum, fallback = '') {
    const normalized = String(value ?? fallback).trim();
    if (normalized.length > maximum) {
      fail('invalid_request', `${fieldName} must not exceed ${maximum} characters`);
    }
    return normalized;
  }

  function safeSpeechTransportSource(value) {
    return String(value || '').trim().slice(0, 64);
  }

  function safeSpeechPlaybackNumber(value) {
    const numeric = Number(value || 0);
    return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
  }

  function normalizeSpeechRequest(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_request', 'speech request must be an object');
    }
    const text = boundedSpeechString(value.text, 'speech text', MAX_SPEECH_TEXT_CHARS);
    if (!text) fail('invalid_request', 'speech text is required');
    const priority = value.priority === undefined
      ? 4
      : finiteNumber(value.priority, 'speech priority', { minimum: 0, maximum: 9 });
    if (!Number.isInteger(priority)) {
      fail('invalid_request', 'speech priority must be an integer between 0 and 9');
    }
    const relativeGain = value.relativeGain === undefined
      ? 1
      : finiteNumber(value.relativeGain, 'speech relative gain', { minimum: 0, maximum: 2 });
    const language = boundedSpeechString(value.language, 'speech language', 32);
    const renderLanguage = boundedSpeechString(value.renderLanguage, 'speech render language', 32);
    if (language && !/^[A-Za-z0-9-]+$/.test(language)) {
      fail('invalid_request', 'speech language must be a BCP-47-style identifier');
    }
    if (renderLanguage && !/^[A-Za-z0-9-]+$/.test(renderLanguage)) {
      fail('invalid_request', 'speech render language must be a BCP-47-style identifier');
    }
    for (const field of ['mirrorText', 'emitTurnEnd']) {
      if (value[field] !== undefined && typeof value[field] !== 'boolean') {
        fail('invalid_request', `speech ${field} must be a boolean when provided`);
      }
    }
    const event = normalizeSpeechEvent(value.event);
    return Object.freeze({
      text,
      requestId: boundedSpeechString(value.requestId, 'speech requestId', 128),
      source: boundedSpeechString(value.source, 'speech source', 64, 'game'),
      eventKey: boundedSpeechString(value.eventKey || event.kind, 'speech eventKey', 128),
      priority,
      relativeGain,
      interruptExisting: value.interruptExisting === true,
      reuseSynthesizedAudio: value.reuseSynthesizedAudio === true,
      mirrorText: value.mirrorText,
      emitTurnEnd: value.emitTurnEnd,
      reason: boundedSpeechString(value.reason, 'speech reason', 128),
      language,
      renderLanguage,
      event,
    });
  }

  function normalizeSpeechMirrorRequest(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail('invalid_request', 'speech mirror request must be an object');
    }
    const text = boundedSpeechString(value.text, 'speech mirror text', MAX_SPEECH_TEXT_CHARS);
    if (!text) fail('invalid_request', 'speech mirror text is required');
    if (value.finalizeTurn !== undefined && typeof value.finalizeTurn !== 'boolean') {
      fail('invalid_request', 'speech mirror finalizeTurn must be a boolean when provided');
    }
    return Object.freeze({
      text,
      requestId: boundedSpeechString(value.requestId, 'speech mirror requestId', 128),
      turnId: boundedSpeechString(value.turnId, 'speech mirror turnId', 128),
      source: boundedSpeechString(value.source, 'speech mirror source', 64, 'game'),
      finalizeTurn: value.finalizeTurn,
      event: normalizeSpeechEvent(value.event),
    });
  }

  function normalizeSpeechPreloadRequest(value, options = {}) {
    const input = Array.isArray(value) ? value : [value];
    if (!input.length || input.length > MAX_SPEECH_PRELOAD_LINES) {
      fail('invalid_request', 'speech preload lines must contain between 1 and 32 items', {
        limit: MAX_SPEECH_PRELOAD_LINES,
      });
    }
    const lines = [];
    const seen = new Set();
    let characters = 0;
    for (const item of input) {
      if (typeof item !== 'string') {
        fail('invalid_request', 'speech preload lines must contain only strings');
      }
      const line = boundedSpeechString(item, 'speech preload line', MAX_SPEECH_TEXT_CHARS);
      if (!line) fail('invalid_request', 'speech preload line must not be empty');
      if (seen.has(line)) continue;
      seen.add(line);
      characters += line.length;
      if (characters > MAX_SPEECH_PRELOAD_CHARS) {
        fail('invalid_request', 'speech preload text exceeds the batch size limit', {
          limit: MAX_SPEECH_PRELOAD_CHARS,
        });
      }
      lines.push(line);
    }
    const language = boundedSpeechString(options.language, 'speech preload language', 32);
    const renderLanguage = boundedSpeechString(
      options.renderLanguage,
      'speech preload render language',
      32,
    );
    if (language && !/^[A-Za-z0-9-]+$/.test(language)) {
      fail('invalid_request', 'speech preload language must be a BCP-47-style identifier');
    }
    if (renderLanguage && !/^[A-Za-z0-9-]+$/.test(renderLanguage)) {
      fail('invalid_request', 'speech preload render language must be a BCP-47-style identifier');
    }
    return Object.freeze({
      lines: Object.freeze(lines),
      language,
      renderLanguage,
    });
  }

  function normalizeSpeechPlaybackState(raw, source, metadata = null, now = Date.now()) {
    if (!raw || typeof raw !== 'object') return null;
    const updatedAt = Number(raw.updatedAt || raw.updated_at || 0);
    if (!Number.isFinite(updatedAt) || updatedAt <= 0) return null;
    const ageMs = Math.max(0, Number(now) - updatedAt);
    const rawRemainingSeconds = Math.max(
      0,
      Math.min(MAX_SPEECH_PLAYBACK_SECONDS, Number(raw.remainingSeconds || raw.remaining_seconds || 0) || 0),
    );
    const audioContextState = boundedSpeechString(
      raw.audioContextState || raw.audio_context_state,
      'speech audio context state',
      32,
    );
    const remainingSeconds = audioContextState === 'suspended'
      ? rawRemainingSeconds
      : Math.max(0, rawRemainingSeconds - ageMs / 1000);
    const pendingAudioWork = raw.pendingAudioWork === true || raw.pending_audio_work === true;
    if (ageMs > SPEECH_PLAYBACK_ABSOLUTE_STALE_MS) return null;
    if (ageMs > SPEECH_PLAYBACK_STALE_MS && remainingSeconds <= 0.5) return null;
    const speechId = boundedSpeechString(raw.speechId || raw.speech_id, 'speech playback id', 128);
    const priorityValue = metadata?.priority ?? raw.priority;
    const priority = Number.isInteger(Number(priorityValue))
      ? Math.max(0, Math.min(9, Number(priorityValue)))
      : null;
    return Object.freeze({
      active: raw.active === true && (remainingSeconds > 0.05 || pendingAudioWork),
      speechId,
      turnId: boundedSpeechString(raw.turnId || raw.turn_id, 'speech turn id', 128),
      playbackTurnId: boundedSpeechString(
        raw.playbackTurnId || raw.playback_turn_id,
        'speech playback turn id',
        128,
      ),
      remainingSeconds,
      pendingAudioWork,
      audioContextState,
      audioContextTime: safeSpeechPlaybackNumber(
        raw.audioContextTime || raw.audio_context_time,
      ),
      scheduledEndAudioTime: safeSpeechPlaybackNumber(
        raw.scheduledEndAudioTime || raw.scheduled_end_audio_time,
      ),
      playbackStartAudioTime: safeSpeechPlaybackNumber(
        raw.playbackStartAudioTime || raw.playback_start_audio_time,
      ),
      playbackEndAudioTime: safeSpeechPlaybackNumber(
        raw.playbackEndAudioTime || raw.playback_end_audio_time,
      ),
      updatedAt,
      ageMs,
      reason: boundedSpeechString(raw.reason, 'speech playback reason', 128),
      source: boundedSpeechString(raw.source, 'speech playback source', 128),
      transportSource: boundedSpeechString(source, 'speech transport source', 64),
      priority,
      requestId: boundedSpeechString(metadata?.requestId, 'speech playback requestId', 128),
      eventKey: boundedSpeechString(metadata?.eventKey, 'speech playback eventKey', 128),
    });
  }

  function sanitizeSpeechPlaybackRawState(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const updatedAt = Number(raw.updatedAt || raw.updated_at || 0);
    if (!Number.isFinite(updatedAt) || updatedAt <= 0) return null;
    return Object.freeze({
      active: raw.active === true,
      speechId: boundedSpeechString(raw.speechId || raw.speech_id, 'speech playback id', 128),
      correlationId: boundedSpeechString(
        raw.correlationId || raw.correlation_id || raw.sdk_speech_correlation_id,
        'speech playback correlation id',
        128,
      ),
      turnId: boundedSpeechString(raw.turnId || raw.turn_id, 'speech turn id', 128),
      playbackTurnId: boundedSpeechString(
        raw.playbackTurnId || raw.playback_turn_id,
        'speech playback turn id',
        128,
      ),
      remainingSeconds: Math.max(
        0,
        Math.min(
          MAX_SPEECH_PLAYBACK_SECONDS,
          Number(raw.remainingSeconds || raw.remaining_seconds || 0) || 0,
        ),
      ),
      pendingAudioWork: raw.pendingAudioWork === true || raw.pending_audio_work === true,
      audioContextState: boundedSpeechString(
        raw.audioContextState || raw.audio_context_state,
        'speech audio context state',
        32,
      ),
      audioContextTime: safeSpeechPlaybackNumber(
        raw.audioContextTime || raw.audio_context_time,
      ),
      scheduledEndAudioTime: safeSpeechPlaybackNumber(
        raw.scheduledEndAudioTime || raw.scheduled_end_audio_time || 0,
      ),
      playbackStartAudioTime: safeSpeechPlaybackNumber(
        raw.playbackStartAudioTime || raw.playback_start_audio_time || 0,
      ),
      playbackEndAudioTime: safeSpeechPlaybackNumber(
        raw.playbackEndAudioTime || raw.playback_end_audio_time || 0,
      ),
      updatedAt,
      reason: boundedSpeechString(raw.reason, 'speech playback reason', 128),
      source: boundedSpeechString(raw.source, 'speech playback source', 128),
    });
  }

  function normalizeTranscript(payload) {
    const text = String(payload?.text || payload?.transcript || '').trim();
    if (!text) return null;
    return Object.freeze({
      text,
      requestId: String(payload?.request_id || payload?.requestId || ''),
      source: String(payload?.source || 'voice'),
      timestamp: Number(payload?.timestamp || Date.now()),
    });
  }

  async function normalizeTransportResponse(value) {
    if (value && typeof value.json === 'function') {
      let data = {};
      try { data = await value.json(); } catch (_) { /* invalid/empty response body */ }
      return Object.freeze({
        ok: value.ok === true,
        status: Number(value.status || 0),
        data: data && typeof data === 'object' ? data : {},
      });
    }
    const data = value && typeof value === 'object' ? value : {};
    return Object.freeze({
      ok: data.ok !== false,
      status: Number(data.status || 0),
      data,
    });
  }

  async function connect(manifestInput, options = {}) {
    const manifest = normalizeManifest(manifestInput);
    const transport = options.transport;
    const windowImpl = options.windowImpl || global;
    const documentImpl = options.documentImpl || windowImpl.document;
    const AbortControllerImpl = windowImpl.AbortController || globalThis.AbortController;
    if (!transport || typeof transport !== 'object') {
      fail('transport_unavailable', 'A host-managed transport is required');
    }

    let handshake;
    try {
      handshake = await negotiateConnection(manifest, transport, options, windowImpl);
    } catch (error) {
      try { transport.dispose?.(); } catch (_) { /* rejected handshake cleanup */ }
      throw error;
    }

    const requested = [...manifest.requiredCapabilities, ...manifest.optionalCapabilities];
    const granted = requested.filter((capability) => (
      handshake.grantedCapabilities.includes(capability)
      && SUPPORTED_CAPABILITIES.includes(capability)
      && supportedByTransport(transport, capability)
    ));
    // The manifest rule ("these need runtime") is checked against what the
    // manifest REQUESTS. A host may legitimately withhold an OPTIONAL runtime,
    // and a dependent grant is then permanently unusable: the game would connect
    // reporting `memory` as granted while memory.submit() can never pass its
    // active-route guard. Drop those grants so a required one fails through the
    // existing path below instead of connecting into a dead end.
    const runtimeGranted = granted.includes('runtime');
    const usableGranted = runtimeGranted ? granted : granted.filter(
      (capability) => !RUNTIME_DEPENDENT_CAPABILITIES.includes(capability),
    );
    const missingRequired = manifest.requiredCapabilities.filter(
      (capability) => !usableGranted.includes(capability),
    );
    if (missingRequired.length) {
      try { transport.dispose?.(); } catch (_) { /* rejected capability cleanup */ }
      fail('capability_unavailable', 'One or more required capabilities are unavailable', {
        missing: missingRequired,
      });
    }
    // Contracts are not capabilities, so they cannot be dropped -- but they need
    // runtime for the same reason, and the manifest-time rule already says so.
    if (!runtimeGranted && CONTRACT_KINDS.some(
      (kind) => Object.keys(manifest.contracts[kind]).length > 0,
    )) {
      try { transport.dispose?.(); } catch (_) { /* rejected capability cleanup */ }
      fail('capability_unavailable', 'Game contracts require the runtime capability', {
        missing: ['runtime'],
      });
    }

    const grantedSet = new Set(usableGranted);
    const listeners = new Map();
    const avatarRenderers = new Set();
    let avatarMountsPending = 0;
    const audioControllers = new Set();
    let audioMountsPending = 0;
    let disposed = false;
    let disposing = false;
    let voiceBridgeStarted = false;
    let speechBridgeStarted = false;
    let speechPlaybackRawState = null;
    let speechPlaybackTransportSource = '';
    const speechRequestMetadata = new Map();
    const speechCorrelationMetadata = new Map();
    let speechCorrelationSequence = 0;
    const speechPendingRequests = new Set();
    const speechPreloadPendingRequests = new Set();
    const protocolPendingRequests = new Set();
    const contextPendingRequests = new Set();
    const dialoguePendingRequests = new Set();
    const memoryPendingRequests = new Set();
    const storagePendingRequests = new Set();
    const localLeaderboardPendingRequests = new Set();
    const localLeaderboardMutationPendingRequests = new Set();
    const serverLeaderboardPendingRequests = new Set();
    const localLeaderboardMutations = new Set();
    const loadingPresentations = new Set();
    const bubblePresentations = new Set();
    const consentPresentations = new Set();
    let gameProtocolSequence = 0;
    let controlBridgeStarted = false;
    let lastControlSequence = 0;
    let memoryConsentEnabled = false;
    let memoryConsentLocked = false;
    let memoryConsentConfigured = false;
    let localLeaderboardSequence = 0;
    const localLeaderboardClientId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    let runtimePhase = 'idle';
    let runtimeRouteEstablished = false;
    let runtimeRouteInstanceId = '';
    const runtimeRouteInstanceIds = [];
    let runtimeRouteInstanceSequence = 0;
    let runtimeStartSettlement = null;
    let runtimeEndWaitingForStart = false;
    let runtimeEventSequence = 0;
    let runtimeConfig = null;

    function requireActiveRuntimeRoute(operation) {
      if (!runtimeRouteEstablished || !['running', 'degraded'].includes(runtimePhase)) {
        fail('invalid_state', `${operation} requires an active runtime route`, {
          operation,
          state: runtimePhase,
        });
      }
    }

    function voicePayloadMatchesActiveRoute(payload) {
      if (!runtimeRouteEstablished || !['running', 'degraded'].includes(runtimePhase)) return false;
      const expected = String(runtimeRouteInstanceId || '').trim();
      const actual = String(payload?.sdk_route_instance_id || '').trim();
      return !expected || actual === expected;
    }
    const heartbeatLifecycle = {
      timer: null,
      controller: null,
      inFlight: false,
      startedAt: 0,
      failures: 0,
    };
    const outputLifecycle = {
      timer: null,
      controller: null,
      inFlight: false,
    };
    const runtimeOperation = {
      name: '',
      controller: null,
      externalSignal: null,
      externalAbortHandler: null,
    };
    let visibilityHandler = null;
    let pageExitHandler = null;
    let pageExitDispatched = false;

    function ensureActive(operation) {
      if (disposed || disposing) {
        fail('disposed', 'The mini-game SDK client has been disposed', { operation });
      }
    }

    function requireCapability(capability, operation) {
      ensureActive(operation);
      if (!grantedSet.has(capability)) {
        fail('capability_unavailable', `Capability "${capability}" was not granted`, {
          capability,
          operation,
        });
      }
    }

    function subscribe(eventName, handler) {
      ensureActive(`events.${eventName}`);
      if (typeof handler !== 'function') {
        fail('invalid_request', 'Event handler must be a function', { eventName });
      }
      let bucket = listeners.get(eventName);
      if (!bucket) {
        bucket = new Set();
        listeners.set(eventName, bucket);
      }
      if (bucket.size >= MAX_LISTENERS_PER_EVENT) {
        fail('busy', 'Event listener limit reached', {
          eventName,
          limit: MAX_LISTENERS_PER_EVENT,
        });
      }
      bucket.add(handler);
      let active = true;
      return function unsubscribe() {
        if (!active) return;
        active = false;
        bucket.delete(handler);
        if (!bucket.size) listeners.delete(eventName);
      };
    }

    function emit(eventName, payload) {
      const bucket = listeners.get(eventName);
      if (!bucket || disposed) return;
      for (const handler of Array.from(bucket)) {
        try { handler(payload); }
        catch (error) {
          global.console?.error?.(`[NekoMiniGame] ${eventName} listener failed`, error);
        }
      }
    }

    function runtimeSession() {
      let state = {};
      try { state = transport.getRuntimeState() || {}; }
      catch (_) { state = {}; }
      return Object.freeze({
        id: String(state.sessionId || state.session_id || ''),
        characterName: String(
          state.characterName || state.lanlanName || state.lanlan_name || '',
        ),
      });
    }

    function runtimeEventPayload(payload) {
      if (payload == null) return null;
      const state = { nodes: 0 };
      const clone = (value, fieldName, depth = 0) => {
        state.nodes += 1;
        if (state.nodes > MAX_CONTRACT_PAYLOAD_NODES || depth > 16) {
          fail('invalid_event', 'Runtime event payload exceeds the complexity limit', {
            limit: MAX_CONTRACT_PAYLOAD_NODES,
          });
        }
        if (value === null || typeof value === 'boolean' || typeof value === 'string') return value;
        if (typeof value === 'number') {
          if (!Number.isFinite(value)) {
            fail('invalid_event', `${fieldName} contains a non-finite number`);
          }
          return value;
        }
        if (value instanceof Error) {
          const normalizedError = {
            name: String(value.name || 'Error').slice(0, 128),
            message: String(value.message || '').slice(0, 500),
          };
          if (value.code !== undefined) normalizedError.code = String(value.code).slice(0, 128);
          if (value.details !== undefined) normalizedError.details = value.details;
          return clone(normalizedError, fieldName, depth);
        }
        if (Array.isArray(value)) {
          if (value.length > 256) {
            fail('invalid_event', `${fieldName} contains too many items`);
          }
          return Object.freeze(value.map((item, index) => clone(
            item,
            `${fieldName}[${index}]`,
            depth + 1,
          )));
        }
        if (!plainObject(value)) {
          fail('invalid_event', `${fieldName} contains an unsupported value`);
        }
        const entries = Object.entries(value);
        if (entries.length > 128) {
          fail('invalid_event', `${fieldName} contains too many fields`);
        }
        const result = {};
        for (const [key, item] of entries) {
          if (
            !key || key.length > 128
            || key === '__proto__' || key === 'prototype' || key === 'constructor'
          ) {
            fail('invalid_event', `${fieldName} contains an invalid field`, { key });
          }
          result[key] = clone(item, `${fieldName}.${key}`, depth + 1);
        }
        return Object.freeze(result);
      };
      return clone(payload, 'runtime event payload');
    }

    function runtimeEventSize(payload) {
      try {
        const serialized = JSON.stringify(payload ?? null);
        const TextEncoderImpl = windowImpl.TextEncoder || globalThis.TextEncoder;
        return typeof TextEncoderImpl === 'function'
          ? new TextEncoderImpl().encode(serialized).byteLength
          : unescape(encodeURIComponent(serialized)).length;
      }
      catch (_) { return Number.POSITIVE_INFINITY; }
    }

    async function awaitHandlerWithinBudget(result, normalizedType) {
      // `waitForHandlers` is a promised ordering contract (README: runtime
      // output handlers run sequentially in poll order), so this must not
      // become fire-and-forget. But the await was unbounded, and a handler that
      // never settles pins `outputLifecycle.inFlight` at true forever: every
      // later poll returns null with no error, no event and no log, drain stops
      // (so declared controls stop arriving too), and the backend's 50-entry
      // pending ring silently discards everything past the cap. Only an
      // explicit restart cleared it.
      const settled = Promise.resolve(result);
      const setTimer = windowImpl.setTimeout?.bind(windowImpl) || globalThis.setTimeout;
      const clearTimer = windowImpl.clearTimeout?.bind(windowImpl) || globalThis.clearTimeout;
      if (typeof setTimer !== 'function' || typeof clearTimer !== 'function') {
        await settled;
        return;
      }
      // The handler promise outlives this race when it times out; mark it
      // handled so a late rejection is not an unhandled rejection. This does
      // not stop the `await` below from throwing a prompt rejection.
      settled.catch(() => {});
      let timerId = null;
      let timedOut = false;
      try {
        await Promise.race([
          settled,
          new Promise((resolve) => {
            timerId = setTimer(() => { timedOut = true; resolve(); }, MAX_RUNTIME_HANDLER_MS);
          }),
        ]);
      } finally {
        if (timerId !== null) clearTimer(timerId);
      }
      if (timedOut) {
        // Log only. Publishing a runtime-error from here would recurse into
        // listeners that can hang the same way.
        windowImpl.console?.error?.(
          `[NekoMiniGame] ${normalizedType} listener exceeded the handler budget`,
        );
      }
    }

    async function publishRuntimeEvent(type, payload, { waitForHandlers = false } = {}) {
      if (disposed) return null;
      const normalizedType = String(type || '').trim();
      if (!RUNTIME_EVENT_PATTERN.test(normalizedType)) {
        fail('invalid_event', 'Runtime event type is invalid', { type: normalizedType });
      }
      const immutablePayload = runtimeEventPayload(payload);
      const payloadBytes = runtimeEventSize(immutablePayload);
      if (payloadBytes > MAX_RUNTIME_EVENT_BYTES) {
        fail('invalid_event', 'Runtime event payload exceeds the size limit', {
          type: normalizedType,
          bytes: payloadBytes,
          limit: MAX_RUNTIME_EVENT_BYTES,
        });
      }
      runtimeEventSequence = (runtimeEventSequence % Number.MAX_SAFE_INTEGER) + 1;
      const envelope = Object.freeze({
        protocolVersion: SDK_PROTOCOL_VERSION,
        sequence: runtimeEventSequence,
        type: normalizedType,
        timestamp: Date.now(),
        sessionId: runtimeSession().id,
        payload: immutablePayload,
      });
      const bucket = listeners.get(`runtime-event:${normalizedType}`);
      if (!bucket) return envelope;
      for (const handler of Array.from(bucket)) {
        try {
          const result = handler(envelope);
          if (result && typeof result.then === 'function') {
            if (waitForHandlers) await awaitHandlerWithinBudget(result, normalizedType);
            else {
              void Promise.resolve(result).catch((error) => {
                windowImpl.console?.error?.(`[NekoMiniGame] ${normalizedType} listener failed`, error);
              });
            }
          }
        } catch (error) {
          windowImpl.console?.error?.(`[NekoMiniGame] ${normalizedType} listener failed`, error);
        }
      }
      return envelope;
    }

    function setRuntimePhase(nextPhase, reason = '') {
      const normalized = String(nextPhase || 'idle');
      if (runtimePhase === normalized) return;
      const previous = runtimePhase;
      runtimePhase = normalized;
      void publishRuntimeEvent('runtime-state', Object.freeze({
        previous,
        current: normalized,
        reason: String(reason || ''),
      }));
    }

    function boundedRuntimeNumber(value, fallback, maximum = MAX_RUNTIME_INTERVAL_MS) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric) || numeric <= 0) return fallback;
      return Math.max(MIN_RUNTIME_INTERVAL_MS, Math.min(Math.floor(numeric), maximum));
    }

    function boundedRuntimeOutputLimit(value) {
      // `Number()` on a truthy non-numeric gives NaN, and Math.min/Math.max
      // preserve it. The non-finite limit then rides every poll payload, the
      // trusted host's clone rejects it before /route/drain, and polling emits
      // one error per tick while delivering no output and no control at all.
      // Falls back rather than throwing, matching boundedRuntimeNumber, which
      // the sibling intervalMs/timeoutMs fields already use.
      const numeric = Number(value);
      if (!Number.isFinite(numeric) || numeric <= 0) return MAX_RUNTIME_OUTPUTS_PER_POLL;
      return Math.max(1, Math.min(Math.floor(numeric), MAX_RUNTIME_OUTPUTS_PER_POLL));
    }

    function requireBoundedRuntimeLifecyclePayload(payload, operation) {
      // Every other SDK egress path is bounded; the runtime lifecycle payload
      // was not, in the one dimension that costs anything. Same 256 KiB the
      // trusted host now enforces, so the two cannot disagree about an honest
      // payload.
      const bytes = jsonByteLength(payload ?? {});
      if (bytes > MAX_RUNTIME_EVENT_BYTES) {
        fail('invalid_request', 'The runtime lifecycle payload exceeds its size limit', {
          operation,
          bytes,
          limit: MAX_RUNTIME_EVENT_BYTES,
        });
      }
    }

    function runtimePayload() {
      if (!runtimeConfig || typeof runtimeConfig.payload !== 'function') return {};
      const payload = runtimeConfig.payload();
      return payload == null ? {} : payload;
    }

    function runtimeRouteInstanceEntropy() {
      // Without this the fallback is `${Date.now()}-${sequence}`, and every SDK
      // client starts its sequence at 1: two windows opening in the same
      // millisecond mint the SAME generation, and the server cannot tell them
      // apart -- one route silently supersedes or answers for the other.
      const cryptoImpl = windowImpl.crypto || globalThis.crypto;
      const values = cryptoImpl?.getRandomValues?.(new Uint32Array(2));
      if (values) return `${values[0].toString(36)}${values[1].toString(36)}`;
      return `${Math.floor(Math.random() * 0xffffffff).toString(36)}`
        + `${Math.floor(Math.random() * 0xffffffff).toString(36)}`;
    }

    function nextRuntimeRouteInstanceId() {
      runtimeRouteInstanceSequence = (runtimeRouteInstanceSequence % Number.MAX_SAFE_INTEGER) + 1;
      const randomId = windowImpl.crypto?.randomUUID?.();
      return String(randomId || (
        `${Date.now().toString(36)}-${runtimeRouteInstanceSequence.toString(36)}`
        + `-${runtimeRouteInstanceEntropy()}`
      ));
    }

    function rememberRuntimeRouteInstanceId(routeInstanceId) {
      const normalized = String(routeInstanceId || '').trim();
      if (!normalized) return;
      const existingIndex = runtimeRouteInstanceIds.indexOf(normalized);
      if (existingIndex >= 0) runtimeRouteInstanceIds.splice(existingIndex, 1);
      else if (runtimeRouteInstanceIds.length >= MAX_RUNTIME_ROUTE_INSTANCE_IDS) {
        fail('busy', 'Too many unresolved runtime route generations', {
          operation: 'runtime.start',
          limit: MAX_RUNTIME_ROUTE_INSTANCE_IDS,
        });
      }
      runtimeRouteInstanceIds.push(normalized);
      runtimeRouteInstanceId = normalized;
    }

    function resolveRuntimeRouteInstanceId(routeInstanceId, { active = false } = {}) {
      const normalized = String(routeInstanceId || '').trim();
      if (active) {
        runtimeRouteInstanceIds.splice(0, runtimeRouteInstanceIds.length, normalized);
        runtimeRouteInstanceId = normalized;
        return;
      }
      const existingIndex = runtimeRouteInstanceIds.indexOf(normalized);
      if (existingIndex >= 0) runtimeRouteInstanceIds.splice(existingIndex, 1);
      runtimeRouteInstanceId = runtimeRouteInstanceIds[runtimeRouteInstanceIds.length - 1] || '';
    }

    function clearRuntimeRouteInstanceIds() {
      runtimeRouteInstanceIds.length = 0;
      runtimeRouteInstanceId = '';
    }

    function runtimeRoutePayload(payload, routeInstanceId = runtimeRouteInstanceId) {
      const candidateIds = runtimeRouteInstanceIds.length
        ? Array.from(runtimeRouteInstanceIds)
        : (routeInstanceId ? [routeInstanceId] : []);
      return Object.freeze({
        ...(payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {}),
        ...(routeInstanceId ? { sdk_route_instance_id: routeInstanceId } : {}),
        ...(candidateIds.length ? { sdk_route_instance_ids: Object.freeze(candidateIds) } : {}),
      });
    }

    function runtimeCapabilityPayload(payload) {
      const routeInstanceId = String(runtimeRouteInstanceId || '').trim();
      return Object.freeze({
        ...(payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {}),
        ...(routeInstanceId ? { sdk_route_instance_id: routeInstanceId } : {}),
      });
    }

    function stopHeartbeatLifecycle() {
      if (heartbeatLifecycle.timer != null) {
        windowImpl.clearInterval?.(heartbeatLifecycle.timer);
        heartbeatLifecycle.timer = null;
      }
      if (heartbeatLifecycle.controller) {
        try { heartbeatLifecycle.controller.abort(); } catch (_) { /* already aborted */ }
        heartbeatLifecycle.controller = null;
      }
      heartbeatLifecycle.inFlight = false;
      heartbeatLifecycle.startedAt = 0;
      if (visibilityHandler) {
        documentImpl?.removeEventListener?.('visibilitychange', visibilityHandler);
        visibilityHandler = null;
      }
    }

    function stopOutputLifecycle() {
      if (outputLifecycle.timer != null) {
        windowImpl.clearInterval?.(outputLifecycle.timer);
        outputLifecycle.timer = null;
      }
      if (outputLifecycle.controller) {
        try { outputLifecycle.controller.abort(); } catch (_) { /* already aborted */ }
        outputLifecycle.controller = null;
      }
      outputLifecycle.inFlight = false;
    }

    function stopPageExitLifecycle() {
      if (!pageExitHandler) return;
      windowImpl.removeEventListener?.('pagehide', pageExitHandler);
      windowImpl.removeEventListener?.('beforeunload', pageExitHandler);
      pageExitHandler = null;
    }

    function finishRuntimeOperation(entry) {
      if (!entry || runtimeOperation.controller !== entry.controller) return;
      if (runtimeOperation.externalSignal && runtimeOperation.externalAbortHandler) {
        runtimeOperation.externalSignal.removeEventListener?.(
          'abort',
          runtimeOperation.externalAbortHandler,
        );
      }
      runtimeOperation.name = '';
      runtimeOperation.controller = null;
      runtimeOperation.externalSignal = null;
      runtimeOperation.externalAbortHandler = null;
    }

    async function waitForRuntimeStartSettlement(requestOptions = {}) {
      const settlement = runtimeStartSettlement;
      if (!settlement) return;
      const signal = requestOptions?.signal || null;
      if (signal?.aborted) {
        fail('cancelled', 'The runtime end request was cancelled', { operation: 'runtime.end' });
      }
      // The caller's deadline has to cover this wait too. A built-in start can
      // spend many seconds building pregame context, and observing only the
      // abort signal here meant `timeoutMs` described a request that had not
      // been issued yet -- end() blocked for the whole start and only THEN
      // began counting.
      const budgetMs = requestOptions?.timeoutMs === undefined
        ? null
        : normalizedRequestTimeout(requestOptions.timeoutMs, 'timeoutMs');
      const setTimer = windowImpl.setTimeout?.bind(windowImpl) || globalThis.setTimeout;
      const clearTimer = windowImpl.clearTimeout?.bind(windowImpl) || globalThis.clearTimeout;
      let abortHandler = null;
      let timeoutId = null;
      try {
        const racers = [
          settlement.promise,
          new Promise((_, reject) => {
            if (!signal?.addEventListener) return;
            abortHandler = () => reject(new NekoMiniGameError(
              'cancelled',
              'The runtime end request was cancelled',
              { operation: 'runtime.end' },
            ));
            signal.addEventListener('abort', abortHandler, { once: true });
          }),
        ];
        if (budgetMs !== null) {
          racers.push(new Promise((_, reject) => {
            timeoutId = setTimer(() => reject(new NekoMiniGameError(
              'timeout',
              'The runtime end request timed out waiting for start to settle',
              { operation: 'runtime.end' },
            )), budgetMs);
          }));
        }
        await Promise.race(racers);
      } finally {
        if (timeoutId !== null) {
          try { clearTimer(timeoutId); } catch (_) { /* already cleared */ }
        }
        signal?.removeEventListener?.('abort', abortHandler);
      }
    }

    function isRuntimeOperationCurrent(entry) {
      return !!entry && !disposed && runtimeOperation.controller === entry.controller;
    }

    function stopRuntimeOperation({ preserveEnd = false } = {}) {
      if (!runtimeOperation.controller) return;
      if (preserveEnd && runtimeOperation.name === 'end') return;
      const entry = { controller: runtimeOperation.controller };
      try { runtimeOperation.controller.abort(); } catch (_) { /* already aborted */ }
      finishRuntimeOperation(entry);
    }

    function beginRuntimeOperation(name, requestOptions = {}) {
      if (typeof AbortControllerImpl !== 'function') {
        fail('unsupported', 'AbortController is required for managed runtime lifecycle');
      }
      stopRuntimeOperation();
      const controller = new AbortControllerImpl();
      const externalSignal = requestOptions?.signal || null;
      const externalAbortHandler = externalSignal
        ? () => { try { controller.abort(); } catch (_) { /* already aborted */ } }
        : null;
      if (externalSignal?.aborted) externalAbortHandler();
      else externalSignal?.addEventListener?.('abort', externalAbortHandler, { once: true });
      runtimeOperation.name = String(name || 'runtime');
      runtimeOperation.controller = controller;
      runtimeOperation.externalSignal = externalSignal;
      runtimeOperation.externalAbortHandler = externalAbortHandler;
      return {
        controller,
        requestOptions: { ...(requestOptions || {}), signal: controller.signal },
      };
    }

    function stopRuntimeMonitoring() {
      stopHeartbeatLifecycle();
      stopOutputLifecycle();
      stopPageExitLifecycle();
    }

    async function pulseRuntime(force = false) {
      ensureActive('runtime.pulse');
      if (!runtimeConfig?.heartbeat) return null;
      if (heartbeatLifecycle.inFlight) {
        if (!force) return null;
        try { heartbeatLifecycle.controller?.abort(); } catch (_) { /* already aborted */ }
      }
      if (typeof AbortControllerImpl !== 'function') {
        fail('unsupported', 'AbortController is required for managed runtime lifecycle');
      }
      const controller = new AbortControllerImpl();
      heartbeatLifecycle.controller = controller;
      heartbeatLifecycle.inFlight = true;
      heartbeatLifecycle.startedAt = Date.now();
      try {
        const response = await normalizeTransportResponse(await transport.heartbeat(
          runtimeCapabilityPayload(runtimePayload()),
          {
            signal: controller.signal,
            timeoutMs: runtimeConfig.heartbeat.timeoutMs,
          },
        ));
        if (disposed || heartbeatLifecycle.controller !== controller) return response;
        const data = response.data || {};
        if (response.ok && data.ok !== false && data.active !== false) {
          heartbeatLifecycle.failures = 0;
          return response;
        }
        // A generation-mismatch heartbeat is a REJECTED request that still
        // carries authoritative news: another client superseded this route while
        // keeping the same session_id, so the backend answers
        // {ok:false, active:false, reason:'route_instance_id_mismatch'}. The
        // `data.ok !== false` condition below would skip it, leaving this client
        // locally `running` forever -- still polling, with every route-bound
        // capability failing its generation check and nothing ever telling the
        // game why.
        // Deliberately independent of the HTTP framing: the trusted host returns
        // a Response (so `response.ok` is the 200) while a plain-object transport
        // returns the body itself (so `response.ok` mirrors `data.ok`). The
        // authoritative signal is the same in both -- the backend says this
        // generation is gone.
        const routeGenerationRetired = data.active === false
          && String(data.reason || '') === 'route_instance_id_mismatch';
        if (data.active === false && (routeGenerationRetired || (response.ok && data.ok !== false))) {
          heartbeatLifecycle.failures = 0;
          runtimeRouteEstablished = false;
          // Retire the generation with the route. Capabilities that are allowed
          // before a route exists (speech.speak/mirror/preload, context.read)
          // would otherwise keep asserting a dead sdk_route_instance_id, and the
          // host rejects "no active route + caller asserts a generation" as
          // route_instance_id_mismatch instead of serving the pre-route call.
          clearRuntimeRouteInstanceIds();
          stopRuntimeMonitoring();
          setRuntimePhase('inactive', String(data.reason || 'host-inactive'));
          await publishRuntimeEvent('runtime-inactive', data, { waitForHandlers: true });
          return response;
        }
        heartbeatLifecycle.failures += 1;
        await publishRuntimeEvent('runtime-error', {
          operation: 'heartbeat',
          status: response.status,
          failures: heartbeatLifecycle.failures,
          data,
        }, { waitForHandlers: true });
        return response;
      } catch (error) {
        const normalizedError = normalizeTransportError(error, 'runtime.heartbeat');
        const superseded = heartbeatLifecycle.controller !== controller;
        if (!superseded && !disposed && runtimePhase !== 'ending' && runtimePhase !== 'ended') {
          heartbeatLifecycle.failures += 1;
          await publishRuntimeEvent('runtime-error', {
            operation: 'heartbeat',
            reason: normalizedError.code,
            failures: heartbeatLifecycle.failures,
            error: normalizedError,
          }, { waitForHandlers: true });
        }
        return null;
      } finally {
        if (heartbeatLifecycle.controller === controller) {
          heartbeatLifecycle.controller = null;
          heartbeatLifecycle.inFlight = false;
          heartbeatLifecycle.startedAt = 0;
        }
      }
    }

    async function pollRuntimeOutputs() {
      ensureActive('runtime.pollOutputs');
      if (!runtimeConfig?.outputs || outputLifecycle.inFlight) return null;
      if (typeof AbortControllerImpl !== 'function') {
        fail('unsupported', 'AbortController is required for managed runtime lifecycle');
      }
      const controller = new AbortControllerImpl();
      outputLifecycle.controller = controller;
      outputLifecycle.inFlight = true;
      try {
        const response = await normalizeTransportResponse(await transport.drain(
          runtimeCapabilityPayload({
            ...runtimePayload(),
            limit: runtimeConfig.outputs.limit,
          }),
          {
            signal: controller.signal,
            timeoutMs: runtimeConfig.outputs.timeoutMs,
          },
        ));
        if (disposed || outputLifecycle.controller !== controller) return response;
        const outputs = Array.isArray(response.data?.outputs) ? response.data.outputs : [];
        for (const output of outputs) {
          if (disposed || outputLifecycle.controller !== controller) break;
          // Per output, deliberately. `publishRuntimeEvent` validates the payload
          // (node count, array/field width, depth, serialized bytes) BEFORE it
          // reaches its per-handler try/catch, and a failure there THROWS. The
          // backend deletes a drained batch at response-construction time, so one
          // unrepresentable output used to take every remaining output in that
          // batch with it -- permanently, and with nothing logged. The host feeds
          // this from the game's own state snapshot, which the backend never
          // bounds, so it is a deterministic every-poll failure rather than a
          // rare one.
          try {
            await publishRuntimeEvent('runtime-output', output, { waitForHandlers: true });
          } catch (outputError) {
            windowImpl.console?.error?.(
              '[NekoMiniGame] runtime-output could not be published', outputError,
            );
          }
        }
        if (disposed || outputLifecycle.controller !== controller) return response;
        if (!response.ok || response.data?.ok === false) {
          await publishRuntimeEvent('runtime-error', {
            operation: 'drain',
            status: response.status,
            data: response.data,
          }, { waitForHandlers: true });
        }
        return response;
      } catch (error) {
        const normalizedError = normalizeTransportError(error, 'runtime.drain');
        const superseded = outputLifecycle.controller !== controller;
        if (!superseded && !disposed && runtimePhase !== 'ending' && runtimePhase !== 'ended') {
          await publishRuntimeEvent('runtime-error', {
            operation: 'drain',
            reason: normalizedError.code,
            error: normalizedError,
          }, { waitForHandlers: true });
        }
        return null;
      } finally {
        if (outputLifecycle.controller === controller) {
          outputLifecycle.controller = null;
          outputLifecycle.inFlight = false;
        }
      }
    }

    function startPageExitLifecycle() {
      if (!runtimeConfig?.pageExit || pageExitHandler) return;
      pageExitHandler = (event = {}) => {
        if (disposed || pageExitDispatched) return;
        pageExitDispatched = true;
        const type = String(event.type || 'page-exit');
        const exitContext = Object.freeze({
          type,
          timestamp: Date.now(),
          session: runtimeSession(),
        });
        stopRuntimeMonitoring();
        void publishRuntimeEvent('page-exit', exitContext);

        let payload = {};
        try {
          payload = runtimeConfig.pageExit.payload
            ? runtimeConfig.pageExit.payload(exitContext)
            : runtimePayload();
        } catch (error) {
          void publishRuntimeEvent('runtime-error', {
            operation: 'page-exit',
            reason: 'payload_failed',
            error,
          });
        }
        // Invoke the host transport directly: an unload handler cannot rely on
        // a Promise continuation after an in-flight start settles. The
        // same-origin host calls sendBeacon synchronously before this returns;
        // the backend orders an early end against the matching start.
        try {
          const endRequest = transport.end(
            runtimeRoutePayload(payload),
            { useBeacon: true },
          );
          Promise.resolve(endRequest).catch(() => null);
        } catch (_) { /* page exit remains best effort */ }
        client.dispose({ preserveRuntimeEnd: true });
      };
      windowImpl.addEventListener?.('pagehide', pageExitHandler);
      windowImpl.addEventListener?.('beforeunload', pageExitHandler);
    }

    function startRuntimeMonitoring({ heartbeat = true, outputs = true } = {}) {
      stopRuntimeMonitoring();
      if (disposed || !runtimeConfig) return;
      startPageExitLifecycle();
      if (heartbeat && runtimeConfig.heartbeat) {
        visibilityHandler = () => {
          void publishRuntimeEvent('visibility-change', {
            visibilityState: String(documentImpl?.visibilityState || 'visible'),
            pageVisible: typeof documentImpl?.hidden === 'boolean' ? !documentImpl.hidden : true,
          });
          if (!disposed) void pulseRuntime(true);
        };
        documentImpl?.addEventListener?.('visibilitychange', visibilityHandler);
        void pulseRuntime(false);
        heartbeatLifecycle.timer = windowImpl.setInterval?.(
          () => { if (!disposed) void pulseRuntime(false); },
          runtimeConfig.heartbeat.intervalMs,
        ) ?? null;
      }
      if (outputs && runtimeConfig.outputs) {
        void pollRuntimeOutputs();
        outputLifecycle.timer = windowImpl.setInterval?.(
          () => { if (!disposed) void pollRuntimeOutputs(); },
          runtimeConfig.outputs.intervalMs,
        ) ?? null;
      }
    }

    function rememberSpeechRequest(speechId, metadata) {
      const key = boundedSpeechString(speechId, 'speech response id', 128);
      if (!key) return;
      speechRequestMetadata.delete(key);
      speechRequestMetadata.set(key, Object.freeze({ ...(metadata || {}) }));
      while (speechRequestMetadata.size > MAX_SPEECH_REQUEST_METADATA) {
        const oldestKey = speechRequestMetadata.keys().next().value;
        if (oldestKey === undefined) break;
        speechRequestMetadata.delete(oldestKey);
      }
    }

    function beginSpeechCorrelation(metadata) {
      speechCorrelationSequence = (speechCorrelationSequence % Number.MAX_SAFE_INTEGER) + 1;
      // Every SDK client starts this sequence at 1, so two clients on the same
      // host route speaking in the same millisecond minted the SAME id -- and
      // both resolve playback state from the shared bridge through their own
      // correlation maps, so each would attribute the first utterance's state
      // to its own request. Same entropy as the route generation.
      const correlationId = `sdk-speech-${Date.now().toString(36)}`
        + `-${speechCorrelationSequence.toString(36)}-${runtimeRouteInstanceEntropy()}`;
      speechCorrelationMetadata.set(correlationId, Object.freeze({ ...(metadata || {}) }));
      while (speechCorrelationMetadata.size > MAX_SPEECH_REQUEST_METADATA) {
        const oldestKey = speechCorrelationMetadata.keys().next().value;
        if (oldestKey === undefined) break;
        speechCorrelationMetadata.delete(oldestKey);
      }
      return correlationId;
    }

    function currentSpeechPlaybackState() {
      const speechId = String(
        speechPlaybackRawState?.speechId || speechPlaybackRawState?.speech_id || '',
      );
      return normalizeSpeechPlaybackState(
        speechPlaybackRawState,
        speechPlaybackTransportSource,
        speechRequestMetadata.get(speechId) || null,
      );
    }

    function publishSpeechPlaybackState(rawState, source = '') {
      try {
        const transportSource = safeSpeechTransportSource(source);
        const boundedRawState = sanitizeSpeechPlaybackRawState(rawState);
        const speechId = String(boundedRawState?.speechId || '');
        const correlationId = String(boundedRawState?.correlationId || '');
        const correlatedMetadata = correlationId
          ? speechCorrelationMetadata.get(correlationId) || null
          : null;
        if (correlatedMetadata && speechId) {
          rememberSpeechRequest(speechId, correlatedMetadata);
          speechCorrelationMetadata.delete(correlationId);
        }
        const metadata = speechRequestMetadata.get(speechId) || correlatedMetadata;
        const normalized = normalizeSpeechPlaybackState(boundedRawState, transportSource, metadata);
        speechPlaybackRawState = boundedRawState;
        speechPlaybackTransportSource = transportSource;
        if (!normalized) return;
        emit('speech-state', normalized);
        if (!normalized.active && normalized.speechId) {
          speechRequestMetadata.delete(normalized.speechId);
        }
      } catch (error) {
        const normalized = normalizeTransportError(error, 'speech.bridge');
        emit('speech-error', Object.freeze({
          code: normalized.code,
          message: normalized.message,
          source: safeSpeechTransportSource(source) || 'bridge',
        }));
      }
    }

    function abortManagedRequests(pendingSet, reason = 'cancelled') {
      for (const entry of Array.from(pendingSet)) {
        entry.reason = reason;
        try { entry.controller.abort(); } catch (_) { /* already aborted */ }
        entry.rejectCancellation?.(new NekoMiniGameError(
          reason === 'disposed' ? 'disposed' : 'cancelled',
          reason === 'disposed'
            ? 'The mini-game SDK client has been disposed'
            : 'The host request was cancelled',
          { operation: entry.operation },
        ));
      }
    }

    async function performManagedHostRequest({
      operation,
      pendingSet,
      limit,
      timeoutMs,
      maximumTimeoutMs = MAX_REQUEST_TIMEOUT_MS,
      requestOptions = {},
      invoke,
    }) {
      ensureActive(operation);
      if (pendingSet.size >= limit) fail('busy', `${operation} request limit reached`, { limit });
      const externalSignal = requestOptions.signal || null;
      if (externalSignal?.aborted) fail('cancelled', 'The host request was cancelled', { operation });
      const normalizedTimeoutMs = normalizedRequestTimeout(
        requestOptions.timeoutMs === undefined ? timeoutMs : requestOptions.timeoutMs,
        'timeoutMs',
        maximumTimeoutMs,
      );
      const controller = new AbortControllerImpl();
      const entry = {
        operation,
        controller,
        reason: '',
        externalSignal,
        externalAbortHandler: null,
        timeoutId: null,
        rejectCancellation: null,
      };
      const cancellationPromise = new Promise((_, reject) => {
        entry.rejectCancellation = reject;
      });
      if (externalSignal && typeof externalSignal.addEventListener === 'function') {
        entry.externalAbortHandler = () => {
          entry.reason = 'cancelled';
          try { controller.abort(); } catch (_) { /* already aborted */ }
          entry.rejectCancellation?.(new NekoMiniGameError(
            'cancelled',
            'The host request was cancelled',
            { operation },
          ));
        };
        externalSignal.addEventListener('abort', entry.externalAbortHandler, { once: true });
      }
      pendingSet.add(entry);
      const setTimer = windowImpl.setTimeout?.bind(windowImpl) || globalThis.setTimeout;
      const clearTimer = windowImpl.clearTimeout?.bind(windowImpl) || globalThis.clearTimeout;
      const timeoutPromise = new Promise((_, reject) => {
        entry.timeoutId = setTimer(() => {
          entry.reason = 'timeout';
          try { controller.abort(); } catch (_) { /* already aborted */ }
          reject(new NekoMiniGameError('timeout', 'The host request timed out', { operation }));
        }, normalizedTimeoutMs);
      });
      try {
        const result = await Promise.race([
          Promise.resolve().then(() => {
            if (controller.signal.aborted) {
              fail(entry.reason === 'disposed' ? 'disposed' : (entry.reason || 'cancelled'),
                entry.reason === 'disposed'
                  ? 'The mini-game SDK client has been disposed'
                  : 'The host request was cancelled',
                { operation });
            }
            return invoke({
              signal: controller.signal,
              timeoutMs: normalizedTimeoutMs,
            });
          }),
          timeoutPromise,
          cancellationPromise,
        ]);
        if (controller.signal.aborted) {
          fail(entry.reason === 'disposed' ? 'disposed' : (entry.reason || 'cancelled'),
            entry.reason === 'disposed'
              ? 'The mini-game SDK client has been disposed'
              : 'The host request was cancelled',
            { operation });
        }
        return result;
      } catch (error) {
        if (entry.reason === 'timeout') fail('timeout', 'The host request timed out', { operation });
        if (entry.reason === 'disposed') {
          fail('disposed', 'The mini-game SDK client has been disposed', { operation });
        }
        if (entry.reason === 'cancelled') fail('cancelled', 'The host request was cancelled', { operation });
        throw normalizeTransportError(error, operation);
      } finally {
        pendingSet.delete(entry);
        entry.rejectCancellation = null;
        if (entry.timeoutId != null) clearTimer(entry.timeoutId);
        externalSignal?.removeEventListener?.('abort', entry.externalAbortHandler);
      }
    }

    function declaredContractSchema(kind, type, operation) {
      ensureActive(operation);
      const normalizedType = String(type || '').trim();
      const schema = manifest.contracts[kind]?.[normalizedType];
      if (!schema) {
        fail('invalid_contract', `The ${kind} contract is not declared by this game`, {
          kind,
          type: normalizedType,
          operation,
        });
      }
      return { type: normalizedType, schema };
    }

    function abortPendingProtocolRequests(reason = 'cancelled') {
      for (const entry of Array.from(protocolPendingRequests)) {
        entry.reason = reason;
        try { entry.controller.abort(); } catch (_) { /* already aborted */ }
        entry.rejectCancellation?.(new NekoMiniGameError(
          reason === 'disposed' ? 'disposed' : 'cancelled',
          reason === 'disposed'
            ? 'The mini-game SDK client has been disposed'
            : 'The game protocol request was cancelled',
          { operation: entry.operation || 'game-protocol' },
        ));
      }
    }

    async function sendGameProtocolMessage(kind, typeInput, payloadInput, requestOptions = {}) {
      const operation = kind === 'event'
        ? 'events.emit'
        : kind === 'state'
          ? 'state.update'
          : 'results.submit';
      const contractKind = `${kind}s`;
      const { type, schema } = declaredContractSchema(contractKind, typeInput, operation);
      if (typeof transport.publishGameProtocol !== 'function') {
        fail('transport_unavailable', 'The host game protocol transport is unavailable', { operation });
      }
      if (protocolPendingRequests.size >= MAX_CONTRACT_PENDING_REQUESTS) {
        fail('busy', 'Game protocol request limit reached', {
          operation,
          limit: MAX_CONTRACT_PENDING_REQUESTS,
        });
      }
      const session = runtimeSession();
      if (!session.id) fail('session_invalid', 'The game runtime session is unavailable', { operation });
      const payload = normalizeContractPayload(payloadInput, schema, `${operation} payload`);
      const timeoutMs = normalizedRequestTimeout(
        requestOptions.timeoutMs === undefined ? 8000 : requestOptions.timeoutMs,
        'timeoutMs',
        MAX_CONNECT_TIMEOUT_MS,
      );
      const externalSignal = requestOptions.signal || null;
      if (externalSignal?.aborted) fail('cancelled', 'The game protocol request was cancelled', { operation });
      const controller = new AbortControllerImpl();
      const entry = {
        controller,
        externalSignal,
        externalAbortHandler: null,
        timeoutId: null,
        reason: '',
        operation,
        rejectCancellation: null,
      };
      const cancellationPromise = new Promise((_, reject) => {
        entry.rejectCancellation = reject;
      });
      if (externalSignal && typeof externalSignal.addEventListener === 'function') {
        entry.externalAbortHandler = () => {
          entry.reason = 'cancelled';
          try { controller.abort(); } catch (_) { /* already aborted */ }
          entry.rejectCancellation?.(new NekoMiniGameError(
            'cancelled',
            'The game protocol request was cancelled',
            { operation },
          ));
        };
        externalSignal.addEventListener('abort', entry.externalAbortHandler, { once: true });
      }
      gameProtocolSequence = (gameProtocolSequence % Number.MAX_SAFE_INTEGER) + 1;
      const envelope = runtimeCapabilityPayload({
        protocolVersion: SDK_PROTOCOL_VERSION,
        sequence: gameProtocolSequence,
        kind,
        type,
        timestamp: Date.now(),
        sessionId: session.id,
        payload,
      });
      protocolPendingRequests.add(entry);
      const setTimer = windowImpl.setTimeout?.bind(windowImpl) || globalThis.setTimeout;
      const clearTimer = windowImpl.clearTimeout?.bind(windowImpl) || globalThis.clearTimeout;
      const timeoutPromise = new Promise((_, reject) => {
        entry.timeoutId = setTimer(() => {
          entry.reason = 'timeout';
          try { controller.abort(); } catch (_) { /* already aborted */ }
          reject(new NekoMiniGameError('timeout', 'The game protocol request timed out', { operation }));
        }, timeoutMs);
      });
      try {
        const transportPromise = Promise.resolve().then(() => transport.publishGameProtocol(
          kind,
          envelope,
          { signal: controller.signal, timeoutMs },
        ));
        const response = await Promise.race([transportPromise, timeoutPromise, cancellationPromise]);
        if (controller.signal.aborted) {
          fail(entry.reason === 'disposed' ? 'disposed' : (entry.reason || 'cancelled'),
            entry.reason === 'disposed'
              ? 'The mini-game SDK client has been disposed'
              : 'The game protocol request was cancelled',
            { operation });
        }
        return normalizeTransportResponse(response);
      } catch (error) {
        if (entry.reason === 'timeout') {
          fail('timeout', 'The game protocol request timed out', { operation });
        }
        if (controller.signal.aborted && entry.reason && entry.reason !== 'timeout') {
          fail(entry.reason === 'disposed' ? 'disposed' : 'cancelled',
            entry.reason === 'disposed'
              ? 'The mini-game SDK client has been disposed'
              : 'The game protocol request was cancelled',
            { operation });
        }
        throw normalizeTransportError(error, operation);
      } finally {
        protocolPendingRequests.delete(entry);
        entry.rejectCancellation = null;
        if (entry.timeoutId != null) clearTimer(entry.timeoutId);
        externalSignal?.removeEventListener?.('abort', entry.externalAbortHandler);
      }
    }

    function publishControlEnvelope(rawEnvelope) {
      try {
        if (!rawEnvelope || typeof rawEnvelope !== 'object' || Array.isArray(rawEnvelope)) {
          fail('invalid_contract', 'The host control envelope is invalid');
        }
        const protocolVersion = String(
          rawEnvelope.protocolVersion || rawEnvelope.protocol_version || '',
        );
        if (protocolVersion !== SDK_PROTOCOL_VERSION) {
          fail('incompatible_version', 'The host control uses an incompatible protocol');
        }
        const type = String(rawEnvelope.type || '').trim();
        const { schema } = declaredContractSchema('controls', type, 'controls.dispatch');
        const sequence = Number(rawEnvelope.sequence);
        if (!Number.isSafeInteger(sequence) || sequence <= 0) {
          fail('invalid_contract', 'The host control sequence is invalid');
        }
        if (sequence <= lastControlSequence) return false;
        const sessionId = String(rawEnvelope.sessionId || rawEnvelope.session_id || '');
        const currentSessionId = runtimeSession().id;
        if (!sessionId || (currentSessionId && sessionId !== currentSessionId)) {
          fail('session_invalid', 'The host control belongs to another game session');
        }
        const routeInstanceId = String(
          rawEnvelope.routeInstanceId || rawEnvelope.sdk_route_instance_id || '',
        ).trim();
        // The generation comparison below fails OPEN when we hold no generation,
        // and runtime.end() clears exactly that (clearRuntimeRouteInstanceIds),
        // so after a route ends a straggling control matched everything: the
        // session id still matches too, because ending does not rotate it.
        // Gate on the live route first, like voicePayloadMatchesActiveRoute
        // does. Output polling only runs in these two phases (monitoring is
        // started for 'running' and 'degraded' only), so this cannot drop a
        // control that legitimately belongs to the current route.
        if (!runtimeRouteEstablished || !['running', 'degraded'].includes(runtimePhase)) {
          return false;
        }
        const currentRouteInstanceId = String(runtimeRouteInstanceId || '').trim();
        if (
          currentRouteInstanceId
          && routeInstanceId !== currentRouteInstanceId
        ) {
          fail('session_invalid', 'The host control belongs to another route generation');
        }
        const payload = normalizeContractPayload(
          rawEnvelope.payload,
          schema,
          `controls.${type} payload`,
        );
        lastControlSequence = sequence;
        emit(`control:${type}`, Object.freeze({
          protocolVersion,
          sequence,
          type,
          timestamp: Number(rawEnvelope.timestamp || Date.now()),
          sessionId,
          ...(routeInstanceId ? { routeInstanceId } : {}),
          payload,
        }));
        return true;
      } catch (error) {
        const normalized = error instanceof NekoMiniGameError
          ? error
          : normalizeTransportError(error, 'controls.dispatch');
        emit('control-error', Object.freeze({
          code: normalized.code,
          message: normalized.message,
        }));
        return false;
      }
    }

    const outboundContractsDeclared = ['events', 'states', 'results'].some(
      (kind) => Object.keys(manifest.contracts[kind]).length > 0,
    );
    const controlsDeclared = Object.keys(manifest.contracts.controls).length > 0;
    if (outboundContractsDeclared && typeof transport.publishGameProtocol !== 'function') {
      try { transport.dispose?.(); } catch (_) { /* connection cleanup */ }
      fail('transport_unavailable', 'The host does not support declared game protocol messages');
    }
    if (controlsDeclared) {
      if (
        typeof transport.startGameControlBridge !== 'function'
        || typeof transport.stopGameControlBridge !== 'function'
      ) {
        try { transport.dispose?.(); } catch (_) { /* connection cleanup */ }
        fail('transport_unavailable', 'The host does not support declared game controls');
      }
      try {
        controlBridgeStarted = transport.startGameControlBridge({
          onControl: publishControlEnvelope,
          onError: (error) => {
            const normalized = normalizeTransportError(error, 'controls.bridge');
            emit('control-error', Object.freeze({
              code: normalized.code,
              message: normalized.message,
            }));
          },
        }) !== false;
      } catch (_) {
        controlBridgeStarted = false;
      }
      if (!controlBridgeStarted) {
        try { transport.stopGameControlBridge('unavailable'); } catch (_) { /* partial start cleanup */ }
        try { transport.dispose?.(); } catch (_) { /* connection cleanup */ }
        fail('transport_unavailable', 'The host game control bridge is unavailable');
      }
    }

    if (grantedSet.has('voice-input')) {
      try {
        voiceBridgeStarted = transport.startVoiceControlBridge({
          onState: (state) => {
            if (voicePayloadMatchesActiveRoute(state)) {
              emit('voice-state', Object.freeze({ ...(state || {}) }));
            }
          },
          onTranscript: (payload) => {
            if (!voicePayloadMatchesActiveRoute(payload)) return;
            const transcript = normalizeTranscript(payload);
            if (transcript) emit('voice-transcript', transcript);
          },
          onError: (error, source) => emit('voice-error', Object.freeze({ error, source })),
        }) !== false;
      } catch (_) {
        voiceBridgeStarted = false;
      }
      if (!voiceBridgeStarted) {
        try { transport.stopVoiceControlBridge('unavailable'); } catch (_) { /* partial start cleanup */ }
        grantedSet.delete('voice-input');
        granted.splice(granted.indexOf('voice-input'), 1);
        if (manifest.requiredCapabilities.includes('voice-input')) {
          transport.dispose?.();
          fail('capability_unavailable', 'The required host voice bridge is unavailable', {
            missing: ['voice-input'],
          });
        }
      }
    }

    if (grantedSet.has('speech-output')) {
      try {
        speechBridgeStarted = transport.startSpeechOutputBridge({
          onState: publishSpeechPlaybackState,
          onError: (error, source) => {
            const normalized = normalizeTransportError(error, 'speech.bridge');
            emit('speech-error', Object.freeze({
              code: normalized.code,
              message: normalized.message,
              source: safeSpeechTransportSource(source) || 'bridge',
            }));
          },
        }) !== false;
      } catch (_) {
        speechBridgeStarted = false;
      }
      if (!speechBridgeStarted) {
        try { transport.stopSpeechOutputBridge('unavailable'); } catch (_) { /* partial start cleanup */ }
        grantedSet.delete('speech-output');
        granted.splice(granted.indexOf('speech-output'), 1);
        if (manifest.requiredCapabilities.includes('speech-output')) {
          transport.dispose?.();
          fail('capability_unavailable', 'The required host speech output bridge is unavailable', {
            missing: ['speech-output'],
          });
        }
      }
    }

    const capabilities = Object.freeze({
      granted: Object.freeze([...granted]),
      unavailable: Object.freeze(requested.filter((capability) => !grantedSet.has(capability))),
      has: (capability) => !disposed && !disposing && grantedSet.has(String(capability || '')),
      require: (capability) => {
        requireCapability(String(capability || ''), 'capabilities.require');
        return true;
      },
    });

    const runtime = Object.freeze({
      get state() { return disposed || disposing ? 'disposed' : runtimePhase; },
      get session() { return runtimeSession(); },
      configure(config = {}) {
        requireCapability('runtime', 'runtime.configure');
        if (!config || typeof config !== 'object' || Array.isArray(config)) {
          fail('invalid_request', 'runtime configuration must be an object');
        }
        if (config.payload != null && typeof config.payload !== 'function') {
          fail('invalid_request', 'runtime payload must be a function');
        }
        // ABSENT only, and a real configuration object only. `pageExit: null`
        // used to skip this check and then normalize into "disabled", and
        // `new Date()` passed as an enabled empty config -- both differ from the
        // declared `false | true | { payload? }` API, and both turn a typo meant
        // to ENABLE cleanup into a route left active after navigation.
        if (
          config.pageExit !== undefined
          && config.pageExit !== true
          && config.pageExit !== false
          && !plainObject(config.pageExit)
        ) {
          fail('invalid_request', 'runtime pageExit must be true, false, or an object');
        }
        if (
          config.pageExit
          && config.pageExit !== true
          && config.pageExit.payload != null
          && typeof config.pageExit.payload !== 'function'
        ) {
          fail('invalid_request', 'runtime pageExit.payload must be a function');
        }
        // `false` or an object, exactly as the .d.ts declares. A truthy
        // non-object -- `heartbeat: 'disabled'`, `outputs: 'off'` -- used to fall
        // through as "enabled with defaults", i.e. the author's intent inverted
        // in silence, which is the one outcome a typo must never produce.
        for (const key of ['heartbeat', 'outputs']) {
          const value = config[key];
          // `undefined` alone means absent: `0` and `null` take the `|| {}`
          // fallback too, so they would also come out as enabled-with-defaults.
          if (value === undefined || value === false || plainObject(value)) continue;
          fail('invalid_request', `runtime ${key} must be false or an object`, { [key]: value });
        }
        const heartbeatInput = config.heartbeat === false ? null : (config.heartbeat || {});
        const outputsInput = config.outputs === false ? null : (config.outputs || {});
        const pageExitInput = config.pageExit === true
          ? {}
          : (config.pageExit && config.pageExit !== false ? config.pageExit : null);
        stopRuntimeMonitoring();
        pageExitDispatched = false;
        runtimeConfig = Object.freeze({
          payload: config.payload || (() => ({})),
          heartbeat: heartbeatInput ? Object.freeze({
            intervalMs: boundedRuntimeNumber(
              heartbeatInput.intervalMs,
              DEFAULT_HEARTBEAT_INTERVAL_MS,
            ),
            timeoutMs: boundedRuntimeNumber(
              heartbeatInput.timeoutMs,
              DEFAULT_HEARTBEAT_TIMEOUT_MS,
            ),
          }) : null,
          outputs: outputsInput ? Object.freeze({
            intervalMs: boundedRuntimeNumber(
              outputsInput.intervalMs,
              DEFAULT_OUTPUT_INTERVAL_MS,
            ),
            timeoutMs: boundedRuntimeNumber(
              outputsInput.timeoutMs,
              DEFAULT_OUTPUT_TIMEOUT_MS,
            ),
            limit: boundedRuntimeOutputLimit(outputsInput.limit),
          }) : null,
          pageExit: pageExitInput ? Object.freeze({
            payload: typeof pageExitInput.payload === 'function' ? pageExitInput.payload : null,
          }) : null,
        });
        if (runtimePhase === 'running') startRuntimeMonitoring();
        else if (runtimePhase === 'degraded') startRuntimeMonitoring({ heartbeat: false });
        else startPageExitLifecycle();
        return runtimeConfig;
      },
      reset(resetOptions = {}) {
        requireCapability('runtime', 'runtime.reset');
        if (!['idle', 'ended', 'inactive'].includes(runtimePhase)) {
          fail('invalid_state', 'Active runtimes must be ended before reset', {
            operation: 'runtime.reset',
            state: runtimePhase,
          });
        }
        stopRuntimeMonitoring();
        stopRuntimeOperation();
        abortPendingProtocolRequests('cancelled');
        abortManagedRequests(contextPendingRequests, 'cancelled');
        abortManagedRequests(dialoguePendingRequests, 'cancelled');
        abortManagedRequests(memoryPendingRequests, 'cancelled');
        abortManagedRequests(serverLeaderboardPendingRequests, 'cancelled');
        abortPendingSpeechRequests('cancelled');
        speechRequestMetadata.clear();
        speechCorrelationMetadata.clear();
        speechPlaybackRawState = null;
        speechPlaybackTransportSource = '';
        pageExitDispatched = false;
        runtimeRouteEstablished = false;
        // No clearRuntimeRouteInstanceIds() here: every route-loss outcome now
        // retires the generation at its own boundary (end() clears, an inactive
        // start removes its own id, a heartbeat-detected loss clears), so a
        // fifth clear here is unreachable and would be an untestable guard.
        // If a new route-loss path is ever added, retire the generation THERE.
        const state = transport.resetRuntime({ newSession: resetOptions.newSession === true });
        memoryConsentEnabled = false;
        memoryConsentLocked = false;
        memoryConsentConfigured = false;
        emit('memory-consent-state', Object.freeze({
          enabled: false,
          configured: false,
          locked: false,
        }));
        setRuntimePhase('idle', resetOptions.newSession ? 'new-session' : 'reset');
        startPageExitLifecycle();
        const normalized = state && typeof state === 'object' ? state : transport.getRuntimeState();
        return Object.freeze({
          id: String(normalized?.sessionId || normalized?.session_id || ''),
          characterName: String(
            normalized?.characterName || normalized?.lanlanName || normalized?.lanlan_name || '',
          ),
        });
      },
      async start(payload = {}, requestOptions = {}) {
        requireCapability('runtime', 'runtime.start');
        if (runtimePhase === 'starting' || runtimePhase === 'running' || runtimePhase === 'ending') {
          fail('busy', 'The runtime lifecycle is already active', { state: runtimePhase });
        }
        if (runtimeRouteEstablished) {
          fail('invalid_state', 'The established runtime route must be ended before starting again', {
            operation: 'runtime.start',
            state: runtimePhase,
          });
        }
        if (memoryPendingRequests.size) {
          fail('busy', 'Memory consent configuration is still pending', {
            operation: 'runtime.start',
          });
        }
        // Deliberately BEFORE the generation is minted. The trusted host also
        // bounds this payload, but a transport throw lands in the catch below,
        // which leaves the generation unresolved on purpose (a network throw
        // may well have reached the server). An oversized payload never leaves
        // the browser, so letting it take that path would burn one of the four
        // candidate slots per attempt and wedge start() on `busy` after four
        // tries with the same mistake.
        requireBoundedRuntimeLifecyclePayload(payload, 'runtime.start');
        memoryConsentLocked = true;
        runtimeRouteEstablished = false;
        const routeInstanceId = nextRuntimeRouteInstanceId();
        rememberRuntimeRouteInstanceId(routeInstanceId);
        stopRuntimeMonitoring();
        startPageExitLifecycle();
        const operation = beginRuntimeOperation('start', requestOptions);
        let resolveStartSettlement;
        const startSettlement = {
          promise: new Promise((resolve) => { resolveStartSettlement = resolve; }),
          resolve: () => resolveStartSettlement?.(),
        };
        runtimeStartSettlement = startSettlement;
        setRuntimePhase('starting', 'start-request');
        try {
          const response = await normalizeTransportResponse(await transport.start(
            runtimeRoutePayload(payload, routeInstanceId),
            operation.requestOptions,
          ));
          if (!isRuntimeOperationCurrent(operation)) return response;
          const data = response.data || {};
          const routeState = data.state && typeof data.state === 'object' ? data.state : null;
          const routeActive = routeState?.game_route_active === true || data.active === true;
          if (response.ok && data.ok !== false && routeActive) {
            if (routeState) transport.applyRuntimeState(routeState);
            resolveRuntimeRouteInstanceId(routeInstanceId, { active: true });
            runtimeRouteEstablished = true;
            setRuntimePhase('running', 'start-accepted');
            if (isRuntimeOperationCurrent(operation) && runtimePhase === 'running') {
              startRuntimeMonitoring();
            }
          } else if (response.ok && data.ok !== false) {
            if (routeState) transport.applyRuntimeState(routeState);
            resolveRuntimeRouteInstanceId(routeInstanceId);
            runtimeRouteEstablished = false;
            stopRuntimeMonitoring();
            setRuntimePhase('inactive', 'start-inactive');
            await publishRuntimeEvent('runtime-inactive', data, { waitForHandlers: true });
          } else {
            resolveRuntimeRouteInstanceId(routeInstanceId);
            setRuntimePhase('degraded', 'start-rejected');
            if (isRuntimeOperationCurrent(operation) && runtimePhase === 'degraded') {
              startRuntimeMonitoring({ heartbeat: false });
            }
          }
          return response;
        } catch (error) {
          const normalizedError = normalizeTransportError(error, 'runtime.start');
          if (isRuntimeOperationCurrent(operation)) {
            setRuntimePhase('degraded', 'start-failed');
            if (isRuntimeOperationCurrent(operation) && runtimePhase === 'degraded') {
              startRuntimeMonitoring({ heartbeat: false });
              await publishRuntimeEvent('runtime-error', {
                operation: 'start',
                reason: normalizedError.code,
                error: normalizedError,
              }, { waitForHandlers: true });
            }
          }
          throw normalizedError;
        } finally {
          finishRuntimeOperation(operation);
          startSettlement.resolve();
          if (runtimeStartSettlement === startSettlement) runtimeStartSettlement = null;
        }
      },
      async end(payload = {}, requestOptions = {}) {
        requireCapability('runtime', 'runtime.end');
        let endRequestOptions = requestOptions;
        if (runtimePhase === 'starting' && runtimeStartSettlement) {
          if (runtimeEndWaitingForStart) {
            fail('busy', 'The runtime lifecycle is already waiting to end');
          }
          runtimeEndWaitingForStart = true;
          const waitStartedAt = Date.now();
          try {
            await waitForRuntimeStartSettlement(requestOptions);
          } finally {
            runtimeEndWaitingForStart = false;
          }
          ensureActive('runtime.end');
          // Charge the wait to the same budget, so the whole end() honours the
          // advertised deadline instead of spending it twice.
          if (requestOptions?.timeoutMs !== undefined) {
            const budgetMs = normalizedRequestTimeout(requestOptions.timeoutMs, 'timeoutMs');
            const remainingMs = budgetMs - (Date.now() - waitStartedAt);
            if (remainingMs < MIN_CONNECT_TIMEOUT_MS) {
              fail('timeout', 'The runtime end request timed out waiting for start to settle', {
                operation: 'runtime.end',
              });
            }
            endRequestOptions = { ...requestOptions, timeoutMs: remainingMs };
          }
        }
        if (runtimePhase === 'ending') {
          fail('busy', 'The runtime lifecycle is already ending');
        }
        stopRuntimeMonitoring();
        stopRuntimeOperation();
        const operation = beginRuntimeOperation('end', endRequestOptions);
        setRuntimePhase('ending', 'end-request');
        const recoverEndFailure = (reason) => {
          if (!isRuntimeOperationCurrent(operation)) return;
          setRuntimePhase('degraded', reason);
          if (runtimePhase === 'degraded') startRuntimeMonitoring();
        };
        try {
          const response = await normalizeTransportResponse(await transport.end(
            runtimeRoutePayload(payload),
            operation.requestOptions,
          ));
          if (isRuntimeOperationCurrent(operation)) {
            if (response.ok && response.data?.ok !== false) {
              runtimeRouteEstablished = false;
              clearRuntimeRouteInstanceIds();
              setRuntimePhase('ended', 'end-accepted');
            } else recoverEndFailure('end-rejected');
          }
          return response;
        } catch (error) {
          const normalizedError = normalizeTransportError(error, 'runtime.end');
          if (isRuntimeOperationCurrent(operation)) {
            recoverEndFailure('end-failed');
            await publishRuntimeEvent('runtime-error', {
              operation: 'end',
              reason: normalizedError.code,
              error: normalizedError,
            }, { waitForHandlers: true });
          }
          throw normalizedError;
        } finally {
          finishRuntimeOperation(operation);
        }
      },
      pulse(force = false) {
        requireCapability('runtime', 'runtime.pulse');
        return pulseRuntime(force === true);
      },
      pollOutputs() {
        requireCapability('runtime', 'runtime.pollOutputs');
        return pollRuntimeOutputs();
      },
      startMonitoring(options = {}) {
        requireCapability('runtime', 'runtime.startMonitoring');
        startRuntimeMonitoring(options);
      },
      stopMonitoring() {
        requireCapability('runtime', 'runtime.stopMonitoring');
        stopRuntimeMonitoring();
      },
    });

    const events = Object.freeze({
      declared: Object.freeze(Object.keys(manifest.contracts.events)),
      on(type, handler) {
        requireCapability('runtime', 'events.on');
        const normalizedType = String(type || '').trim();
        if (!RUNTIME_EVENT_PATTERN.test(normalizedType) || !RUNTIME_EVENT_TYPES.includes(normalizedType)) {
          fail('invalid_event', 'Runtime event type is not supported', {
            type: normalizedType,
            supported: RUNTIME_EVENT_TYPES,
          });
        }
        return subscribe(`runtime-event:${normalizedType}`, handler);
      },
      emit(type, payload, requestOptions = {}) {
        return sendGameProtocolMessage('event', type, payload, requestOptions);
      },
    });

    const state = Object.freeze({
      declared: Object.freeze(Object.keys(manifest.contracts.states)),
      update(type, payload, requestOptions = {}) {
        return sendGameProtocolMessage('state', type, payload, requestOptions);
      },
    });

    const results = Object.freeze({
      declared: Object.freeze(Object.keys(manifest.contracts.results)),
      submit(type, payload, requestOptions = {}) {
        return sendGameProtocolMessage('result', type, payload, requestOptions);
      },
    });

    const controls = Object.freeze({
      declared: Object.freeze(Object.keys(manifest.contracts.controls)),
      get connected() { return !disposed && !disposing && (!controlsDeclared || controlBridgeStarted); },
      on(type, handler) {
        const { type: normalizedType } = declaredContractSchema(
          'controls',
          type,
          'controls.on',
        );
        return subscribe(`control:${normalizedType}`, handler);
      },
      onError(handler) {
        ensureActive('controls.onError');
        return subscribe('control-error', handler);
      },
    });

    const context = Object.freeze({
      get pendingCount() { return contextPendingRequests.size; },
      async read(scopesInput, requestOptions = {}) {
        requireCapability('context-read', 'context.read');
        const scopes = normalizeContextScopes(scopesInput);
        const session = runtimeSession();
        const rawResponse = await performManagedHostRequest({
          operation: 'context.read',
          pendingSet: contextPendingRequests,
          limit: MAX_CONTEXT_PENDING_REQUESTS,
          timeoutMs: 15000,
          requestOptions,
          invoke: (options) => transport.readGameContext(runtimeCapabilityPayload({
            scopes,
            session_id: session.id,
          }), options),
        });
        const response = await normalizeTransportResponse(rawResponse);
        return Object.freeze({
          ...response,
          data: normalizeBoundedJson(response.data, 'context response'),
        });
      },
    });

    const memory = Object.freeze({
      get consent() {
        return Object.freeze({
          enabled: memoryConsentEnabled,
          configured: memoryConsentConfigured,
          locked: memoryConsentLocked,
        });
      },
      get pendingCount() { return memoryPendingRequests.size; },
      async configureConsent(enabledInput, requestOptions = {}) {
        requireCapability('memory', 'memory.configureConsent');
        if (memoryConsentLocked || runtimePhase !== 'idle') {
          fail('consent_locked', 'Memory consent cannot change after runtime start', {
            operation: 'memory.configureConsent',
          });
        }
        if (typeof enabledInput !== 'boolean') {
          fail('invalid_request', 'Memory consent must be a boolean');
        }
        const rawResponse = await performManagedHostRequest({
          operation: 'memory.configureConsent',
          pendingSet: memoryPendingRequests,
          limit: 1,
          timeoutMs: 8000,
          requestOptions,
          invoke: (options) => transport.configureGameMemoryConsent(Object.freeze({
            enabled: enabledInput,
            session_id: runtimeSession().id,
          }), options),
        });
        const response = await normalizeTransportResponse(rawResponse);
        if (response.ok && response.data?.ok !== false) {
          memoryConsentEnabled = enabledInput;
          memoryConsentConfigured = true;
          emit('memory-consent-state', Object.freeze({
            enabled: memoryConsentEnabled,
            configured: memoryConsentConfigured,
            locked: memoryConsentLocked,
          }));
        }
        return response;
      },
      async submit(value, requestOptions = {}) {
        requireCapability('memory', 'memory.submit');
        if (!memoryConsentLocked) {
          fail('session_invalid', 'Memory can only be submitted during an active game runtime', {
            operation: 'memory.submit',
          });
        }
        requireActiveRuntimeRoute('memory.submit');
        if (!memoryConsentEnabled) {
          fail('consent_required', 'This game session did not receive memory consent', {
            operation: 'memory.submit',
          });
        }
        const submission = normalizeMemorySubmission(value);
        const rawResponse = await performManagedHostRequest({
          operation: 'memory.submit',
          pendingSet: memoryPendingRequests,
          limit: MAX_MEMORY_PENDING_REQUESTS,
          timeoutMs: 15000,
          requestOptions,
          invoke: (options) => transport.submitGameMemory(runtimeCapabilityPayload({
            session_id: runtimeSession().id,
            submission,
          }), options),
        });
        return normalizeTransportResponse(rawResponse);
      },
    });

    async function requestStorage(operation, payload, requestOptions = {}) {
      requireCapability('storage', `storage.${operation}`);
      const rawResponse = await performManagedHostRequest({
        operation: `storage.${operation}`,
        pendingSet: storagePendingRequests,
        limit: MAX_STORAGE_PENDING_REQUESTS,
        timeoutMs: 8000,
        requestOptions,
        invoke: (options) => transport.requestGameStorage(operation, Object.freeze({
          ...payload,
          session_id: runtimeSession().id,
        }), options),
      });
      const response = await normalizeTransportResponse(rawResponse);
      return Object.freeze({
        ...response,
        data: normalizeBoundedEnvelope(response.data, 'storage response', MAX_STORAGE_VALUE_BYTES),
      });
    }

    const storage = Object.freeze({
      get pendingCount() { return storagePendingRequests.size; },
      get(key, requestOptions = {}) {
        return requestStorage('get', { key: normalizeStorageKey(key) }, requestOptions);
      },
      set(key, value, requestOptions = {}) {
        return requestStorage('set', {
          key: normalizeStorageKey(key),
          value: normalizeBoundedJson(value, 'storage value', MAX_STORAGE_VALUE_BYTES),
        }, requestOptions);
      },
      delete(key, requestOptions = {}) {
        return requestStorage('delete', { key: normalizeStorageKey(key) }, requestOptions);
      },
      list(options = {}, requestOptions = {}) {
        if (!plainObject(options)) fail('invalid_request', 'storage list options must be an object');
        const limit = options.limit === undefined ? 100 : Number(options.limit);
        if (!Number.isInteger(limit) || limit < 1 || limit > 256) {
          fail('invalid_request', 'storage list limit must be an integer between 1 and 256');
        }
        return requestStorage('list', {
          prefix: normalizeStorageKey(options.prefix || '', 'storage prefix', { allowEmpty: true, allowReserved: true }),
          limit,
        }, requestOptions);
      },
      clear(options = {}, requestOptions = {}) {
        if (!plainObject(options) || options.confirm !== true) {
          fail('invalid_request', 'storage.clear requires { confirm: true }');
        }
        return requestStorage('clear', { confirm: true }, requestOptions);
      },
    });

    async function requestLocalLeaderboardStorage(operation, boardId, payload, requestOptions = {}) {
      requireCapability('leaderboard-local', `leaderboard.local.${operation}`);
      const rawResponse = await performManagedHostRequest({
        operation: `leaderboard.local.${operation}`,
        pendingSet: localLeaderboardPendingRequests,
        limit: MAX_LEADERBOARD_PENDING_REQUESTS,
        timeoutMs: 8000,
        requestOptions,
        invoke: (options) => transport.requestGameStorage(operation, Object.freeze({
          ...payload,
          key: `leaderboards/${boardId}`,
          session_id: runtimeSession().id,
        }), options),
      });
      const response = await normalizeTransportResponse(rawResponse);
      return Object.freeze({
        ...response,
        data: normalizeBoundedEnvelope(response.data, 'local leaderboard response', MAX_LEADERBOARD_STATE_BYTES),
      });
    }

    async function readLocalLeaderboard(boardId, definition, requestOptions = {}) {
      const response = await requestLocalLeaderboardStorage('get', boardId, {}, requestOptions);
      // A transport that reports failure by RETURNING a non-OK response instead
      // of throwing used to look identical to "no board yet": `found` is simply
      // absent either way. The caller then wrote a replacement holding only the
      // new entry, so one transient read failure erased the whole board. A read
      // that did not succeed is not an empty board.
      if (response.ok === false || response.data?.ok === false) {
        fail('request_failed', 'The local leaderboard could not be read', {
          operation: 'leaderboard.local.read',
          boardId,
          status: response.status,
        });
      }
      const value = response.data?.found === true ? response.data.value : null;
      return normalizeStoredLeaderboardState(value, definition);
    }

    async function writeLocalLeaderboard(boardId, state, requestOptions = {}) {
      let normalizedState = null;
      const entries = [...state.entries];
      while (!normalizedState) {
        try {
          normalizedState = normalizeBoundedJson(
            { version: 1, entries },
            'local leaderboard state',
            MAX_LEADERBOARD_STATE_BYTES,
          );
        } catch (error) {
          if (entries.length <= 1 || error?.code !== 'invalid_request') throw error;
          entries.pop();
        }
      }
      const writeResponse = await requestLocalLeaderboardStorage(
        'set', boardId, { value: normalizedState }, requestOptions,
      );
      // The dual of the read guard: a transport that reports a failed `set` by
      // RETURNING a non-OK response instead of throwing used to resolve here,
      // and submit() then built its success result from the in-memory state --
      // telling the game its entry was retained and ranked while nothing had
      // been persisted.
      if (writeResponse.ok === false || writeResponse.data?.ok === false) {
        fail('request_failed', 'The local leaderboard could not be written', {
          operation: 'leaderboard.local.write',
          boardId,
          status: writeResponse.status,
        });
      }
      return normalizeStoredLeaderboardState(normalizedState, state.definition);
    }

    function localLeaderboardResult(data) {
      return Object.freeze({
        ok: true,
        status: 200,
        // `entries` restates a board state that was written under exactly
        // this budget, under a different wrapper; measuring the wrapper with
        // it breaks `list` on a full board.
        data: normalizeBoundedEnvelope(data, 'local leaderboard result', MAX_LEADERBOARD_STATE_BYTES, 'entries'),
      });
    }

    async function mutateLocalLeaderboard(boardId, operation, callback, requestOptions = {}) {
      if (localLeaderboardMutations.has(boardId)) {
        fail('busy', 'A local leaderboard mutation is already in progress', {
          operation,
          boardId,
        });
      }
      localLeaderboardMutations.add(boardId);
      try {
        return await performManagedHostRequest({
          operation,
          pendingSet: localLeaderboardMutationPendingRequests,
          limit: MAX_LEADERBOARD_PENDING_REQUESTS,
          timeoutMs: 8500,
          maximumTimeoutMs: 60000,
          requestOptions,
          invoke: ({ signal, timeoutMs }) => {
            const managedRequestOptions = Object.freeze({
              ...requestOptions,
              timeoutMs: Math.min(8000, timeoutMs),
              signal,
            });
            return transport.runGameStorageExclusive(
              `leaderboards/${boardId}`,
              () => callback(managedRequestOptions),
              managedRequestOptions,
            );
          },
        });
      }
      finally { localLeaderboardMutations.delete(boardId); }
    }

    const localLeaderboard = Object.freeze({
      get pendingCount() {
        return localLeaderboardPendingRequests.size + localLeaderboardMutationPendingRequests.size;
      },
      async submit(boardIdInput, value, requestOptions = {}) {
        requireCapability('leaderboard-local', 'leaderboard.local.submit');
        const { boardId, definition } = leaderboardDefinition(
          manifest,
          boardIdInput,
          'leaderboard.local.submit',
        );
        const normalized = normalizeLeaderboardEntryData(value, definition);
        return mutateLocalLeaderboard(boardId, 'leaderboard.local.submit', async (managedRequestOptions) => {
          const current = await readLocalLeaderboard(boardId, definition, managedRequestOptions);
          localLeaderboardSequence = (localLeaderboardSequence + 1) % Number.MAX_SAFE_INTEGER;
          const entry = Object.freeze({
            id: `local-${localLeaderboardClientId}-${localLeaderboardSequence.toString(36)}`,
            submittedAt: Date.now(),
            score: normalized.score,
            data: normalized.data,
          });
          const entries = [...current.entries, entry];
          if (definition.retention === 'best') {
            entries.sort((left, right) => leaderboardEntryCompare(left, right, definition, 'rank'));
          } else {
            entries.sort((left, right) => leaderboardEntryCompare(left, right, definition, 'recent'));
          }
          entries.splice(definition.maxEntries);
          const stored = await writeLocalLeaderboard(
            boardId,
            { definition, entries },
            managedRequestOptions,
          );
          const ranked = [...stored.entries]
            .sort((left, right) => leaderboardEntryCompare(left, right, definition, 'rank'));
          const rankIndex = ranked.findIndex((item) => item.id === entry.id);
          return localLeaderboardResult({
            boardId,
            entry,
            retained: rankIndex >= 0,
            rank: rankIndex >= 0 ? rankIndex + 1 : null,
            totalEntries: stored.entries.length,
            isPersonalBest: rankIndex === 0,
          });
        }, requestOptions);
      },
      async list(boardIdInput, options = {}, requestOptions = {}) {
        requireCapability('leaderboard-local', 'leaderboard.local.list');
        const { boardId, definition } = leaderboardDefinition(
          manifest,
          boardIdInput,
          'leaderboard.local.list',
        );
        const normalizedOptions = normalizeLeaderboardListOptions(options, { allowQuery: false });
        const current = await readLocalLeaderboard(boardId, definition, requestOptions);
        const sorted = [...current.entries]
          .sort((left, right) => leaderboardEntryCompare(left, right, definition, normalizedOptions.sort));
        const entries = sorted.slice(
          normalizedOptions.offset,
          normalizedOptions.offset + normalizedOptions.limit,
        );
        return localLeaderboardResult({
          boardId,
          entries,
          totalEntries: sorted.length,
          limit: normalizedOptions.limit,
          offset: normalizedOptions.offset,
          hasMore: normalizedOptions.offset + entries.length < sorted.length,
        });
      },
      async getBest(boardIdInput, requestOptions = {}) {
        requireCapability('leaderboard-local', 'leaderboard.local.getBest');
        const { boardId, definition } = leaderboardDefinition(
          manifest,
          boardIdInput,
          'leaderboard.local.getBest',
        );
        const current = await readLocalLeaderboard(boardId, definition, requestOptions);
        const best = [...current.entries]
          .sort((left, right) => leaderboardEntryCompare(left, right, definition, 'rank'))[0] || null;
        return localLeaderboardResult({ boardId, entry: best });
      },
      async clear(boardIdInput, options = {}, requestOptions = {}) {
        requireCapability('leaderboard-local', 'leaderboard.local.clear');
        const { boardId } = leaderboardDefinition(manifest, boardIdInput, 'leaderboard.local.clear');
        if (!plainObject(options) || options.confirm !== true) {
          fail('invalid_request', 'leaderboard.local.clear requires { confirm: true }');
        }
        return mutateLocalLeaderboard(boardId, 'leaderboard.local.clear', async (managedRequestOptions) => {
          const deleteResponse = await requestLocalLeaderboardStorage(
            'delete', boardId, {}, managedRequestOptions,
          );
          // Third of the same family as the read and write guards: a transport
          // that reports a failed delete by RETURNING a non-OK response used to
          // resolve here, and the game was told the board was cleared while it
          // was still there.
          if (deleteResponse.ok === false || deleteResponse.data?.ok === false) {
            fail('request_failed', 'The local leaderboard could not be cleared', {
              operation: 'leaderboard.local.clear',
              boardId,
              status: deleteResponse.status,
            });
          }
          return localLeaderboardResult({ boardId, cleared: true });
        }, requestOptions);
      },
    });

    async function requestServerLeaderboard(operation, boardIdInput, payload, requestOptions = {}) {
      requireCapability('leaderboard-server', `leaderboard.server.${operation}`);
      const { boardId } = leaderboardDefinition(
        manifest,
        boardIdInput,
        `leaderboard.server.${operation}`,
      );
      const method = operation === 'submit'
        ? 'submitServerLeaderboard'
        : (operation === 'list' ? 'listServerLeaderboard' : 'getServerLeaderboardBest');
      const rawResponse = await performManagedHostRequest({
        operation: `leaderboard.server.${operation}`,
        pendingSet: serverLeaderboardPendingRequests,
        limit: MAX_LEADERBOARD_PENDING_REQUESTS,
        timeoutMs: 15000,
        requestOptions,
        invoke: (options) => transport[method](Object.freeze({
          board_id: boardId,
          session_id: runtimeSession().id,
          character_name: runtimeSession().characterName,
          ...payload,
        }), options),
      });
      const response = await normalizeTransportResponse(rawResponse);
      return Object.freeze({
        ...response,
        data: normalizeBoundedJson(response.data, 'server leaderboard response'),
      });
    }

    const serverLeaderboard = Object.freeze({
      get pendingCount() { return serverLeaderboardPendingRequests.size; },
      submit(boardIdInput, value, requestOptions = {}) {
        requireCapability('leaderboard-server', 'leaderboard.server.submit');
        if (runtimePhase !== 'ended') {
          fail('session_invalid', 'Server leaderboard scores can only be submitted after runtime.end()', {
            operation: 'leaderboard.server.submit',
          });
        }
        const { definition } = leaderboardDefinition(
          manifest,
          boardIdInput,
          'leaderboard.server.submit',
        );
        const entry = normalizeLeaderboardEntryData(value, definition).data;
        return requestServerLeaderboard('submit', boardIdInput, { entry }, requestOptions);
      },
      list(boardIdInput, options = {}, requestOptions = {}) {
        const normalized = normalizeLeaderboardListOptions(options);
        return requestServerLeaderboard('list', boardIdInput, { query: normalized }, requestOptions);
      },
      getMyBest(boardIdInput, query = {}, requestOptions = {}) {
        const normalizedQuery = normalizeBoundedJson(
          query || {},
          'server leaderboard best query',
          MAX_LEADERBOARD_ENTRY_BYTES,
        );
        return requestServerLeaderboard('best', boardIdInput, { query: normalizedQuery }, requestOptions);
      },
    });

    const leaderboard = Object.freeze({
      local: localLeaderboard,
      server: serverLeaderboard,
    });

    function presentationContainer(config, operation) {
      const container = config?.container;
      if (!container || typeof container.appendChild !== 'function') {
        fail('invalid_request', `${operation} requires a DOM container`);
      }
      const doc = container.ownerDocument || documentImpl;
      if (!doc || typeof doc.createElement !== 'function') {
        fail('unsupported', `${operation} requires a DOM document`);
      }
      ensurePresentationStyles(doc);
      return container;
    }

    function presentationText(value, fieldName, maximum, { required = false } = {}) {
      const text = String(value || '').trim();
      if ((required && !text) || text.length > maximum) {
        fail('invalid_request', `${fieldName} ${required ? 'is required and ' : ''}must not exceed ${maximum} characters`);
      }
      return text;
    }

    function mountLoadingPresentation(config = {}) {
      ensureActive('presentation.loading.mount');
      if (loadingPresentations.size >= MAX_LOADING_PRESENTATIONS) {
        fail('busy', 'Loading presentation limit reached', { limit: MAX_LOADING_PRESENTATIONS });
      }
      const container = presentationContainer(config, 'presentation.loading.mount');
      const doc = container.ownerDocument || documentImpl;
      const root = doc.createElement('section');
      root.className = 'neko-minigame-loading';
      root.setAttribute('role', 'status');
      root.setAttribute('aria-live', 'polite');
      root.setAttribute('aria-atomic', 'true');
      root.hidden = config.visible === false;
      root.dataset.state = 'loading';
      const panel = doc.createElement('div');
      panel.className = 'neko-minigame-loading__panel';
      const title = doc.createElement('h2');
      title.className = 'neko-minigame-loading__title';
      title.textContent = presentationText(config.title, 'loading title', 200, { required: true });
      const message = doc.createElement('p');
      message.className = 'neko-minigame-loading__message';
      message.textContent = presentationText(config.message, 'loading message', 1000);
      const progress = doc.createElement('progress');
      progress.className = 'neko-minigame-loading__progress';
      progress.max = 1;
      progress.value = 0;
      const progressText = doc.createElement('p');
      progressText.className = 'neko-minigame-loading__message';
      progressText.textContent = '0%';
      const errorText = doc.createElement('p');
      errorText.className = 'neko-minigame-loading__error';
      errorText.hidden = true;
      panel.append(title, message, progress, progressText, errorText);
      root.appendChild(panel);
      container.appendChild(root);
      const state = { root, disposed: false, stage: '', progress: 0 };
      loadingPresentations.add(state);
      const requireMounted = () => {
        ensureActive('presentation.loading');
        if (state.disposed) fail('disposed', 'The loading presentation has been disposed');
      };
      const disposeController = () => {
        if (state.disposed) return;
        state.disposed = true;
        loadingPresentations.delete(state);
        root.remove?.();
      };
      return Object.freeze({
        element: root,
        get disposed() { return state.disposed; },
        get state() {
          return Object.freeze({
            stage: state.stage,
            progress: state.progress,
            visible: !root.hidden,
            error: errorText.hidden ? '' : errorText.textContent,
          });
        },
        setStage(value) {
          requireMounted();
          state.stage = presentationText(value, 'loading stage', 64);
          root.dataset.stage = state.stage;
        },
        setProgress(value) {
          requireMounted();
          state.progress = finiteNumber(value, 'loading progress', { minimum: 0, maximum: 1 });
          progress.value = state.progress;
          progressText.textContent = `${Math.round(state.progress * 100)}%`;
        },
        setMessage(value) {
          requireMounted();
          message.textContent = presentationText(value, 'loading message', 1000);
        },
        setError(value) {
          requireMounted();
          const text = presentationText(value, 'loading error', 1000);
          errorText.textContent = text;
          errorText.hidden = !text;
          root.dataset.state = text ? 'error' : 'loading';
          root.setAttribute('role', text ? 'alert' : 'status');
        },
        show() { requireMounted(); root.hidden = false; },
        hide() { requireMounted(); root.hidden = true; },
        dispose: disposeController,
      });
    }

    function mountBubblePresentation(config = {}) {
      ensureActive('presentation.bubble.mount');
      if (bubblePresentations.size >= MAX_BUBBLE_PRESENTATIONS) {
        fail('busy', 'Bubble presentation limit reached', { limit: MAX_BUBBLE_PRESENTATIONS });
      }
      const container = presentationContainer(config, 'presentation.bubble.mount');
      const doc = container.ownerDocument || documentImpl;
      const root = doc.createElement('div');
      root.className = 'neko-minigame-bubble';
      root.setAttribute('role', 'status');
      const live = String(config.live || 'polite');
      if (!['polite', 'assertive', 'off'].includes(live)) {
        fail('invalid_request', 'bubble live mode must be polite, assertive, or off');
      }
      root.setAttribute('aria-live', live);
      root.setAttribute('aria-atomic', 'true');
      root.hidden = true;
      // Attach it. Every mount built a root, registered it, handed it back and
      // never put it in the document, so `presentation.bubble` could never
      // display anything -- the sibling loading presentation does this at its
      // own mount.
      container.appendChild(root);
      const state = { root, disposed: false, timer: null };
      bubblePresentations.add(state);
      const clearTimer = () => {
        if (state.timer != null) {
          const clear = windowImpl.clearTimeout?.bind(windowImpl) || globalThis.clearTimeout;
          clear(state.timer);
          state.timer = null;
        }
      };
      const requireMounted = () => {
        ensureActive('presentation.bubble');
        if (state.disposed) fail('disposed', 'The bubble presentation has been disposed');
      };
      const hide = () => {
        clearTimer();
        root.hidden = true;
        root.textContent = '';
      };
      const disposeController = () => {
        if (state.disposed) return;
        state.disposed = true;
        clearTimer();
        bubblePresentations.delete(state);
        root.remove?.();
      };
      return Object.freeze({
        element: root,
        get disposed() { return state.disposed; },
        show(value, options = {}) {
          requireMounted();
          const text = presentationText(value, 'bubble text', 2000, { required: true });
          const durationMs = options.durationMs === undefined
            ? 0
            : finiteNumber(options.durationMs, 'bubble durationMs', { minimum: 0, maximum: 60000 });
          clearTimer();
          root.textContent = text;
          root.hidden = false;
          if (durationMs > 0) {
            const setTimer = windowImpl.setTimeout?.bind(windowImpl) || globalThis.setTimeout;
            state.timer = setTimer(() => {
              state.timer = null;
              if (!state.disposed) {
                root.hidden = true;
                root.textContent = '';
              }
            }, durationMs);
          }
        },
        hide() { requireMounted(); hide(); },
        dispose: disposeController,
      });
    }

    function mountMemoryConsentPresentation(config = {}) {
      requireCapability('memory', 'presentation.memoryConsent.mount');
      if (config.onError !== undefined && typeof config.onError !== 'function') {
        fail('invalid_request', 'memory consent onError must be a function');
      }
      if (consentPresentations.size >= MAX_CONSENT_PRESENTATIONS) {
        fail('busy', 'Memory consent presentation limit reached', { limit: MAX_CONSENT_PRESENTATIONS });
      }
      const container = presentationContainer(config, 'presentation.memoryConsent.mount');
      const doc = container.ownerDocument || documentImpl;
      const root = doc.createElement('label');
      root.className = 'neko-minigame-consent';
      const input = doc.createElement('input');
      input.className = 'neko-minigame-consent__input';
      input.type = 'checkbox';
      input.checked = memoryConsentEnabled;
      const copy = doc.createElement('span');
      copy.className = 'neko-minigame-consent__copy';
      const labelText = doc.createElement('span');
      labelText.textContent = presentationText(config.label, 'memory consent label', 200, {
        required: true,
      });
      const hint = doc.createElement('span');
      hint.className = 'neko-minigame-consent__hint';
      hint.textContent = presentationText(config.hint, 'memory consent hint', 500);
      const status = doc.createElement('span');
      status.className = 'neko-minigame-consent__status';
      status.setAttribute('aria-live', 'polite');
      copy.append(labelText, hint, status);
      root.append(input, copy);
      container.appendChild(root);
      const state = {
        root,
        input,
        disposed: false,
        pending: false,
        acceptedValue: memoryConsentEnabled,
        runtimeUnsubscribe: null,
        memoryUnsubscribe: null,
        changeHandler: null,
      };
      consentPresentations.add(state);
      const updateDisabled = () => {
        input.disabled = state.pending || memoryConsentLocked || runtimePhase !== 'idle';
      };
      const sync = async () => {
        ensureActive('presentation.memoryConsent.sync');
        if (state.disposed) fail('disposed', 'The memory consent presentation has been disposed');
        if (state.pending) fail('busy', 'Memory consent update is already pending');
        state.pending = true;
        status.textContent = '';
        updateDisabled();
        try {
          const response = await memory.configureConsent(input.checked);
          if (response.ok && response.data?.ok !== false) {
            state.acceptedValue = input.checked;
            input.removeAttribute('aria-invalid');
            status.textContent = '';
          } else {
            input.checked = state.acceptedValue;
            input.setAttribute('aria-invalid', 'true');
            status.textContent = presentationText(
              config.errorMessage || response.data?.message || 'Memory consent was not accepted',
              'memory consent error message',
              500,
            );
          }
          return response;
        } catch (error) {
          input.checked = state.acceptedValue;
          input.setAttribute('aria-invalid', 'true');
          status.textContent = presentationText(
            config.errorMessage || error?.message || '',
            'memory consent error message',
            500,
          );
          try { config.onError?.(error); }
          catch (handlerError) {
            windowImpl.console?.error?.('[NekoMiniGame] memory consent onError failed', handlerError);
          }
          throw error;
        } finally {
          state.pending = false;
          updateDisabled();
        }
      };
      const changeHandler = () => { void sync().catch(() => null); };
      state.changeHandler = changeHandler;
      input.addEventListener('change', changeHandler);
      const updateFromRuntime = () => {
        if (!state.pending) {
          input.checked = memoryConsentEnabled;
          state.acceptedValue = memoryConsentEnabled;
        }
        updateDisabled();
      };
      state.runtimeUnsubscribe = subscribe('runtime-event:runtime-state', updateFromRuntime);
      state.memoryUnsubscribe = subscribe('memory-consent-state', updateFromRuntime);
      updateDisabled();
      const disposeController = () => {
        if (state.disposed) return;
        state.disposed = true;
        consentPresentations.delete(state);
        input.removeEventListener('change', changeHandler);
        state.changeHandler = null;
        state.runtimeUnsubscribe?.();
        state.runtimeUnsubscribe = null;
        state.memoryUnsubscribe?.();
        state.memoryUnsubscribe = null;
        root.remove?.();
      };
      return Object.freeze({
        element: root,
        input,
        get disposed() { return state.disposed; },
        get enabled() { return input.checked; },
        sync,
        dispose: disposeController,
      });
    }

    const presentation = Object.freeze({
      loading: Object.freeze({
        get activeCount() { return loadingPresentations.size; },
        mount: mountLoadingPresentation,
      }),
      bubble: Object.freeze({
        get activeCount() { return bubblePresentations.size; },
        mount: mountBubblePresentation,
      }),
      memoryConsent: Object.freeze({
        get activeCount() { return consentPresentations.size; },
        mount: mountMemoryConsentPresentation,
      }),
    });

    const dialogue = Object.freeze({
      get pendingCount() { return dialoguePendingRequests.size; },
      async quickLines(payload = {}, requestOptions = {}) {
        requireCapability('quick-lines', 'dialogue.quickLines');
        if (typeof transport.getQuickLines !== 'function') {
          fail('transport_unavailable', 'The host does not support dialogue quick lines');
        }
        if (!plainObject(payload)) fail('invalid_request', 'quick lines payload must be an object');
        assertNoForbiddenDialogueFields(payload);
        const boundedPayload = normalizeBoundedJson(payload, 'quick lines payload');
        const session = runtimeSession();
        const trustedPayload = runtimeCapabilityPayload({
          ...boundedPayload,
          session_id: session.id,
          ...(session.characterName ? { lanlan_name: session.characterName } : {}),
        });
        const rawResponse = await performManagedHostRequest({
          operation: 'dialogue.quickLines',
          pendingSet: dialoguePendingRequests,
          limit: MAX_DIALOGUE_PENDING_REQUESTS,
          timeoutMs: 15000,
          requestOptions,
          invoke: (options) => transport.getQuickLines(trustedPayload, options),
        });
        const response = await normalizeTransportResponse(rawResponse);
        return Object.freeze({
          ...response,
          data: normalizeBoundedJson(response.data, 'quick lines response'),
        });
      },
      async request(payload = {}, requestOptions = {}) {
        requireCapability('dialogue', 'dialogue.request');
        requireActiveRuntimeRoute('dialogue.request');
        if (!plainObject(payload)) fail('invalid_request', 'dialogue payload must be an object');
        const { prompt: rawPrompt, ...payloadWithoutPrompt } = payload;
        assertNoForbiddenDialogueFields(payloadWithoutPrompt);
        const authorPrompt = rawPrompt === undefined
          ? null
          : normalizeAuthorManagedDialoguePrompt(rawPrompt);
        const boundedPayload = normalizeBoundedJson({
          ...payloadWithoutPrompt,
          ...(authorPrompt ? { prompt: authorPrompt } : {}),
        }, 'dialogue payload');
        const session = runtimeSession();
        const trustedPayload = runtimeCapabilityPayload({
          ...boundedPayload,
          session_id: session.id,
          ...(session.characterName ? { lanlan_name: session.characterName } : {}),
        });
        const rawResponse = await performManagedHostRequest({
          operation: 'dialogue.request',
          pendingSet: dialoguePendingRequests,
          limit: MAX_DIALOGUE_PENDING_REQUESTS,
          timeoutMs: 60000,
          requestOptions,
          invoke: (options) => transport.requestDialogue(trustedPayload, options),
        });
        const response = await normalizeTransportResponse(rawResponse);
        let responseData = normalizeBoundedJson(response.data, 'dialogue response');
        if (plainObject(responseData) && responseData.control !== undefined) {
          if (!plainObject(responseData.control)) {
            fail('invalid_contract', 'dialogue response control must be an object');
          }
          const controlEntries = Object.entries(responseData.control);
          if (controlEntries.length > MAX_CONTRACTS_PER_KIND) {
            fail('invalid_contract', 'dialogue response contains too many controls', {
              limit: MAX_CONTRACTS_PER_KIND,
            });
          }
          const validatedControls = {};
          for (const [type, value] of controlEntries) {
            const { schema } = declaredContractSchema(
              'controls',
              type,
              'dialogue.response.control',
            );
            validatedControls[type] = normalizeContractPayload(
              value,
              schema,
              `dialogue response control.${type}`,
            );
          }
          responseData = Object.freeze({
            ...responseData,
            control: Object.freeze(validatedControls),
          });
        }
        return Object.freeze({
          ...response,
          data: responseData,
        });
      },
    });

    const logger = Object.freeze({
      configure(config = {}) {
        requireCapability('logging', 'logger.configure');
        return transport.configureLogger?.(config) || transport.logger;
      },
      log(...args) {
        requireCapability('logging', 'logger.log');
        return transport.logger.log(...args);
      },
      info(...args) {
        requireCapability('logging', 'logger.info');
        return transport.logger.info(...args);
      },
      warn(...args) {
        requireCapability('logging', 'logger.warn');
        return transport.logger.warn(...args);
      },
      error(...args) {
        requireCapability('logging', 'logger.error');
        return transport.logger.error(...args);
      },
      enable(reason) {
        requireCapability('logging', 'logger.enable');
        return transport.logger.enable(reason);
      },
      enableAfterRuntimeStart() {
        requireCapability('logging', 'logger.enableAfterRuntimeStart');
        return transport.logger.enableAfterRouteStart();
      },
      flush(options = {}) {
        requireCapability('logging', 'logger.flush');
        return transport.logger.flush(options);
      },
      reset() {
        requireCapability('logging', 'logger.reset');
        return transport.logger.reset();
      },
    });

    async function requestVoice(action = 'query', requestOptions = {}) {
      requireCapability('voice-input', 'voice.request');
      requireActiveRuntimeRoute(`voice.${action}`);
      if (!voiceBridgeStarted) {
        fail('transport_unavailable', 'The host voice bridge is unavailable');
      }
      try {
        return await transport.requestVoiceControl(action, {
          ...requestOptions,
          sdkRouteInstanceId: String(runtimeRouteInstanceId || '').trim(),
        });
      } catch (error) {
        throw normalizeTransportError(error, `voice.${action}`);
      }
    }

    const voice = Object.freeze({
      get connected() { return !disposed && !disposing && voiceBridgeStarted; },
      request: requestVoice,
      query(requestOptions) { return requestVoice('query', requestOptions); },
      start(requestOptions) { return requestVoice('start', requestOptions); },
      stop(requestOptions) { return requestVoice('stop', requestOptions); },
      toggle(requestOptions) { return requestVoice('toggle', requestOptions); },
      onState(handler) {
        requireCapability('voice-input', 'voice.onState');
        return subscribe('voice-state', handler);
      },
      onTranscript(handler) {
        requireCapability('voice-input', 'voice.onTranscript');
        return subscribe('voice-transcript', handler);
      },
      onError(handler) {
        requireCapability('voice-input', 'voice.onError');
        return subscribe('voice-error', handler);
      },
    });

    function abortPendingSpeechRequests(reason = 'cancelled') {
      abortManagedRequests(speechPendingRequests, reason);
      abortManagedRequests(speechPreloadPendingRequests, reason);
    }

    async function preloadSpeechOutput(linesInput, options = {}) {
      requireCapability('speech-output', 'speech.preload');
      if (!speechBridgeStarted) {
        fail('transport_unavailable', 'The host speech output bridge is unavailable');
      }
      const request = normalizeSpeechPreloadRequest(linesInput, options);
      const session = runtimeSession();
      const payload = runtimeCapabilityPayload({
        lines: request.lines,
        session_id: session.id,
        ...(session.characterName ? { lanlan_name: session.characterName } : {}),
        ...(request.language ? { i18n_language: request.language } : {}),
        ...(request.renderLanguage ? { render_language: request.renderLanguage } : {}),
      });
      const response = await performManagedHostRequest({
        operation: 'speech.preload',
        pendingSet: speechPreloadPendingRequests,
        limit: MAX_SPEECH_PRELOAD_PENDING_REQUESTS,
        timeoutMs: DEFAULT_SPEECH_PRELOAD_TIMEOUT_MS,
        maximumTimeoutMs: MAX_SPEECH_PRELOAD_TIMEOUT_MS,
        requestOptions: options,
        invoke: (requestOptions) => transport.preloadSpeechOutput(payload, requestOptions),
      });
      return normalizeTransportResponse(response);
    }

    async function requestSpeechOutput(requestInput, requestOptions = {}) {
      requireCapability('speech-output', 'speech.speak');
      if (!speechBridgeStarted) {
        fail('transport_unavailable', 'The host speech output bridge is unavailable');
      }
      const request = normalizeSpeechRequest(requestInput);
      const speechMetadata = Object.freeze({
        priority: request.priority,
        requestId: request.requestId,
        eventKey: request.eventKey,
      });
      let correlationId = '';
      const session = runtimeSession();
      const payload = Object.freeze({
        line: request.text,
        source: request.source,
        session_id: session.id,
        ...(session.characterName ? { lanlan_name: session.characterName } : {}),
        ...(request.requestId ? { request_id: request.requestId } : {}),
        ...(request.mirrorText !== undefined ? { mirror_text: request.mirrorText } : {}),
        ...(request.emitTurnEnd !== undefined ? { emit_turn_end: request.emitTurnEnd } : {}),
        interrupt_audio: request.interruptExisting,
        reuse_synthesized_audio: request.reuseSynthesizedAudio,
        // `speech.speak()` is awaited by game code, so it must resolve when the
        // line has actually been spoken. The host endpoint defaults to
        // returning as soon as the line is queued (the pre-SDK contract its
        // built-in callers rely on), so the SDK opts in explicitly. Without
        // this, two awaited speaks run concurrently and the second overwrites
        // the host's single speech-correlation slot, leaving the first
        // utterance uncancellable when the route ends.
        wait_for_audio_completion: true,
        playback_gain: request.relativeGain,
        ...(request.reason ? { voice_arbiter_reason: request.reason } : {}),
        ...(request.language ? { i18n_language: request.language } : {}),
        ...(request.renderLanguage ? { render_language: request.renderLanguage } : {}),
        event: request.event,
      });
      let response;
      try {
        response = await normalizeTransportResponse(await performManagedHostRequest({
          operation: 'speech.speak',
          pendingSet: speechPendingRequests,
          limit: MAX_SPEECH_PENDING_REQUESTS,
          timeoutMs: DEFAULT_SPEECH_REQUEST_TIMEOUT_MS,
          requestOptions,
          invoke: (options) => {
            correlationId = beginSpeechCorrelation(speechMetadata);
            return transport.requestSpeechOutput(runtimeCapabilityPayload({
              ...payload,
              sdk_speech_correlation_id: correlationId,
            }), options);
          },
        }));
      } catch (error) {
        speechCorrelationMetadata.delete(correlationId);
        throw error;
      }
      let speechId;
      try {
        speechId = boundedSpeechString(
          response.data?.speech_id || response.data?.speechId,
          'speech response id',
          128,
        );
      } catch (error) {
        speechCorrelationMetadata.delete(correlationId);
        throw error;
      }
      const pendingMetadata = speechCorrelationMetadata.get(correlationId) || null;
      if (speechId && pendingMetadata) {
        rememberSpeechRequest(speechId, pendingMetadata);
        speechCorrelationMetadata.delete(correlationId);
      } else if (response.ok === false || response.data?.audio_sent === false) {
        speechCorrelationMetadata.delete(correlationId);
      }
      return response;
    }

    async function mirrorSpeechOutput(requestInput, requestOptions = {}) {
      requireCapability('speech-output', 'speech.mirror');
      if (!speechBridgeStarted) {
        fail('transport_unavailable', 'The host speech output bridge is unavailable');
      }
      if (typeof transport.mirrorSpeechOutput !== 'function') {
        fail('transport_unavailable', 'The host does not support text-only speech mirroring');
      }
      const request = normalizeSpeechMirrorRequest(requestInput);
      const session = runtimeSession();
      const payload = runtimeCapabilityPayload({
        line: request.text,
        source: request.source,
        session_id: session.id,
        ...(session.characterName ? { lanlan_name: session.characterName } : {}),
        ...(request.requestId ? { request_id: request.requestId } : {}),
        ...(request.turnId ? { turn_id: request.turnId } : {}),
        ...(request.finalizeTurn !== undefined ? { finalize_turn: request.finalizeTurn } : {}),
        event: request.event,
      });
      return normalizeTransportResponse(await performManagedHostRequest({
        operation: 'speech.mirror',
        pendingSet: speechPendingRequests,
        limit: MAX_SPEECH_PENDING_REQUESTS,
        timeoutMs: DEFAULT_SPEECH_REQUEST_TIMEOUT_MS,
        requestOptions,
        invoke: (options) => transport.mirrorSpeechOutput(payload, options),
      }));
    }

    const speech = Object.freeze({
      get connected() { return !disposed && !disposing && speechBridgeStarted; },
      get pendingCount() { return speechPendingRequests.size; },
      get preloadPendingCount() { return speechPreloadPendingRequests.size; },
      speak: requestSpeechOutput,
      mirror: mirrorSpeechOutput,
      preload: preloadSpeechOutput,
      getState() {
        requireCapability('speech-output', 'speech.getState');
        return currentSpeechPlaybackState() || Object.freeze({
          active: false,
          speechId: '',
          turnId: '',
          playbackTurnId: '',
          remainingSeconds: 0,
          pendingAudioWork: false,
          audioContextState: '',
          audioContextTime: 0,
          scheduledEndAudioTime: 0,
          playbackStartAudioTime: 0,
          playbackEndAudioTime: 0,
          updatedAt: 0,
          ageMs: 0,
          reason: 'missing',
          source: '',
          transportSource: '',
          priority: null,
          requestId: '',
          eventKey: '',
        });
      },
      onState(handler) {
        requireCapability('speech-output', 'speech.onState');
        return subscribe('speech-state', handler);
      },
      onError(handler) {
        requireCapability('speech-output', 'speech.onError');
        return subscribe('speech-error', handler);
      },
    });

    function disposeAudioController(controllerState) {
      if (!controllerState || controllerState.disposed) return;
      controllerState.disposed = true;
      audioControllers.delete(controllerState);
      try {
        const result = controllerState.raw.dispose();
        if (result && typeof result.catch === 'function') {
          result.catch((error) => global.console?.error?.(
            '[NekoMiniGame] audio controller disposal failed',
            error,
          ));
        }
      } catch (error) {
        global.console?.error?.('[NekoMiniGame] audio controller disposal failed', error);
      }
    }

    const audio = Object.freeze({
      get activeCount() { return audioControllers.size; },
      async mount(configInput) {
        requireCapability('audio', 'audio.mount');
        if (audioControllers.size + audioMountsPending >= MAX_AUDIO_CONTROLLERS) {
          fail('busy', 'Audio controller limit reached', { limit: MAX_AUDIO_CONTROLLERS });
        }
        const config = normalizeAudioMountConfig(configInput);
        audioMountsPending += 1;
        let raw;
        try {
          raw = await transport.mountAudio(Object.freeze({ ...config, gameId: manifest.id }));
        } catch (error) {
          throw normalizeTransportError(error, 'audio.mount');
        } finally {
          audioMountsPending -= 1;
        }
        if (!raw || typeof raw !== 'object' || typeof raw.dispose !== 'function') {
          try { raw?.dispose?.(); } catch (_) { /* invalid host controller cleanup */ }
          fail('transport_unavailable', 'The host returned an invalid audio controller');
        }
        if (disposed) {
          try { raw.dispose(); } catch (_) { /* client disposed while mounting */ }
          fail('disposed', 'The mini-game SDK client has been disposed', { operation: 'audio.mount' });
        }
        const controllerState = { raw, disposed: false };
        audioControllers.add(controllerState);

        function requireController(method) {
          ensureActive(`audio.${method}`);
          if (controllerState.disposed) {
            fail('disposed', 'The audio controller has been disposed', { operation: `audio.${method}` });
          }
          if (typeof raw[method] !== 'function') {
            fail('transport_unavailable', `The host audio controller does not support ${method}`);
          }
        }

        function call(method, ...args) {
          requireController(method);
          try {
            const result = raw[method](...args);
            if (result && typeof result.then === 'function') {
              return result.catch((error) => {
                throw normalizeTransportError(error, `audio.${method}`);
              });
            }
            return result;
          } catch (error) {
            throw normalizeTransportError(error, `audio.${method}`);
          }
        }

        return Object.freeze({
          config,
          get disposed() { return controllerState.disposed || disposed || disposing; },
          configure(resources) {
            return call('configure', normalizeAudioResources(resources, 'audio resources'));
          },
          playBgm(value, optionsInput = {}) {
            return call(
              'playBgm',
              normalizeAudioValue(value, 'audio BGM'),
              normalizeAudioValue(optionsInput, 'audio BGM options'),
            );
          },
          waitForBgmEnd(optionsInput = {}) {
            return call('waitForBgmEnd', normalizeAudioValue(optionsInput, 'audio wait options'));
          },
          playLoopedBgm(value, optionsInput = {}) {
            return call(
              'playLoopedBgm',
              normalizeAudioValue(value, 'audio looped BGM'),
              normalizeAudioValue(optionsInput, 'audio looped BGM options'),
            );
          },
          stopLoopedBgm(optionsInput = {}) {
            return call(
              'stopLoopedBgm',
              normalizeAudioValue(optionsInput, 'audio stop options'),
            );
          },
          finishLoopedBgm() { return call('finishLoopedBgm'); },
          playSfx(value, optionsInput = {}) {
            return call(
              'playSfx',
              normalizeAudioValue(value, 'audio SFX'),
              normalizeAudioValue(optionsInput, 'audio SFX options'),
            );
          },
          preloadBgm(value) { return call('preloadBgm', normalizeAudioValue(value, 'audio BGM')); },
          preloadLoopedBgm(value) {
            return call('preloadLoopedBgm', normalizeAudioValue(value, 'audio looped BGM'));
          },
          preloadSfx(value) { return call('preloadSfx', normalizeAudioValue(value, 'audio SFX')); },
          unloadBgm(value) { return call('unloadBgm', normalizeAudioValue(value, 'audio BGM')); },
          unloadLoopedBgm(value) {
            return call('unloadLoopedBgm', normalizeAudioValue(value, 'audio looped BGM'));
          },
          unloadSfx(value) { return call('unloadSfx', normalizeAudioValue(value, 'audio SFX')); },
          setBgmVolume(value) {
            return call('setBgmVolume', finiteNumber(value, 'audio BGM volume', { minimum: 0, maximum: 1 }));
          },
          getBgmVolume() { return call('getBgmVolume'); },
          setSfxVolume(value) {
            return call('setSfxVolume', finiteNumber(value, 'audio SFX volume', { minimum: 0, maximum: 1 }));
          },
          getSfxVolume() { return call('getSfxVolume'); },
          getCurrentBgmSrc() { return call('getCurrentBgmSrc'); },
          isCurrentBgm(value) {
            return call('isCurrentBgm', normalizeAudioValue(value, 'audio BGM'));
          },
          pauseBgm() { return call('pauseBgm'); },
          resumeBgm() { return call('resumeBgm'); },
          stopBgm() { return call('stopBgm'); },
          unlock() { return call('unlock'); },
          onError(handler) {
            if (typeof handler !== 'function') {
              fail('invalid_request', 'Audio error handler must be a function');
            }
            return call('onError', handler);
          },
          getState() {
            const state = call('getState');
            return state && typeof state === 'object' ? Object.freeze({ ...state }) : Object.freeze({});
          },
          dispose() { disposeAudioController(controllerState); },
        });
      },
      disposeAll() {
        for (const controllerState of Array.from(audioControllers)) {
          disposeAudioController(controllerState);
        }
      },
    });

    function disposeAvatarController(controllerState) {
      if (!controllerState || controllerState.disposed) return;
      controllerState.disposed = true;
      avatarRenderers.delete(controllerState);
      try {
        const result = controllerState.raw.dispose();
        if (result && typeof result.catch === 'function') {
          result.catch((error) => global.console?.error?.(
            '[NekoMiniGame] avatar controller disposal failed',
            error,
          ));
        }
      } catch (error) {
        global.console?.error?.('[NekoMiniGame] avatar controller disposal failed', error);
      }
    }

    const avatar = Object.freeze({
      get activeCount() { return avatarRenderers.size; },
      async mount(configInput) {
        requireCapability('avatar-renderer', 'avatar.mount');
        if (avatarRenderers.size + avatarMountsPending >= MAX_AVATAR_RENDERERS) {
          fail('busy', 'Avatar renderer limit reached', { limit: MAX_AVATAR_RENDERERS });
        }
        const config = normalizeAvatarConfig(configInput);
        avatarMountsPending += 1;
        let raw;
        try {
          raw = await transport.mountAvatar(config);
        } catch (error) {
          throw normalizeTransportError(error, 'avatar.mount');
        } finally {
          avatarMountsPending -= 1;
        }
        if (!raw || typeof raw !== 'object' || typeof raw.dispose !== 'function') {
          try { raw?.dispose?.(); } catch (_) { /* invalid host controller cleanup */ }
          fail('transport_unavailable', 'The host returned an invalid avatar controller');
        }
        if (disposed) {
          try { raw.dispose(); } catch (_) { /* client disposed while mounting */ }
          fail('disposed', 'The mini-game SDK client has been disposed', { operation: 'avatar.mount' });
        }
        const controllerState = { raw, disposed: false };
        avatarRenderers.add(controllerState);

        function requireController(method) {
          ensureActive(`avatar.${method}`);
          if (controllerState.disposed) {
            fail('disposed', 'The avatar controller has been disposed', { operation: `avatar.${method}` });
          }
          if (typeof raw[method] !== 'function') {
            fail('transport_unavailable', `The host avatar controller does not support ${method}`);
          }
        }

        function callController(method, invoke) {
          requireController(method);
          try {
            const result = invoke();
            if (result && typeof result.then === 'function') {
              return Promise.resolve(result).catch((error) => {
                throw normalizeTransportError(error, `avatar.${method}`);
              });
            }
            return result;
          } catch (error) {
            throw normalizeTransportError(error, `avatar.${method}`);
          }
        }

        return Object.freeze({
          config,
          get disposed() { return controllerState.disposed || disposed || disposing; },
          async setModel(modelInput) {
            return callController('setModel', () => raw.setModel(normalizeAvatarModel(modelInput)));
          },
          focus(pointInput) {
            return callController('focus', () => raw.focus(normalizeAvatarFocus(pointInput)));
          },
          setEmotion(name) {
            return callController('setEmotion', () => {
              const emotion = String(name || '').trim();
              if (!emotion || emotion.length > 64) {
                fail('invalid_request', 'avatar emotion name is required');
              }
              return raw.setEmotion(emotion);
            });
          },
          pause() {
            return callController('pause', () => raw.pause());
          },
          resume() {
            return callController('resume', () => raw.resume());
          },
          getState() {
            const state = callController('getState', () => raw.getState());
            return state && typeof state === 'object' ? Object.freeze({ ...state }) : Object.freeze({});
          },
          dispose() { disposeAvatarController(controllerState); },
        });
      },
      disposeAll() {
        for (const controllerState of Array.from(avatarRenderers)) {
          disposeAvatarController(controllerState);
        }
      },
    });

    const client = {
      manifest,
      host: Object.freeze({
        version: handshake.hostVersion,
        protocolVersion: handshake.protocolVersion,
        registration: handshake.registration,
      }),
      capabilities,
      runtime,
      events,
      state,
      controls,
      results,
      context,
      memory,
      storage,
      leaderboard,
      presentation,
      dialogue,
      logger,
      voice,
      speech,
      audio,
      avatar,
      get disposed() { return disposed || disposing; },
      dispose(disposeOptions = {}) {
        if (disposed || disposing) return;
        disposing = true;
        runtimeRouteEstablished = false;
        clearRuntimeRouteInstanceIds();
        stopRuntimeMonitoring();
        stopRuntimeOperation({ preserveEnd: disposeOptions.preserveRuntimeEnd === true });
        abortPendingSpeechRequests('disposed');
        abortPendingProtocolRequests('disposed');
        abortManagedRequests(contextPendingRequests, 'disposed');
        abortManagedRequests(dialoguePendingRequests, 'disposed');
        abortManagedRequests(memoryPendingRequests, 'disposed');
        abortManagedRequests(storagePendingRequests, 'disposed');
        abortManagedRequests(localLeaderboardPendingRequests, 'disposed');
        abortManagedRequests(localLeaderboardMutationPendingRequests, 'disposed');
        abortManagedRequests(serverLeaderboardPendingRequests, 'disposed');
        localLeaderboardMutations.clear();
        setRuntimePhase('disposed', 'client-dispose');
        disposed = true;
        disposing = false;
        for (const item of Array.from(loadingPresentations)) {
          item.disposed = true;
          item.root?.remove?.();
        }
        loadingPresentations.clear();
        for (const item of Array.from(bubblePresentations)) {
          if (item.timer != null) {
            const clear = windowImpl.clearTimeout?.bind(windowImpl) || globalThis.clearTimeout;
            clear(item.timer);
            item.timer = null;
          }
          item.disposed = true;
          item.root?.remove?.();
        }
        bubblePresentations.clear();
        for (const item of Array.from(consentPresentations)) {
          item.input?.removeEventListener?.('change', item.changeHandler);
          item.changeHandler = null;
          item.runtimeUnsubscribe?.();
          item.runtimeUnsubscribe = null;
          item.memoryUnsubscribe?.();
          item.memoryUnsubscribe = null;
          item.root?.remove?.();
          item.disposed = true;
        }
        consentPresentations.clear();
        listeners.clear();
        runtimeConfig = null;
        voiceBridgeStarted = false;
        speechBridgeStarted = false;
        controlBridgeStarted = false;
        speechPlaybackRawState = null;
        speechPlaybackTransportSource = '';
        speechRequestMetadata.clear();
        speechCorrelationMetadata.clear();
        for (const controllerState of Array.from(audioControllers)) {
          disposeAudioController(controllerState);
        }
        for (const controllerState of Array.from(avatarRenderers)) {
          disposeAvatarController(controllerState);
        }
        if (typeof transport.dispose === 'function') {
          const preservePendingOperations = disposeOptions.preserveRuntimeEnd ? ['route_end'] : [];
          transport.dispose({ preservePendingOperations });
        } else {
          try { transport.stopVoiceControlBridge?.('disposed'); } catch (_) { /* already stopped */ }
          try { transport.stopSpeechOutputBridge?.('disposed'); } catch (_) { /* already stopped */ }
          try { transport.stopGameControlBridge?.('disposed'); } catch (_) { /* already stopped */ }
        }
      },
    };
    return Object.freeze(client);
  }

  global.NekoMiniGame = Object.freeze({
    version: SDK_VERSION,
    protocolVersion: SDK_PROTOCOL_VERSION,
    supportedCapabilities: SUPPORTED_CAPABILITIES,
    mandatoryCapabilities: MANDATORY_CAPABILITIES,
    connect,
    Error: NekoMiniGameError,
  });
})(window);
