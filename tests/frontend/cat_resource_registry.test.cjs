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
const APPEARANCE_MANIFEST_PATH = path.join(
  PROJECT_ROOT,
  'static/assets/cat-resources/appearance/dev_neko/manifest.json',
);
const VOICE_MANIFEST_PATH = path.join(
  PROJECT_ROOT,
  'static/assets/cat-resources/voice/dev_neko/manifest.json',
);
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

function loadRegistry(source = fs.readFileSync(REGISTRY_PATH, 'utf8'), overrides = {}) {
  const window = {};
  vm.runInNewContext(source, { window, ...overrides }, {
    filename: REGISTRY_PATH,
  });
  return window.NekoCatResourceRegistry;
}

function assertManifestMatchesRegistry(manifest, readSlot) {
  assert.equal(manifest.group_id, 'dev_neko');
  for (const [slot, entry] of Object.entries(manifest.slots)) {
    const expectedUrls = entry.resources.map((resource) => resource.path);
    const actual = readSlot(slot, { random: false });
    assert.equal(actual.available, true, `${manifest.kind}:${slot} should be available`);
    assert.equal(actual.groupId, 'dev_neko', `${manifest.kind}:${slot} group`);
    assert.deepEqual(Array.from(actual.urls), expectedUrls, `${manifest.kind}:${slot} paths`);
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
  const registry = loadRegistry();
  const appearance = JSON.parse(fs.readFileSync(APPEARANCE_MANIFEST_PATH, 'utf8'));
  const voice = JSON.parse(fs.readFileSync(VOICE_MANIFEST_PATH, 'utf8'));

  assert.equal(registry.selectedAppearanceGroupId, 'dev_neko');
  assert.equal(registry.selectedVoiceGroupId, 'dev_neko');
  assertManifestMatchesRegistry(appearance, registry.getAppearance);
  assertManifestMatchesRegistry(voice, registry.getVoice);

  assert.equal(registry.getAppearance('action.cat1.play_yarn', { random: false }).metadata.wideArt, true);
  for (const actionId of [
    'cat1_social_ping',
    'cat1_eat_snack',
    'cat1_small_move',
    'cat1_play_yarn',
    'cat2_nap_feedback',
    'cat3_sleep_feedback',
    'cat1_hiss_stretch',
  ]) {
    assert.equal(registry.getActionCapabilities(actionId).available, true, actionId);
  }
  const unknownAction = registry.getActionCapabilities('missing-action');
  assert.equal(unknownAction.available, false);
  assert.equal(unknownAction.reason, 'unknown_action');
});


test('every registered action exposes the design dependency matrix', () => {
  const registry = loadRegistry();
  const expected = {
    cat1_social_ping: { appearance: [], voice: ['cat1.ambient'] },
    cat1_eat_snack: { appearance: ['action.cat1.eat'], voice: ['cat1.eat'] },
    cat1_small_move: { appearance: ['movement.cat1.walking'], voice: [] },
    cat1_play_yarn: { appearance: ['action.cat1.play_yarn'], voice: ['cat1.play_yarn'] },
    cat2_nap_feedback: { appearance: ['idle.cat2'], voice: ['cat2.sleep'] },
    cat3_sleep_feedback: { appearance: ['idle.cat3'], voice: ['cat3.sleep'] },
    cat1_hiss_stretch: { appearance: ['movement.cat1.stretch'], voice: ['cat1.hiss'] },
  };

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
    ['cat1_small_move', 'movement.cat1.walking', 'appearance_unavailable'],
    ['cat1_play_yarn', 'action.cat1.play_yarn', 'appearance_unavailable'],
    ['cat2_nap_feedback', 'idle.cat2', 'appearance_unavailable'],
    ['cat3_sleep_feedback', 'cat3.sleep', 'voice_unavailable'],
    ['cat1_hiss_stretch', 'cat1.hiss', 'voice_unavailable'],
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

test('return transition restarts canonical cat GIFs and preserves their version', () => {
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
