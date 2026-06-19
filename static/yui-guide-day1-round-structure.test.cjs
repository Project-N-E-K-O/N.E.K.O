const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const directorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
const day1Source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/days/day1-home-guide.js'), 'utf8');
const resetSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/avatar/floating-guide-reset.js'), 'utf8');
const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
const sceneOrchestratorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/scene-orchestrator.js'), 'utf8');
const operationRegistrySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/operation-registry.js'), 'utf8');
const targetGeometryRegistrySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/target-geometry-registry.js'), 'utf8');

function getSceneBlock(source, sceneId) {
  const idPattern = "id: '" + sceneId + "'";
  const idIndex = source.indexOf(idPattern);
  assert.notStrictEqual(idIndex, -1, 'expected to find scene ' + sceneId);
  const start = source.lastIndexOf('\n                {', idIndex);
  assert.notStrictEqual(start, -1, 'expected scene start for ' + sceneId);
  const end = source.indexOf('\n                }', idIndex);
  assert.notStrictEqual(end, -1, 'expected scene end for ' + sceneId);
  return source.slice(start, end + '\n                }'.length);
}

test('Day1 activation stays timeline-owned while greeting uses the generic scene path', () => {
  const operationRegistryBlock = operationRegistrySource.match(/registerBuiltInOperations\(\)\s*\{([\s\S]*?)\n\s*\}\n\s*\n\s*resolveTargetEntry/);
  assert.ok(operationRegistryBlock, 'expected to find built-in operation registrations');
  const activationSceneBlock = getSceneBlock(day1Source, 'day1_intro_activation');
  const greetingSceneBlock = getSceneBlock(day1Source, 'day1_intro_greeting');

  assert.match(activationSceneBlock, /timelinePlayback:\s*true/);
  assert.match(activationSceneBlock, /timelineAudio:\s*false/);
  assert.match(activationSceneBlock, /operation:\s*'day1-intro-activation-flow'/);
  assert.doesNotMatch(greetingSceneBlock, /timelinePlayback:\s*true/);
  assert.doesNotMatch(greetingSceneBlock, /timelineAudio:\s*false/);
  assert.doesNotMatch(greetingSceneBlock, /operation:\s*'day1-intro-greeting-flow'/);
  assert.match(greetingSceneBlock, /target:\s*'chat-input'/);
  assert.match(greetingSceneBlock, /cursorTarget:\s*'chat-capsule-input'/);
  assert.match(greetingSceneBlock, /cursorAction:\s*'move'/);
  assert.match(greetingSceneBlock, /operation:\s*'day1-intro-greeting-performance'/);
  assert.match(operationRegistryBlock[1], /registerOperation\('day1-intro-activation-flow'/);
  assert.match(operationRegistryBlock[1], /runDay1IntroActivationFlow/);
  assert.match(operationRegistryBlock[1], /registerOperation\('day1-intro-greeting-performance'/);
  assert.match(operationRegistryBlock[1], /runDay1IntroGreetingPerformance/);
  assert.doesNotMatch(sceneOrchestratorSource, /registerSceneFlow\('day1-intro-activation'/);
  assert.doesNotMatch(sceneOrchestratorSource, /registerSceneFlow\('day1-intro-greeting'/);
  assert.doesNotMatch(sceneOrchestratorSource, /playRegisteredSceneFlow/);
  assert.doesNotMatch(directorSource, /isDay1SpecialAvatarFloatingScene\(scene\)/);
  assert.doesNotMatch(directorSource, /playDay1AvatarFloatingScene\(scene/);

  const introActivationMatch = directorSource.match(/async playDay1IntroActivationRoundScene\(sceneRunId\)\s*\{([\s\S]*?)\n\s*\}\n\s*\n\s*async playDay1IntroGreetingRoundScene/);
  assert.ok(introActivationMatch, 'expected to find Day1 intro activation flow');
  assert.match(introActivationMatch[1], /waitForIntroActivationClick\(\)/);
  assert.doesNotMatch(introActivationMatch[1], /setTutorialTakingOver\(true\)/);

  for (const sceneId of [
    'day1_intro_activation',
    'day1_intro_greeting',
    'day1_intro_basic_voice',
    'day1_screen_entry',
    'day1_screen_entry_invite',
    'day1_takeover_capture_cursor'
  ]) {
    assert.match(day1Source, new RegExp("id:\\s*'" + sceneId + "'"));
  }
});

test('Day1 guide config excludes deprecated plugin, settings and cross-page handoff steps', () => {
  const deprecatedDay1Keys = [
    'takeover_plugin_preview',
    'takeover_plugin_preview_home',
    'takeover_plugin_preview_dashboard',
    'takeover_settings_peek',
    'takeover_settings_peek_intro',
    'takeover_settings_peek_detail',
    'handoff_api_key',
    'handoff_memory_browser',
    'handoff_plugin_dashboard',
    'plugin_dashboard_landing',
    'api_key_intro',
    'memory_browser_intro'
  ];

  assert.match(day1Source, /pageKeys:\s*\[\s*'home'\s*\]/);
  assert.doesNotMatch(day1Source, /api_key:\s*\[/);
  assert.doesNotMatch(day1Source, /memory_browser:\s*\[/);
  assert.doesNotMatch(day1Source, /plugin_dashboard:\s*\[/);

  for (const key of deprecatedDay1Keys) {
    assert.doesNotMatch(day1Source, new RegExp(key));
  }
});

test('Avatar floating interrupt step preserves scene target data for restore', () => {
  const interruptStepMatch = directorSource.match(/getAvatarFloatingInterruptStep\(scene\)\s*\{([\s\S]*?)\n\s*\}\n\s*\n\s*getAvatarFloatingBaseTarget/);
  assert.ok(interruptStepMatch, 'expected to find getAvatarFloatingInterruptStep');
  const interruptStepBody = interruptStepMatch[1];

  assert.match(interruptStepBody, /anchor:\s*normalizedScene\.target/);
  assert.match(interruptStepBody, /cursorTarget:\s*normalizedScene\.cursorTarget\s*\|\|\s*normalizedScene\.target/);
  assert.match(interruptStepBody, /cursorAction:\s*normalizedScene\.cursorAction/);
  assert.match(interruptStepBody, /emotion:\s*normalizedScene\.emotion/);
  assert.match(interruptStepBody, /interruptible:\s*normalizedScene\.interruptible\s*!==\s*false/);
});

test('Day1 takeover capture operation is registered in the operation registry', () => {
  const operationFunctionMatch = operationRegistrySource.match(/async runDay1TakeoverCaptureCursor\(scene\)\s*\{([\s\S]*?)\n\s*\}\n\s*async runDay6PluginOpenAgentPanelFlow/);
  assert.ok(operationFunctionMatch, 'expected to find runDay1TakeoverCaptureCursor');
  const operationBody = operationFunctionMatch[1];

  assert.match(operationRegistrySource, /registerOperation\('day1-managed-scene:takeover_capture_cursor'/);
  assert.match(operationRegistrySource, /this\.runDay1TakeoverCaptureCursor\(context\.scene\)/);
  assert.match(operationBody, /runTakeoverKeyboardControlSequence/);
});

test('Day1 intro basic voice showcase is delegated from timeline operation', () => {
  const operationFunctionMatch = operationRegistrySource.match(/async runDay1IntroBasicVoiceShowcase\(scene,\s*narrationStartedAt,\s*narrationPromise\)\s*\{([\s\S]*?)\n\s*\}\n\s*async runDay1TakeoverCaptureCursor/);
  assert.ok(operationFunctionMatch, 'expected to find runDay1IntroBasicVoiceShowcase');
  const operationBody = operationFunctionMatch[1];
  const sceneBlock = getSceneBlock(day1Source, 'day1_intro_basic_voice');

  assert.match(sceneBlock, /timelinePlayback:\s*true/);
  assert.match(sceneBlock, /operation:\s*'day1-intro-basic-voice-showcase'/);
  assert.match(sceneBlock, /at:\s*1,\s*command:\s*'operation\.run'/);
  assert.match(operationRegistrySource, /registerOperation\('day1-intro-basic-voice-showcase'/);
  assert.match(operationBody, /ensurePreTakeoverGhostCursorLookAtPerformance/);
  assert.match(operationBody, /narrationDurationMs \* 0\.16/);
  assert.match(operationBody, /runIntroVoiceControlButtonShowcase/);
  assert.match(operationBody, /Promise\.resolve\(narrationPromise\)/);
  assert.match(operationBody, /adoptPreTakeoverGhostCursorLookAtHandle/);
});

test('Day1 button handoff scenes keep the shared tutorial interrupt resistance path', () => {
  for (const sceneId of [
    'day1_intro_basic_voice',
    'day1_screen_entry',
    'day1_screen_entry_invite',
    'day1_takeover_capture_cursor'
  ]) {
    const sceneBlock = getSceneBlock(day1Source, sceneId);
    assert.doesNotMatch(sceneBlock, /interruptible:\s*false/);
  }
});

test('Day1 takeover capture click is owned by the embedded takeover sequence', () => {
  const sceneBlock = getSceneBlock(day1Source, 'day1_takeover_capture_cursor');
  assert.match(sceneBlock, /cursorAction:\s*'move'/);
  assert.doesNotMatch(sceneBlock, /cursorAction:\s*'click'/);
});

test('Day1 takeover capture leaves the next cursor start on the keyboard control toggle', () => {
  const sequenceMatch = directorSource.match(/async runTakeoverKeyboardControlSequence\(step, performance, runId\)\s*\{([\s\S]*?)\n\s*\}\n\s*async runPluginDashboardLaunchSequence/);
  assert.ok(sequenceMatch, 'expected to find runTakeoverKeyboardControlSequence');
  const sequenceBody = sequenceMatch[1];
  assert.match(sequenceBody, /const keyboardToggleSpotlight = createToggleSpotlightTarget\('takeover-keyboard-toggle', keyboardToggle\);/);
  assert.match(sequenceBody, /this\.rememberAvatarFloatingSceneCursorAnchor\('day1_takeover_capture_cursor', keyboardToggleSpotlight\);/);
});

test('Day1 return control highlights the capsule input and keeps the petal cue', () => {
  const day1SceneBlock = getSceneBlock(day1Source, 'day1_takeover_return_control');
  assert.match(day1SceneBlock, /target:\s*'chat-input'/);
  assert.match(day1SceneBlock, /cursorTarget:\s*'chat-capsule-input'/);
  assert.match(day1SceneBlock, /spotlightVariant:\s*'plain-capsule'/);
  assert.match(day1SceneBlock, /cursorMoveDurationMs:\s*900/);
  assert.match(day1SceneBlock, /operation:\s*'cleanup'/);
  assert.doesNotMatch(day1SceneBlock, /day1-managed-scene:takeover_return_control/);
  assert.doesNotMatch(day1SceneBlock, /target:\s*'#\$\{p\}-container'/);
  assert.match(day1SceneBlock, /petalTransition:\s*true/);
});

test('avatar floating reset script no longer ships the deprecated reset player', () => {
  assert.doesNotMatch(resetSource, /const DAY_TUTORIALS\s*=/);
  assert.doesNotMatch(resetSource, /function createRoundPlayer/);
  assert.doesNotMatch(resetSource, /resetHomeTutorialFallback/);
  assert.doesNotMatch(resetSource, /home-avatar-floating-guide-player/);
});

test('memory reset only prepares the formal avatar floating round for the next Neko refresh', () => {
  const resetHomeBlock = resetSource.split('async function resetHomeTutorialDay(day, options = {}) {')[1].split(
    '\n    async function startAvatarFloatingGuideDay',
    1
  )[0];
  const startDayBlock = resetSource.split('async function startAvatarFloatingGuideDay(day, options = {}) {')[1].split(
    '\n    function showResetToast',
    1
  )[0];

  assert.match(resetHomeBlock, /clearHomeTutorialPromptResetState\(round\);/);
  assert.doesNotMatch(resetHomeBlock, /startFormalAvatarFloatingGuideRound/);
  assert.doesNotMatch(resetHomeBlock, /createRoundPlayer/);
  assert.match(startDayBlock, /return startFormalAvatarFloatingGuideRound\(day,\s*\{\s*source:\s*options\.source \|\| 'home_reset_button'/);
  assert.doesNotMatch(startDayBlock, /createRoundPlayer/);
});

test('avatar floating reset toasts resolve through i18n keys', () => {
  assert.match(resetSource, /function translateResetMessage\(key,\s*fallback,\s*options = \{\}\)/);
  assert.match(resetSource, /window\.t\(key,\s*options\)/);
  assert.match(resetSource, /'tutorial\.reset\.daySuccess'/);
  assert.match(resetSource, /'tutorial\.reset\.dayFailed'/);
});

test('Day1 return control cursor moves to the capsule primary target before the operation runs', () => {
  assert.match(sceneOrchestratorSource, /await director\.moveAvatarFloatingCursor\(scene,\s*cursorTarget \|\| primaryTarget,\s*secondaryTarget,\s*previousSceneId/);
  assert.match(sceneOrchestratorSource, /externalizedSceneTargetKind && scene\.cursorAction === 'move'[\s\S]*await director\.waitForExternalizedChatCursorMove/);
  assert.match(directorSource, /if \(sceneId === 'day1_takeover_return_control'\) \{[\s\S]*this\.getAvatarFloatingSceneCursorAnchor\('day1_takeover_capture_cursor'\)/);
  assert.match(directorSource, /if \(selector === 'chat-capsule-input'\) \{\s*return this\.getChatCapsuleInputTarget\(\);/);
  assert.match(directorSource, /const registeredKind = this\.cursor\.getExternalKind\(this\.getAvatarFloatingCursorTargetKey\(scene\)\);[\s\S]*if \(registeredKind\) \{[\s\S]*return registeredKind;/);
  assert.match(directorSource, /'chat-capsule-input': 'capsule-input'/);
  assert.match(targetGeometryRegistrySource, /'chat-capsule-input': Object\.freeze\(\{[\s\S]*externalKind: 'capsule-input'[\s\S]*data-compact-geometry-part="capsuleBody"/);
  assert.match(appInterpageSource, /getYuiGuideChatTargetRegistryEntryByExternalKind\(kind\)[\s\S]*entry\.localSelectors\.some/);
  assert.match(appInterpageSource, /function updateYuiGuideChatSpotlight\(kind\) \{[\s\S]*var pcOverlayAvailable = isYuiGuidePcOverlayAvailable\(\);/);
  assert.match(appInterpageSource, /function updateYuiGuideChatSpotlight\(kind\) \{[\s\S]*sendYuiGuidePcOverlayPatch\(\{ spotlights: pcRects \}\);/);
  assert.doesNotMatch(appInterpageSource, /function renderYuiGuideChatSpotlight/);
  assert.doesNotMatch(appInterpageSource, /function isYuiGuideInputLikeChatTarget/);
  assert.match(directorSource, /setExternalizedChatCursorEffect\(kind,\s*effect,\s*options\)[\s\S]*this\.rememberExternalizedChatCursorHandoffPoint\(normalizedKind,\s*cursorOptions\.effect\);[\s\S]*this\.interactionTakeover\.setExternalizedChatCursor\(normalizedKind,\s*cursorOptions\);/);
  const moveIndex = sceneOrchestratorSource.indexOf('await director.moveAvatarFloatingCursor(scene, cursorTarget || primaryTarget');
  const operationIndex = sceneOrchestratorSource.indexOf('await startSceneOperation();', moveIndex);
  assert.notStrictEqual(moveIndex, -1, 'expected generic avatar floating cursor move');
  assert.notStrictEqual(operationIndex, -1, 'expected scene operation after cursor move');
  assert.ok(moveIndex < operationIndex, 'cursor should move to the capsule input before the scene operation starts');
});

test('Director passes avatar floating scene spotlight variants to the target element', () => {
  assert.match(sceneOrchestratorSource, /director\.applyAvatarFloatingSceneSpotlightVariant\(scene,\s*primaryTarget\)/);
  const variantFunctionMatch = directorSource.match(/applyAvatarFloatingSceneSpotlightVariant\(scene,\s*target\)\s*\{([\s\S]*?)\n\s*\}\n\s*\n\s*async prepareAvatarFloatingScene/);
  assert.ok(variantFunctionMatch, 'expected to find applyAvatarFloatingSceneSpotlightVariant');
  const variantFunctionBody = variantFunctionMatch[1];
  assert.match(variantFunctionBody, /scene\.spotlightVariant/);
  assert.match(variantFunctionBody, /setSpotlightVariantHints/);
});
