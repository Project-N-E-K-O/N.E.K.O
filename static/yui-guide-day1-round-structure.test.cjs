const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const directorSource = fs.readFileSync(path.join(repoRoot, 'static', 'yui-guide-director.js'), 'utf8');
const day1Source = fs.readFileSync(path.join(repoRoot, 'static', 'yui-guide-day1-home-guide.js'), 'utf8');
const resetSource = fs.readFileSync(path.join(repoRoot, 'static', 'avatar-floating-guide-reset.js'), 'utf8');
const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');

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

test('Day1 button narration scenes stay on the generic avatar floating scene path', () => {
  const specialFunctionMatch = directorSource.match(/isDay1SpecialAvatarFloatingScene\(scene\)\s*\{([\s\S]*?)\n\s*\}\n\s*\n\s*async playDay1IntroActivationRoundScene/);
  assert.ok(specialFunctionMatch, 'expected to find isDay1SpecialAvatarFloatingScene');
  const specialFunctionBody = specialFunctionMatch[1];

  assert.doesNotMatch(specialFunctionBody, /day1_intro_basic_voice/);
  assert.doesNotMatch(specialFunctionBody, /day1-managed-scene:/);
  assert.doesNotMatch(specialFunctionBody, /day1-managed-scene-settled:/);

  for (const sceneId of [
    'day1_intro_basic_voice',
    'day1_screen_entry',
    'day1_screen_entry_invite',
    'day1_takeover_capture_cursor'
  ]) {
    assert.match(day1Source, new RegExp("id:\\s*'" + sceneId + "'"));
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

test('Day1 takeover capture operation is embedded in generic scene operations', () => {
  const operationFunctionMatch = directorSource.match(/async runAvatarFloatingSceneOperation\(scene, primaryTarget, narrationStartedAt\)\s*\{([\s\S]*?)\n\s*\}\n\s*async playAvatarFloatingRound/);
  assert.ok(operationFunctionMatch, 'expected to find runAvatarFloatingSceneOperation');
  const operationBody = operationFunctionMatch[1];

  assert.match(operationBody, /day1-managed-scene:takeover_capture_cursor/);
  assert.match(operationBody, /runTakeoverKeyboardControlSequence/);
});

test('Day1 button handoff scenes opt out of pointer interrupt resistance', () => {
  for (const sceneId of [
    'day1_intro_basic_voice',
    'day1_screen_entry',
    'day1_screen_entry_invite',
    'day1_takeover_capture_cursor'
  ]) {
    const sceneBlock = getSceneBlock(day1Source, sceneId);
    assert.match(sceneBlock, /interruptible:\s*false/);
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

  const resetSceneBlock = getSceneBlock(resetSource, 'day1_takeover_return_control');
  assert.match(resetSceneBlock, /selector:\s*'[^']*data-compact-geometry-owner="surface"[^']*data-compact-geometry-item="input"/);
  assert.match(resetSceneBlock, /cursorMoveDurationMs:\s*900/);
  assert.match(resetSceneBlock, /operation:\s*'cleanup'/);
  assert.doesNotMatch(resetSceneBlock, /selector:\s*'#home-avatar-floating-guide-player'/);
});

test('Day1 return control cursor moves to the capsule primary target before the operation runs', () => {
  assert.match(directorSource, /await this\.moveAvatarFloatingCursor\(scene,\s*cursorTarget \|\| primaryTarget,\s*secondaryTarget,\s*previousSceneId/);
  assert.match(directorSource, /externalizedSceneTargetKind && scene\.cursorAction === 'move'[\s\S]*await this\.waitForExternalizedChatCursorMove/);
  assert.match(directorSource, /if \(sceneId === 'day1_takeover_return_control'\) \{[\s\S]*this\.getAvatarFloatingSceneCursorAnchor\('day1_takeover_capture_cursor'\)/);
  assert.match(directorSource, /if \(selector === 'chat-capsule-input'\) \{\s*return this\.getChatCapsuleInputTarget\(\);/);
  assert.match(directorSource, /if \(targetKey === 'chat-capsule-input'\) \{\s*return 'capsule-input';/);
  assert.match(appInterpageSource, /if \(kind === 'capsule-input'\) \{[\s\S]*data-compact-geometry-part="capsuleBody"/);
  assert.match(appInterpageSource, /if \(kind === 'input' \|\| kind === 'capsule-input'\) \{\s*return false;/);
  assert.match(directorSource, /setExternalizedChatCursorEffect\(kind,\s*effect,\s*options\)[\s\S]*this\.rememberExternalizedChatCursorHandoffPoint\(normalizedKind,\s*cursorOptions\.effect\);[\s\S]*this\.interactionTakeover\.setExternalizedChatCursor\(normalizedKind,\s*cursorOptions\);/);
  assert.doesNotMatch(appInterpageSource, /payload\.cursor\s*=\s*yuiGuidePcOverlayCursor/);
  const moveIndex = directorSource.indexOf('await this.moveAvatarFloatingCursor(scene, cursorTarget || primaryTarget');
  const operationIndex = directorSource.indexOf('await startSceneOperation();', moveIndex);
  assert.notStrictEqual(moveIndex, -1, 'expected generic avatar floating cursor move');
  assert.notStrictEqual(operationIndex, -1, 'expected scene operation after cursor move');
  assert.ok(moveIndex < operationIndex, 'cursor should move to the capsule input before the scene operation starts');
});

test('Director passes avatar floating scene spotlight variants to the target element', () => {
  assert.match(directorSource, /applyAvatarFloatingSceneSpotlightVariant\(scene,\s*primaryTarget\)/);
  const variantFunctionMatch = directorSource.match(/applyAvatarFloatingSceneSpotlightVariant\(scene,\s*target\)\s*\{([\s\S]*?)\n\s*\}\n\s*\n\s*async prepareAvatarFloatingScene/);
  assert.ok(variantFunctionMatch, 'expected to find applyAvatarFloatingSceneSpotlightVariant');
  const variantFunctionBody = variantFunctionMatch[1];
  assert.match(variantFunctionBody, /scene\.spotlightVariant/);
  assert.match(variantFunctionBody, /setSpotlightVariantHints/);
});
