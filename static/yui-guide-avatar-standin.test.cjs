const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const standIn = require('./tutorial/avatar/yui-standin.js');
const repoRoot = path.resolve(__dirname, '..');
const directorSource = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/director.js'), 'utf8');
const visualControllerSource = fs.readFileSync(path.join(__dirname, 'tutorial/visual/controllers.js'), 'utf8');
const avatarStandInControllerSource = fs.readFileSync(
    path.join(__dirname, 'tutorial/avatar/standin-controller.js'),
    'utf8'
);
const petalTransitionControllerSource = fs.readFileSync(
    path.join(__dirname, 'tutorial/visual/petal-transition-controller.js'),
    'utf8'
);

test('tutorial pages load the canonical stand-in cue table before the stand-in controller', () => {
    for (const templatePath of [
        'templates/index.html',
        'templates/api_key_settings.html',
        'templates/memory_browser.html'
    ]) {
        const source = fs.readFileSync(path.join(repoRoot, templatePath), 'utf8');
        const cueTableIndex = source.indexOf('/static/tutorial/avatar/yui-standin.js');
        const controllerIndex = source.indexOf('/static/tutorial/avatar/standin-controller.js');

        assert.notEqual(cueTableIndex, -1, templatePath + ' should load tutorial/avatar/yui-standin.js');
        assert.notEqual(controllerIndex, -1, templatePath + ' should load tutorial/avatar/standin-controller.js');
        assert.ok(
            cueTableIndex < controllerIndex,
            templatePath + ' should load the stand-in cue table before the stand-in controller'
        );
        assert.equal(
            source.includes('/static/tutorial/yui-guide/avatar-standin.js'),
            false,
            templatePath + ' should not load the stale yui-guide/avatar-standin.js cue table'
        );
    }
});

test('returns fixed stand-in cues for the selected tutorial scenes', () => {
    assert.deepEqual(standIn.getCue(2, 'day2_intro_context'), {
        delayMs: 900,
        durationMs: 5000,
        resource: 'peek-head',
        position: 'bottom-right'
    });
    assert.deepEqual(standIn.getCue(3, 'day3_avatar_tools'), {
        delayMs: 900,
        durationMs: 5000,
        resource: 'peek-left-border',
        position: 'top-left-border'
    });
    assert.deepEqual(standIn.getCue(6, 'day6_wrap_cleanup'), {
        delayMs: 900,
        durationMs: 5000,
        resource: 'peek-head',
        position: 'bottom-right'
    });
});

test('does not return cues for final petal scenes or unselected scenes', () => {
    assert.equal(standIn.getCue(2, 'day2_wrap'), null);
    assert.equal(standIn.getCue(3, 'day3_wrap_ready'), null);
    assert.equal(standIn.getCue(7, 'day7_graduation_wrap'), null);
    assert.equal(standIn.getCue(4, 'day4_intro_companion'), null);
});

test('exports all fixed day two through seven cues with legal assets and positions', () => {
    const cues = standIn.getAllCues();
    const allowedResources = new Set(['peek-left-border', 'peek-right-border', 'peek-head', 'day5-character-settings']);
    const allowedPositions = new Set(['top-left-border', 'top-right-border', 'bottom-right', 'top-left-flipped', 'middle-left']);

    assert.equal(Object.keys(cues).length, 6);
    for (const day of [2, 3, 4, 5, 6, 7]) {
        const dayCues = Object.values(cues[String(day)] || {});
        assert.equal(dayCues.length, 2);
        for (const cue of dayCues) {
            assert.ok(cue.delayMs === 900 || cue.delayMs === 2900);
            assert.equal(cue.durationMs, 5000);
            assert.equal(allowedResources.has(cue.resource), true);
            assert.equal(allowedPositions.has(cue.position), true);
        }
    }
});

test('day five first stand-in cue uses the supplied replacement artwork only', () => {
    assert.deepEqual(standIn.getCue(5, 'day5_character_settings'), {
        delayMs: 2900,
        durationMs: 5000,
        resource: 'day5-character-settings',
        position: 'middle-left'
    });
    assert.equal(
        standIn.getResourcePath('day5-character-settings'),
        '/static/assets/tutorial/avatar-standins/day5-character-settings.png'
    );
    assert.equal(standIn.getCue(2, 'day2_proactive_chat').resource, 'peek-right-border');
    assert.equal(standIn.getCue(6, 'day6_plugin_dashboard').resource, 'peek-right-border');
});

test('director schedules day five first stand-in two seconds later than default cues', () => {
    assert.match(avatarStandInControllerSource, /director\.avatarStandInShowTimer = root\.setTimeout\(\(\) => \{[\s\S]*\}, Math\.max\(0, Number\(cue\.delayMs\) \|\| 0\)\);/);
    assert.equal(standIn.getCue(5, 'day5_character_settings').delayMs - standIn.DELAY_MS, 2000);
});

test('director fades the model before showing avatar stand-in overlay', () => {
    assert.match(directorSource, /const AVATAR_STAND_IN_MODEL_FADE_MS\s*=\s*1000;/);
    assert.match(directorSource, /this\.avatarStandInFadeTimer = null;/);
    assert.match(directorSource, /element\.style\.setProperty\('transition', 'opacity ' \+ AVATAR_STAND_IN_MODEL_FADE_MS \+ 'ms ease', 'important'\);/);
    assert.match(directorSource, /element\.style\.setProperty\('transition', 'opacity ' \+ AVATAR_STAND_IN_MODEL_FADE_MS \+ 'ms ease', 'important'\);[\s\S]*void element\.offsetWidth;[\s\S]*element\.style\.setProperty\('opacity', '0', 'important'\);/);
    assert.match(directorSource, /this\.prepareAvatarStandInOpacityTargets\(\);[\s\S]*this\.avatarStandInFadeTimer = window\.setTimeout\(\(\) => \{[\s\S]*this\.overlay\.showAvatarStandIn/);
    assert.match(avatarStandInControllerSource, /root\.clearTimeout\(director\.avatarStandInFadeTimer\);[\s\S]*director\.avatarStandInFadeTimer = null;/);
});

test('director routes avatar stand-in and petal transitions through phase three controllers', () => {
    const constructorBlock = directorSource.split('    class YuiGuideDirector {')[1].split(
        '            this.latestExternalizedChatCursorMoveSceneId =',
        1
    )[0];
    const standInEntryBlock = directorSource.split('        scheduleAvatarStandInForScene(scene, day, sceneRunId) {')[1].split(
        '        prepareAvatarStandInOpacityTargets',
        1
    )[0];
    const standInClearBlock = directorSource.split('        clearAvatarStandIn(options) {')[1].split(
        '        setGuideChatInputLocked',
        1
    )[0];
    const petalEntryBlock = directorSource.split('        async playReturnPetalTransition(options) {')[1].split(
        '        resolveGuideCopy(textKey, fallbackText) {',
        1
    )[0];
    const petalCueEntryBlock = directorSource.split('        async playAvatarFloatingPetalTransitionAtCue(scene, sceneRunId, voiceKey, text, narrationStartedAt) {')[1].split(
        '        rememberAvatarFloatingSceneCursorAnchor(sceneId, element) {',
        1
    )[0];

    assert.match(avatarStandInControllerSource, /class AvatarStandInController/);
    assert.match(visualControllerSource, /avatarStandInControllerApi\.AvatarStandInController/);
    assert.doesNotMatch(visualControllerSource, /class AvatarStandInController/);
    assert.match(petalTransitionControllerSource, /class PetalTransitionController/);
    assert.match(visualControllerSource, /petalTransitionControllerApi\.PetalTransitionController/);
    assert.doesNotMatch(visualControllerSource, /class PetalTransitionController/);
    assert.match(constructorBlock, /this\.avatarStandInController = new TutorialVisualControllers\.AvatarStandInController\(this\);/);
    assert.match(constructorBlock, /this\.petalTransitionController = new TutorialVisualControllers\.PetalTransitionController\(this\);/);
    assert.match(standInEntryBlock, /return this\.avatarStandInController\.schedule\(scene,\s*day,\s*sceneRunId\);/);
    assert.match(standInClearBlock, /return this\.avatarStandInController\.clear\(options\);/);
    assert.doesNotMatch(directorSource, /getAvatarStandInCueLegacy/);
    assert.doesNotMatch(directorSource, /scheduleAvatarStandInForSceneLegacy/);
    assert.doesNotMatch(directorSource, /clearAvatarStandInLegacy/);
    assert.match(petalEntryBlock, /return this\.petalTransitionController\.playReturn\(options\);/);
    assert.match(petalCueEntryBlock, /return this\.petalTransitionController\.playAtCue\(scene,\s*sceneRunId,\s*voiceKey,\s*text,\s*narrationStartedAt\);/);
    assert.doesNotMatch(directorSource, /playReturnPetalTransitionLegacy/);
    assert.doesNotMatch(directorSource, /playAvatarFloatingPetalTransitionAtCueLegacy/);
});

test('avatar stand-in controller prefers scene config before fixed cue fallback', () => {
    const controllerBlock = avatarStandInControllerSource.split('    class AvatarStandInController {')[1].split(
        '\n    return {\n        AvatarStandInController',
        1
    )[0];
    assert.match(controllerBlock, /normalizeSceneCue\(scene\) \{/);
    assert.match(controllerBlock, /const config = scene && scene\.avatarStandIn;/);
    assert.match(controllerBlock, /resolveCue\(scene,\s*day\) \{/);
    assert.match(controllerBlock, /const sceneCue = this\.normalizeSceneCue\(scene\);/);
    assert.match(controllerBlock, /return sceneCue \|\| this\.getCue\(day,\s*scene && scene\.id\);/);
    assert.match(controllerBlock, /const cue = this\.resolveCue\(scene,\s*day\);/);
    assert.doesNotMatch(controllerBlock, /getAvatarStandInCueLegacy/);
    assert.doesNotMatch(controllerBlock, /scheduleAvatarStandInForSceneLegacy/);
    assert.doesNotMatch(controllerBlock, /clearAvatarStandInLegacy/);
});
