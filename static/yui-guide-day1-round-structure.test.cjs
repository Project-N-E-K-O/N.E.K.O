const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { readDirectorSource } = require('./yui-guide-director-test-parts.cjs');
const test = require('node:test');
const { readJsParts } = require('./app-part-test-utils.cjs');

const repoRoot = path.resolve(__dirname, '..');
const directorSource = readDirectorSource(path.join(repoRoot, 'static'));
const day1Source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/days/day1-home-guide.js'), 'utf8');
const resetSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/avatar/floating-guide-reset.js'), 'utf8');
const appInterpageSource = readJsParts(path.join(repoRoot, 'static', 'app/app-interpage'));
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

function getBalancedBlockFrom(source, startIndex) {
  const openBraceIndex = source.indexOf('{', startIndex);
  assert.notStrictEqual(openBraceIndex, -1, 'expected block opening brace');
  let depth = 0;
  for (let index = openBraceIndex; index < source.length; index += 1) {
    const character = source[index];
    if (character === '{') {
      depth += 1;
    } else if (character === '}') {
      depth -= 1;
      if (depth === 0) {
        return source.slice(startIndex, index + 1);
      }
    }
  }
  assert.fail('expected balanced block closing brace');
}

test('Day1 activation and greeting keep timeline-owned capsule targets', () => {
  const operationRegistryBlock = operationRegistrySource.match(/registerBuiltInOperations\(\)\s*\{([\s\S]*?)\n\s*\}\n\s*\n\s*resolveTargetEntry/);
  assert.ok(operationRegistryBlock, 'expected to find built-in operation registrations');
  const activationSceneBlock = getSceneBlock(day1Source, 'day1_intro_activation');
  const greetingSceneBlock = getSceneBlock(day1Source, 'day1_intro_greeting');

  assert.match(activationSceneBlock, /timelinePlayback:\s*true/);
  assert.match(activationSceneBlock, /timelineAudio:\s*false/);
  assert.match(activationSceneBlock, /operation:\s*'day1-intro-activation-flow'/);
  assert.match(greetingSceneBlock, /timelinePlayback:\s*true/);
  assert.doesNotMatch(greetingSceneBlock, /timelineAudio:\s*false/);
  assert.doesNotMatch(greetingSceneBlock, /operation:\s*'day1-intro-greeting-flow'/);
  // 修改原因：显式 timeline 的 spotlight.show 不会自动读取 cursorTarget，Day1 胶囊高亮必须直接写胶囊目标。
  assert.match(greetingSceneBlock, /\{\s*at:\s*0,\s*command:\s*'spotlight\.show',\s*key:\s*'day1_intro_greeting',\s*target:\s*'chat-capsule-input'\s*\}/);
  // 修改原因：scene target 也要与 timeline 保持一致，避免恢复或兼容路径重新读到普通输入框。
  assert.match(greetingSceneBlock, /\n\s+target:\s*'chat-capsule-input',/);
  assert.match(greetingSceneBlock, /cursorTarget:\s*'chat-capsule-input'/);
  assert.match(greetingSceneBlock, /cursorAction:\s*'move'/);
  assert.match(greetingSceneBlock, /operation:\s*'day1-intro-greeting-performance'/);
  assert.doesNotMatch(greetingSceneBlock, /spotlightVariant:\s*'plain-capsule'/);
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
  assert.match(introActivationMatch[1], /waitForIntroActivationTransition\(\)/);
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

test('Day1 takeover capture resamples the keyboard control target before click and anchor persistence', () => {
  const sequenceMatch = directorSource.match(/async runTakeoverKeyboardControlSequence\(step, performance, runId\)\s*\{([\s\S]*?)\n\s*\}\n\s*async runPluginDashboardLaunchSequence/);
  assert.ok(sequenceMatch, 'expected to find runTakeoverKeyboardControlSequence');
  const sequenceBody = sequenceMatch[1];
  assert.match(sequenceBody, /const refreshKeyboardToggleSpotlight = \(options\) => \{/);
  assert.match(sequenceBody, /createToggleSpotlightTarget\('takeover-keyboard-toggle', keyboardToggle\)/);
  assert.match(sequenceBody, /this\.replaceRetainedExtraSpotlight\(isKeyboardToggleSpotlight, refreshedSpotlight\);/);
  assert.match(sequenceBody, /if \(normalizedOptions\.activate === true\) \{\s*this\.overlay\.activateSpotlight\(refreshedSpotlight\);/);
  assert.match(sequenceBody, /keyboardToggleSpotlight = refreshKeyboardToggleSpotlight\(\{ activate: true \}\);/);
  assert.match(sequenceBody, /await this\.waitForStableElementRect\(keyboardToggle,/);
  assert.match(sequenceBody, /this\.moveCursorToTrackedElement\(\s*keyboardToggle,/);
  assert.match(sequenceBody, /if \(!this\.isCursorAlignedWithElement\(keyboardToggle, 5\)\) \{/);
  assert.match(sequenceBody, /keyboardToggleSpotlight = refreshKeyboardToggleSpotlight\(\);[\s\S]*const enabledKeyboardControl = await this\.runActionWithCursorClick/);
  assert.match(sequenceBody, /this\.runActionWithCursorClick\([\s\S]*this\.setAgentFlagEnabled\('computer_use_enabled', true\)/);
  assert.match(sequenceBody, /this\.rememberAvatarFloatingSceneCursorAnchor\('day1_takeover_capture_cursor', keyboardToggleSpotlight\);/);
});

test('Day1 return control cursor start prefers the current keyboard toggle geometry', () => {
  const resolverMatch = directorSource.match(/resolveAvatarFloatingCursorStartPoint\(scene, targets, previousSceneId\)\s*\{([\s\S]*?)\n\s*\}\n\s*async moveAvatarFloatingCursor/);
  assert.ok(resolverMatch, 'expected to find resolveAvatarFloatingCursorStartPoint');
  const resolverBody = resolverMatch[1];
  const returnControlIndex = resolverBody.indexOf("if (sceneId === 'day1_takeover_return_control') {");
  assert.notStrictEqual(returnControlIndex, -1, 'expected day1_takeover_return_control cursor start branch');
  const returnControlBlock = getBalancedBlockFrom(resolverBody, returnControlIndex);
  assert.match(returnControlBlock, /const keyboardToggle = this\.getAgentToggleElement\('agent-keyboard'\);/);
  assert.match(returnControlBlock, /const keyboardRect = this\.getElementRect\(keyboardToggle\);/);
  assert.ok(
    returnControlBlock.indexOf('keyboardRect') < returnControlBlock.indexOf("getAvatarFloatingSceneCursorAnchor('day1_takeover_capture_cursor')"),
    'expected current keyboard toggle geometry to be sampled before falling back to the saved anchor'
  );
});

test('Day1 return control highlights the capsule input and keeps the petal cue', () => {
  const day1SceneBlock = getSceneBlock(day1Source, 'day1_takeover_return_control');
  // 修改原因：返还控制权时视觉焦点也应落到胶囊输入框，而不是只让光标移动到胶囊。
  assert.match(day1SceneBlock, /target:\s*'chat-capsule-input'/);
  assert.match(day1SceneBlock, /cursorTarget:\s*'chat-capsule-input'/);
  // 修改原因：Day1 复用 Day2/Day4 的胶囊目标定位，不再依赖普通输入框的 plain-capsule 特例。
  assert.doesNotMatch(day1SceneBlock, /spotlightVariant:\s*'plain-capsule'/);
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

  assert.match(resetHomeBlock, /await STATE_API\.flush\(\);/);
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
  // 修改原因：胶囊目标已有 registry 几何；这里保留普通 input 的旧 plain-capsule 逻辑，避免新增胶囊特例。
  assert.match(appInterpageSource, /function shouldAlignYuiGuideChatSpotlightToCapsuleText\(kind, variant\) \{\s*return kind === 'input' && variant === 'plain-capsule';\s*\}/);
  assert.match(appInterpageSource, /function getYuiGuideChatSpotlightSourceRect\(kind, variant, rect\)[\s\S]*anchorOffsetX \* YUI_GUIDE_CHAT_CAPSULE_TEXT_ALIGNMENT_RATIO[\s\S]*return \{ rect: sourceRect \};/);
  assert.match(appInterpageSource, /function updateYuiGuideChatSpotlight\(kind,\s*pcOverlayRunId\) \{[\s\S]*var pcOverlayAvailable = isYuiGuidePcOverlayAvailable\(\);/);
  assert.match(appInterpageSource, /function updateYuiGuideChatSpotlight\(kind,\s*pcOverlayRunId\) \{[\s\S]*var sourceRectInfo = rect \? getYuiGuideChatSpotlightSourceRect\(kind, yuiGuideChatSpotlightVariant, rect\) : null;[\s\S]*sendYuiGuidePcOverlayPatch\(\{ spotlights: pcRects \}, false, patchOptions\);/);
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
