const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const REGISTRY_PATH = path.join(
  PROJECT_ROOT,
  'static/avatar/avatar-ui-buttons/cat-resource-registry.js',
);
const IDLE_DRAG_PATH = path.join(
  PROJECT_ROOT,
  'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js',
);
const APPEARANCE_DIR = path.join(
  PROJECT_ROOT,
  'static/assets/cat-resources/appearance/dev_neko',
);
const ACTION_DIR = path.join(
  APPEARANCE_DIR,
  'action',
);
const RESOURCE_GROUPS = ['dev_neko', 'yui_neko'];
const REGISTRY_TEMPLATES = [
  'templates/index.html',
  'templates/chat.html',
  'templates/viewer.html',
  'templates/card_maker.html',
  'templates/character_card_manager.html',
  'templates/drawing_guess.html',
  'templates/live2d_parameter_editor.html',
  'templates/model_manager.html',
  'templates/soccer_demo.html',
];

const VISUAL_ACTION_ASSETS = Object.freeze([
  { actionId: 'cat1_happy', slot: 'action.cat1.happy', file: 'cat-idle-cat1-happy.webp', durationMs: 4040, bounds: [512, 512, 83, 48, 406, 493], metadata: {} },
  { actionId: 'cat1_touch', slot: 'action.cat1.touch', file: 'cat-idle-cat1-touch.webp', durationMs: 6040, bounds: [512, 512, 59, 0, 437, 510], metadata: {} },
  { actionId: 'cat1_cosplay_1', slot: 'action.cat1.cosplay_1', file: 'cat-idle-cat1-cos-1.webp', durationMs: 4040, bounds: [512, 512, 2, 3, 495, 510], metadata: {} },
  { actionId: 'cat1_change', slot: 'action.cat1.change', file: 'cat-idle-cat1-change.webp', durationMs: 6040, bounds: [512, 512, 33, 24, 501, 512], metadata: {} },
  { actionId: 'cat1_cosplay_2', slot: 'action.cat1.cosplay_2', file: 'cat-idle-cat1-cos-2.webp', durationMs: 6040, bounds: [512, 512, 83, 41, 408, 498], metadata: {} },
  { actionId: 'cat1_eat_2', slot: 'action.cat1.eat_2', file: 'cat-idle-cat1-eat-2.webp', durationMs: 5040, bounds: [512, 512, 27, 1, 401, 512], metadata: {} },
  { actionId: 'cat1_eat_3', slot: 'action.cat1.eat_3', file: 'cat-idle-cat1-eat-3.webp', durationMs: 4040, bounds: [512, 512, 53, 46, 463, 493], metadata: {} },
  { actionId: 'cat1_laugh', slot: 'action.cat1.laugh', file: 'cat-idle-cat1-laugh.webp', durationMs: 4040, bounds: [512, 512, 89, 46, 408, 501], metadata: {} },
  { actionId: 'cat1_lion_dance', slot: 'action.cat1.lion_dance', file: 'cat-idle-cat1-lion_dance.webp', durationMs: 4040, bounds: [512, 704, 76, 4, 436, 687], metadata: { artLayout: 'tall-bottom' } },
  { actionId: 'cat1_love_1', slot: 'action.cat1.love_1', file: 'cat-idle-cat1-love-1.webp', durationMs: 4040, bounds: [512, 512, 53, 49, 396, 496], metadata: {} },
  { actionId: 'cat1_mouse', slot: 'action.cat1.mouse', file: 'cat-idle-cat1-mouse.webp', durationMs: 5040, bounds: [896, 512, 6, 42, 851, 508], metadata: { wideArt: true, artLayout: 'wide-centered' } },
  { actionId: 'cat1_wave', slot: 'action.cat1.wave', file: 'cat-idle-cat1-wave.webp', durationMs: 4040, bounds: [512, 512, 80, 48, 399, 510], metadata: {} },
  { actionId: 'cat1_sad', slot: 'action.cat1.sad', file: 'cat-idle-cat1-sad.webp', durationMs: 4040, bounds: [512, 512, 86, 54, 434, 493], metadata: {} },
  { actionId: 'cat1_scared', slot: 'action.cat1.scared', file: 'cat-idle-cat1-scared.webp', durationMs: 4040, bounds: [512, 512, 27, 1, 434, 512], metadata: {} },
  { actionId: 'cat1_stroke', slot: 'action.cat1.stroke', file: 'cat-idle-cat1-stroke.webp', durationMs: 7980, bounds: [512, 512, 86, 14, 435, 492], metadata: {} },
  { actionId: 'cat1_wagging', slot: 'action.cat1.wagging', file: 'cat-idle-cat1-wagging.webp', durationMs: 4040, bounds: [512, 512, 0, 44, 400, 512], metadata: {} },
]);

function loadRegistry(source = fs.readFileSync(REGISTRY_PATH, 'utf8'), overrides = {}, group = 'dev_neko') {
  const window = { __nekoCatResourceGroup: group };
  vm.runInNewContext(source, { window, ...overrides }, {
    filename: REGISTRY_PATH,
  });
  return window.NekoCatResourceRegistry;
}

function loadGifDurationParser() {
  const source = fs.readFileSync(IDLE_DRAG_PATH, 'utf8');
  const start = source.indexOf('function _readUint16LittleEndian(');
  const end = source.indexOf('function _patchGifDelayRate(', start);
  assert.ok(start >= 0 && end > start, 'GIF duration parser slice');
  const context = vm.createContext({ Math, Number, Uint8Array });
  vm.runInContext(`${source.slice(start, end)}\nthis.parseGifDurationMs = _parseGifDurationMs;`, context);
  return context.parseGifDurationMs;
}

function parseAnimatedWebpDurationMs(buffer) {
  assert.equal(buffer.subarray(0, 4).toString('ascii'), 'RIFF');
  assert.equal(buffer.subarray(8, 12).toString('ascii'), 'WEBP');
  let offset = 12;
  let durationMs = 0;
  let frameCount = 0;
  while (offset + 8 <= buffer.length) {
    const tag = buffer.subarray(offset, offset + 4).toString('ascii');
    const size = buffer.readUInt32LE(offset + 4);
    const payload = offset + 8;
    if (tag === 'ANMF') {
      durationMs += buffer.readUIntLE(payload + 12, 3);
      frameCount += 1;
    }
    offset = payload + size + (size & 1);
  }
  assert.ok(frameCount > 0, 'animated WebP must contain ANMF frames');
  return durationMs;
}

function expectedInsets(bounds, layout = 'normal') {
  const [width, height, left, top, right, bottom] = bounds;
  const boxWidth = layout === 'wide-centered' ? 1.75 : 1;
  const boxHeight = layout === 'tall-bottom' ? 1.375 : 1;
  const scale = Math.min(boxWidth / width, boxHeight / height);
  const x = (1 - width * scale) / 2;
  const y = layout === 'tall-bottom' ? 1 - height * scale : (1 - height * scale) / 2;
  return {
    left: x + left * scale,
    top: y + top * scale,
    right: 1 - (x + right * scale),
    bottom: 1 - (y + bottom * scale),
  };
}

function textFilesUnder(directory) {
  const extensions = new Set(['.css', '.html', '.js', '.json', '.md']);
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return textFilesUnder(target);
    return entry.isFile() && extensions.has(path.extname(entry.name)) ? [target] : [];
  });
}

// Slot ids the registry source declares for one group block, so extra registry
// slots that the manifest does not describe are caught too.
function registrySlotIds(blockStart, blockEnd, groupId) {
  const source = fs.readFileSync(REGISTRY_PATH, 'utf8');
  const start = source.indexOf(blockStart);
  const end = source.indexOf(blockEnd, start);
  assert.ok(start >= 0 && end > start, `registry block ${blockStart}`);
  const groupStart = source.indexOf(`        ${groupId}: {`, start);
  assert.ok(groupStart >= start && groupStart < end, `registry group ${groupId}`);
  const groupEnd = Math.min(
    ...['\n        },', '\n        }\n'].map((marker) => {
      const index = source.indexOf(marker, groupStart);
      return index >= 0 && index <= end ? index : Number.POSITIVE_INFINITY;
    }),
  );
  assert.ok(groupEnd > groupStart && groupEnd <= end, `registry group end ${groupId}`);
  return [...source.slice(groupStart, groupEnd).matchAll(/^\s*'([a-z0-9_.]+)':/gm)].map((match) => match[1]).sort();
}

function assertManifestMatchesRegistry(manifest, readSlot, registrySlots, groupId) {
  assert.equal(manifest.group_id, groupId);
  assert.deepEqual(Object.keys(manifest.slots).sort(), registrySlots, `${manifest.kind} slot sets differ`);
  for (const [slot, entry] of Object.entries(manifest.slots)) {
    const expectedUrls = entry.resources.map((resource) => resource.path);
    const actual = readSlot(slot, { random: false });
    assert.equal(actual.available, true, `${manifest.kind}:${slot} should be available`);
    assert.equal(actual.groupId, groupId, `${manifest.kind}:${slot} group`);
    assert.deepEqual(Array.from(actual.urls), expectedUrls, `${manifest.kind}:${slot} paths`);
    assert.deepEqual({ ...actual.metadata }, entry.metadata || {}, `${manifest.kind}:${slot} metadata`);
    for (const resource of entry.resources) {
      assert.equal(
        fs.existsSync(path.join(PROJECT_ROOT, resource.path.slice(1))),
        true,
        `${manifest.kind}:${slot} missing ${resource.path}`,
      );
      assert.equal(
        fs.existsSync(path.join(PROJECT_ROOT, 'static/assets/neko-idle', path.basename(resource.path))),
        false,
        `${manifest.kind}:${slot} still ships a copy in the retired media location`,
      );
    }
  }
}

test('static cat manifests and registry stay slot-compatible', () => {
  for (const groupId of RESOURCE_GROUPS) {
    const registry = loadRegistry(undefined, {}, groupId);
    const appearance = JSON.parse(fs.readFileSync(
      path.join(PROJECT_ROOT, `static/assets/cat-resources/appearance/${groupId}/manifest.json`),
      'utf8',
    ));
    const voice = JSON.parse(fs.readFileSync(
      path.join(PROJECT_ROOT, `static/assets/cat-resources/voice/${groupId}/manifest.json`),
      'utf8',
    ));

    assert.equal(registry.selectedAppearanceGroupId, groupId);
    assert.equal(registry.selectedVoiceGroupId, groupId);
    assertManifestMatchesRegistry(appearance, registry.getAppearance,
      registrySlotIds('const appearanceGroups', 'const voiceGroups', groupId), groupId);
    assertManifestMatchesRegistry(voice, registry.getVoice,
      registrySlotIds('const voiceGroups', 'const actionDependencies', groupId), groupId);
  }

  const registry = loadRegistry();

  assert.equal(registry.getAppearance('action.cat1.play_yarn', { random: false }).metadata.wideArt, true);
  for (const actionId of [
    'cat1_social_ping',
    'cat1_eat_snack',
    'cat1_small_move',
    'cat1_play_yarn',
    'cat1_idle_random',
    'cat1_inactive_sad',
    'cat2_nap_feedback',
    'cat3_sleep_feedback',
    'cat1_hiss_stretch',
    ...VISUAL_ACTION_ASSETS.map((entry) => entry.actionId),
  ]) {
    assert.equal(registry.getActionCapabilities(actionId).available, true, actionId);
  }
  const unknownAction = registry.getActionCapabilities('missing-action');
  assert.equal(unknownAction.available, false);
  assert.equal(unknownAction.reason, 'unknown_action');
});

test('every animation in the CAT1 action directory is registered exactly once', () => {
  const actionFiles = fs.readdirSync(ACTION_DIR)
    .filter((name) => name.endsWith('.gif') || name.endsWith('.webp'))
    .sort();
  const manifest = JSON.parse(fs.readFileSync(path.join(
    PROJECT_ROOT,
    'static/assets/cat-resources/appearance/dev_neko/manifest.json',
  ), 'utf8'));
  const registeredActionFiles = Object.values(manifest.slots)
    .flatMap((entry) => entry.resources)
    .map((resource) => resource.path)
    .filter((resourcePath) => resourcePath.includes('/appearance/dev_neko/action/'))
    .map((resourcePath) => path.basename(resourcePath))
    .sort();

  assert.equal(actionFiles.length, 18, 'audited action directory size');
  assert.deepEqual(registeredActionFiles, actionFiles, 'no action asset may remain outside the manifest');
  assert.equal(new Set(registeredActionFiles).size, registeredActionFiles.length,
    'one action file must not be assigned to multiple slots');
  assert.equal(fs.existsSync(path.join(ACTION_DIR, 'cat-idle-cat1-cos1.gif')), false,
    'retired cosplay filename is removed');
  assert.equal(fs.existsSync(path.join(ACTION_DIR, 'cat-idle-cat1-cos-1.webp')), true,
    'renamed cosplay filename exists');
  assert.equal(
    manifest.slots['action.cat1.cosplay_1'].resources[0].path.endsWith('/cat-idle-cat1-cos-1.webp'),
    true,
  );
  for (const root of [path.join(PROJECT_ROOT, 'static'), path.join(PROJECT_ROOT, 'docs/design')]) {
    for (const filename of textFilesUnder(root)) {
      assert.doesNotMatch(
        fs.readFileSync(filename, 'utf8'),
        /cat-idle-cat1-cos1\.gif/,
        `${path.relative(PROJECT_ROOT, filename)} has no retired cosplay filename`,
      );
    }
  }
});

test('preload resource group takes precedence over stale session storage', () => {
  const source = fs.readFileSync(REGISTRY_PATH, 'utf8');
  const window = {
    __nekoCatResourceGroup: 'yui_neko',
    sessionStorage: { getItem: () => 'dev_neko' },
  };
  vm.runInNewContext(source, { window }, { filename: REGISTRY_PATH });
  assert.equal(window.NekoCatResourceRegistry.selectedAppearanceGroupId, 'yui_neko');
  assert.equal(window.NekoCatResourceRegistry.selectedVoiceGroupId, 'yui_neko');
});

test('both resource groups provide stable mirrored collision bounds for every appearance asset', () => {
  for (const groupId of RESOURCE_GROUPS) {
    const registry = loadRegistry(undefined, {}, groupId);
    const manifest = JSON.parse(fs.readFileSync(
      path.join(PROJECT_ROOT, `static/assets/cat-resources/appearance/${groupId}/manifest.json`), 'utf8'));
    for (const entry of Object.values(manifest.slots)) {
      for (const { path: url } of entry.resources) {
        const hull = registry.getAppearanceVisibleInsets(url);
        assert.ok(hull, url);
        const mirrored = registry.getAppearanceVisibleInsets(url, { facingRight: true });
        assert.equal(mirrored.left, hull.right);
        assert.equal(mirrored.right, hull.left);
        assert.equal(mirrored.top, hull.top);
        assert.equal(mirrored.bottom, hull.bottom);
        assert.equal(registry.getAppearanceVisibleInsets(`https://localhost${url}?v=1`), hull);
      }
    }
    assert.equal(registry.getAppearanceVisibleInsets('blob:unknown'), null);
  }
});

test('every visual-only action keeps its audited one-cycle duration, layout and visible hull', () => {
  const registry = loadRegistry();
  const parseGifDurationMs = loadGifDurationParser();
  for (const entry of VISUAL_ACTION_ASSETS) {
    const resource = registry.getAppearance(entry.slot, { random: false });
    assert.equal(resource.available, true, entry.slot);
    assert.deepEqual({ ...resource.metadata }, entry.metadata, `${entry.slot} layout metadata`);
    assert.equal(registry.getAppearanceDurationMs(resource.url), entry.durationMs,
      `${entry.slot} registered one-cycle duration`);
    if (entry.file.endsWith('.gif')) {
      const bytes = new Uint8Array(fs.readFileSync(path.join(ACTION_DIR, entry.file)));
      assert.equal(parseGifDurationMs(bytes), entry.durationMs, `${entry.slot} GIF duration`);
    }

    const layout = entry.metadata.artLayout || 'normal';
    const normal = expectedInsets(entry.bounds, layout);
    assert.deepEqual({ ...registry.getAppearanceVisibleInsets(resource.url) }, normal,
      `${entry.slot} audited visible hull`);
    assert.deepEqual({ ...registry.getAppearanceVisibleInsets(resource.url, { facingRight: true }) }, {
      ...normal,
      left: normal.right,
      right: normal.left,
    }, `${entry.slot} mirrored visible hull`);
  }
});

test('every animated WebP appearance has its exact one-cycle duration registered', () => {
  const registry = loadRegistry();
  const files = fs.readdirSync(APPEARANCE_DIR, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.webp'))
    .map((entry) => path.join(entry.parentPath, entry.name));
  assert.equal(files.length, 29);
  for (const filename of files) {
    const relative = path.relative(APPEARANCE_DIR, filename).split(path.sep).join('/');
    const url = `/static/assets/cat-resources/appearance/dev_neko/${relative}`;
    assert.equal(
      registry.getAppearanceDurationMs(url),
      parseAnimatedWebpDurationMs(fs.readFileSync(filename)),
      `${relative} registered duration`,
    );
  }
});

test('compact mirror messages carry wide-art metadata from the active registry', () => {
  const coreSource = fs.readFileSync(
    path.join(PROJECT_ROOT, 'static/avatar/avatar-ui-buttons/core.js'),
    'utf8',
  );
  const dragSource = fs.readFileSync(
    path.join(PROJECT_ROOT, 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js'),
    'utf8',
  );
  const mirrorSource = fs.readFileSync(
    path.join(PROJECT_ROOT, 'static/app/app-react-chat-window/bootstrap-state-and-geometry.js'),
    'utf8',
  );
  assert.match(coreSource, /function _isNekoCatAppearanceWideArtUrl\(slot, assetUrl\)/);
  assert.match(dragSource, /const assetUrl = options\.assetUrl \|\| currentArtUrl \|\| _getNekoIdleReturnAssetUrl\(_NEKO_IDLE_TIER_CAT1\)/);
  assert.match(dragSource, /const artLayout = requestedArtLayout === 'wide-centered' \|\| requestedArtLayout === 'tall-bottom'/);
  assert.match(dragSource, /artLayout,/);
  assert.match(dragSource, /wideArt:\s*artLayout === 'wide-centered' \|\|/);
  assert.match(mirrorSource, /detail && detail\.wideArt === true/);
  assert.match(mirrorSource, /data-neko-cat1-art-layout/);
});

function createCompactMirrorProducer(registry, idleUrl) {
  const coreSource = fs.readFileSync(
    path.join(PROJECT_ROOT, 'static/avatar/avatar-ui-buttons/core.js'),
    'utf8',
  );
  const helperStart = coreSource.indexOf('function _isNekoCatAppearanceWideArtUrl(');
  const helperEnd = coreSource.indexOf('\nconst _NEKO_IDLE_CAT1_RAPID_DRAG_ASSET_URL', helperStart);
  assert.ok(helperStart >= 0 && helperEnd > helperStart, 'missing wide-art producer helper');
  const source = fs.readFileSync(
    path.join(PROJECT_ROOT, 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js'),
    'utf8',
  );
  const start = source.indexOf('function _getNekoIdleScreenRectFromCompactSurfaceRect(');
  const end = source.indexOf('function _deactivateNekoIdleCat1CompactMirrors(', start);
  assert.ok(start >= 0 && end > start, 'missing compact mirror producer');
  const events = [];
  const window = {
    dispatchEvent(event) {
      events.push(event.detail);
      return true;
    },
    appInterpage: null,
  };
  const context = vm.createContext({
    _NEKO_CAT_RESOURCE_REGISTRY: registry,
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_OVERLAP_PX: 28,
    _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_SIDE_PADDING_PX: 12,
    _getNekoIdleReturnAssetUrl: () => idleUrl,
    _normalizeNekoIdleScreenRect: (rect) => rect,
    _getNekoDesktopVirtualViewportOrigin: () => ({ x: 0, y: 0 }),
    _getNekoDesktopVirtualElementRect: () => ({ width: 80, height: 80 }),
    _getNekoIdleCurrentLanlanName: () => '',
    clearTimeout: () => {},
    setTimeout: () => 1,
    CustomEvent: function CustomEvent(type, init) {
      this.type = type;
      this.detail = init.detail;
    },
    Date,
    console,
    window,
  });
  vm.runInContext(
    `${coreSource.slice(helperStart, helperEnd)}\n${source.slice(start, end)}\nthis.producer = _setNekoIdleCat1CompactMirrorActive;`,
    context,
  );
  const producer = context.producer;
  const makeContainer = () => ({
    id: 'cat1',
    classList: { add() {}, remove() {} },
  });
  return {
    events,
    producer,
    makeContainer,
  };
}

test('compact mirror producer marks only the actual wide-art asset', () => {
  const playYarnUrl = '/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat-play-1.webp';
  const idleUrl = '/static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat1.webp';
  const producer = createCompactMirrorProducer({
    getAppearance(slot) {
      if (slot === 'action.cat1.play_yarn') {
        return { available: true, urls: [playYarnUrl], metadata: { wideArt: true } };
      }
      return { available: true, urls: [idleUrl], metadata: {} };
    },
  }, idleUrl);

  assert.equal(producer.producer(null, producer.makeContainer(), true, {
    surfaceRect: { left: 0, top: 0, width: 100, height: 100 },
    assetUrl: playYarnUrl,
  }), true);
  assert.equal(producer.events.at(-1).wideArt, true);
  assert.equal(producer.events.at(-1).artLayout, '');

  assert.equal(producer.producer(null, producer.makeContainer(), true, {
    surfaceRect: { left: 0, top: 0, width: 100, height: 100 },
    assetUrl: '/cat-idle-cat1-mouse.webp',
    artLayout: 'wide-centered',
  }), true);
  assert.equal(producer.events.at(-1).wideArt, true);
  assert.equal(producer.events.at(-1).artLayout, 'wide-centered');

  assert.equal(producer.producer(null, producer.makeContainer(), true, {
    surfaceRect: { left: 0, top: 0, width: 100, height: 100 },
    assetUrl: '/cat-idle-cat1-lion_dance.webp',
    artLayout: 'tall-bottom',
  }), true);
  assert.equal(producer.events.at(-1).wideArt, false);
  assert.equal(producer.events.at(-1).artLayout, 'tall-bottom');

  assert.equal(producer.producer(null, producer.makeContainer(), true, {
    surfaceRect: { left: 0, top: 0, width: 100, height: 100 },
    assetUrl: idleUrl,
  }), true);
  assert.equal(producer.events.at(-1).wideArt, false);
  assert.equal(producer.events.at(-1).artLayout, '');
});


test('every registered action exposes the design dependency matrix', () => {
  const registry = loadRegistry();
  const expected = {
    cat1_social_ping: { appearance: [], voice: ['cat1.ambient'] },
    cat1_eat_snack: {
      appearance: ['action.cat1.eat', 'action.cat1.eat_2', 'action.cat1.eat_3'],
      voice: ['cat1.eat'],
    },
    cat1_small_move: { appearance: ['movement.cat1.walking'], voice: [] },
    cat1_play_yarn: { appearance: ['action.cat1.play_yarn'], voice: ['cat1.play_yarn'] },
    cat1_idle_random: {
      appearance: [
        'action.cat1.happy',
        'action.cat1.touch',
        'action.cat1.mouse',
        'action.cat1.laugh',
        'action.cat1.love_1',
        'action.cat1.wagging',
      ],
      voice: [],
    },
    cat1_inactive_sad: { appearance: ['action.cat1.sad'], voice: [] },
    cat2_nap_feedback: { appearance: ['idle.cat2'], voice: ['cat2.sleep'] },
    cat3_sleep_feedback: { appearance: ['idle.cat3'], voice: ['cat3.sleep'] },
    cat1_hiss_stretch: { appearance: ['movement.cat1.stretch'], voice: ['cat1.hiss'] },
  };
  for (const entry of VISUAL_ACTION_ASSETS) {
    expected[entry.actionId] = { appearance: [entry.slot], voice: [] };
  }

  for (const [actionId, dependencies] of Object.entries(expected)) {
    const capability = registry.getActionCapabilities(actionId);
    assert.equal(capability.available, true, actionId);
    assert.deepEqual(Array.from(capability.appearance, (entry) => entry.groupId), dependencies.appearance.map(() => 'dev_neko'), actionId);
    assert.deepEqual(Array.from(capability.voice, (entry) => entry.groupId), dependencies.voice.map(() => 'dev_neko'), actionId);
    assert.deepEqual(
      Array.from(capability.appearance, (entry) => Array.from(entry.urls)),
      dependencies.appearance.map((slot) => Array.from(registry.getAppearance(slot, { random: false }).urls)),
      actionId,
    );
    assert.deepEqual(
      Array.from(capability.voice, (entry) => Array.from(entry.urls)),
      dependencies.voice.map((slot) => Array.from(registry.getVoice(slot, { random: false }).urls)),
      actionId,
    );
  }
});

test('capability reads do not consume the random selection pool', () => {
  let randomCalls = 0;
  const registry = loadRegistry(undefined, {
    Math: {
      floor: Math.floor,
      min: Math.min,
      random() {
        randomCalls += 1;
        return 0;
      },
    },
  });

  registry.getActionCapabilities('cat1_play_yarn');
  assert.equal(randomCalls, 0);
  registry.getAppearance('drag.cat1');
  assert.equal(randomCalls, 1);
});

function registryWithEmptySlot(slot) {
  const source = fs.readFileSync(REGISTRY_PATH, 'utf8');
  const escapedSlot = slot.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&');
  const arrayPattern = new RegExp(`(\\s*'${escapedSlot}'\\s*:\\s*)\\[[^\\]]*\\]`);
  const objectUrlsPattern = new RegExp(`(\\s*'${escapedSlot}'\\s*:\\s*\\{\\s*urls:\\s*)\\[[^\\]]*\\]`);
  let replacements = 0;
  let updated = source.replace(arrayPattern, (_match, prefix) => {
    replacements += 1;
    return `${prefix}[]`;
  });
  if (replacements === 0) {
    updated = source.replace(objectUrlsPattern, (_match, prefix) => {
      replacements += 1;
      return `${prefix}[]`;
    });
  }
  assert.equal(replacements, 1, `registry slot ${slot} should be replaceable exactly once`);
  return loadRegistry(updated);
}

test('missing action resources are rejected with a stable dependency reason', () => {
  const cases = [
    ['cat1_social_ping', 'cat1.ambient', 'voice_unavailable'],
    ['cat1_eat_snack', 'action.cat1.eat', 'appearance_unavailable'],
    ['cat1_eat_snack', 'action.cat1.eat_2', 'appearance_unavailable'],
    ['cat1_eat_snack', 'action.cat1.eat_3', 'appearance_unavailable'],
    ['cat1_small_move', 'movement.cat1.walking', 'appearance_unavailable'],
    ['cat1_play_yarn', 'action.cat1.play_yarn', 'appearance_unavailable'],
    ['cat1_idle_random', 'action.cat1.happy', 'appearance_unavailable'],
    ['cat1_idle_random', 'action.cat1.touch', 'appearance_unavailable'],
    ['cat1_idle_random', 'action.cat1.mouse', 'appearance_unavailable'],
    ['cat1_idle_random', 'action.cat1.laugh', 'appearance_unavailable'],
    ['cat1_idle_random', 'action.cat1.love_1', 'appearance_unavailable'],
    ['cat1_idle_random', 'action.cat1.wagging', 'appearance_unavailable'],
    ['cat1_inactive_sad', 'action.cat1.sad', 'appearance_unavailable'],
    ['cat2_nap_feedback', 'idle.cat2', 'appearance_unavailable'],
    ['cat3_sleep_feedback', 'cat3.sleep', 'voice_unavailable'],
    ['cat1_hiss_stretch', 'cat1.hiss', 'voice_unavailable'],
    ...VISUAL_ACTION_ASSETS.map((entry) => [entry.actionId, entry.slot, 'appearance_unavailable']),
  ];

  for (const [actionId, missingSlot, reason] of cases) {
    const decision = registryWithEmptySlot(missingSlot).getActionCapabilities(actionId);
    assert.equal(decision.available, false, actionId);
    assert.equal(decision.reason, reason, actionId);
  }
});

test('unknown slots return an explicit unavailable result without throwing', () => {
  const registry = loadRegistry();
  for (const readSlot of [registry.getAppearance, registry.getVoice]) {
    const result = readSlot('missing.slot', { random: false });
    assert.equal(result.available, false);
    assert.equal(result.url, null);
    assert.deepEqual(Array.from(result.urls), []);
    assert.equal(result.groupId, 'dev_neko');
    assert.deepEqual({ ...result.metadata }, {});
  }
});

test('missing optional hover art preserves the cat and does not pause or cancel movement', () => {
  const registry = registryWithEmptySlot('click.cat1');
  assert.equal(registry.getActionCapabilities('cat1_social_ping').available, true);
  const source = fs.readFileSync(path.join(PROJECT_ROOT,
    'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js'), 'utf8');
  const start = source.indexOf('function _playNekoIdleHoverArt(');
  const end = source.indexOf('function _finishNekoIdleHoverArtAfterPlayback(', start);
  const profile = { walkingSubstate: 'walking', assets: { interactive: () => '' } };
  const state = { profile, substate: 'idle', targetKind: 'compact-top-edge' };
  const art = {
    src: 'idle.gif',
    getAttribute: (name) => name === 'src' ? art.src : '',
  };
  let effects = 0;
  const context = vm.createContext({
    _NEKO_IDLE_TIER_NONE: 'none',
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _getNekoIdleReturnButtonFromArt: () => ({ __nekoIdleCat1Journey: state }),
    _isNekoIdleReturnDragActionActive: () => false,
    _isNekoIdleCat1IndependentActionActive: () => false,
    _getNekoIdleReturnSubactionProfile: () => profile,
    _getNekoIdleReturnClickAssetUrl: () => registry.getAppearance('click.cat1').url || '',
    _cleanupNekoIdleArtTransition: () => {},
    _cancelNekoIdleCat1PairMove: () => { effects += 1; },
    _pauseNekoIdleCat1Journey: () => { effects += 1; },
    _clearNekoIdleHoverPlayback: () => {},
    _clearNekoIdleGifPlaybackSource: () => {},
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt: () => {},
  });
  vm.runInContext(source.slice(start, end), context);
  context._playNekoIdleHoverArt(art, 'cat1');
  state.substate = 'walking';
  context._playNekoIdleHoverArt(art, 'cat1');
  assert.equal(art.src, 'idle.gif');
  assert.equal(art.__nekoIdleHoverSrc, undefined);
  assert.equal(effects, 0);
});

test('return transition restarts canonical cat animations and preserves their version', () => {
  const source = fs.readFileSync(path.join(PROJECT_ROOT,
    'static/app/app-ui/return-transitions.js'), 'utf8');
  const start = source.indexOf('function buildNekoModelCatRevealPlaybackUrl(');
  const end = source.indexOf('I.restartNekoModelCatRevealArt =', start);
  assert.ok(start >= 0 && end > start);
  const context = vm.createContext({ URL, window: { location: { href: 'http://localhost/' } } });
  vm.runInContext(source.slice(start, end), context);
  const registry = loadRegistry();
  for (const slot of ['idle.cat1', 'idle.cat2', 'idle.cat3']) {
    const src = registry.getAppearance(slot, { random: false }).url;
    const result = new URL(context.buildNekoModelCatRevealPlaybackUrl(`${src}?v=123`, 42));
    assert.equal(result.pathname, src);
    assert.equal(result.searchParams.get('v'), '123');
    assert.equal(result.searchParams.get('reveal'), '42');
  }
  assert.equal(context.buildNekoModelCatRevealPlaybackUrl('/user_pngtuber/custom.gif', 42),
    '/user_pngtuber/custom.gif');
});

test('registry loads before core in every Avatar and chat template', () => {
  for (const relativePath of REGISTRY_TEMPLATES) {
    const source = fs.readFileSync(path.join(PROJECT_ROOT, relativePath), 'utf8');
    const registryIndex = source.indexOf('cat-resource-registry.js');
    assert.ok(registryIndex >= 0, `${relativePath} must load the resource registry`);
    const registryTag = source.slice(Math.max(0, registryIndex - 120), registryIndex + 180);
    assert.match(registryTag, /cat-resource-registry\.js\?v=\{\{\s*static_asset_version/,
      `${relativePath} must version the resource registry`);
    const coreIndex = source.indexOf('avatar-ui-buttons/core.js');
    if (relativePath === 'templates/chat.html') {
      assert.equal(coreIndex, -1, 'chat.html must remain a chat-only window');
    } else {
      assert.ok(coreIndex > registryIndex, `${relativePath} must load registry before core`);
    }
  }
});
