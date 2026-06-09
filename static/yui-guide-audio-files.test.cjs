const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const guideAudioRoot = path.join(__dirname, 'assets', 'tutorial', 'guide-audio');
const supportedRecordedLocales = ['zh', 'ja', 'en', 'ko', 'ru'];
const guideFiles = [
    'yui-guide-day1-home-guide.js',
    'yui-guide-day2-screen-voice-guide.js',
    'yui-guide-day3-interaction-guide.js',
    'yui-guide-day4-companion-guide.js',
    'yui-guide-day5-personalization-guide.js',
    'yui-guide-day6-agent-guide.js',
    'yui-guide-day7-graduation-guide.js'
];

function loadGuides() {
    const context = vm.createContext({ window: {} });
    for (const fileName of guideFiles) {
        const filePath = path.join(__dirname, fileName);
        vm.runInContext(fs.readFileSync(filePath, 'utf8'), context, { filename: filePath });
    }
    return context.window.YuiGuideDailyGuides || {};
}

function collectRoundVoiceKeys(guides) {
    const keys = [];
    for (const day of [1, 2, 3, 4, 5, 6, 7]) {
        const guide = guides[day] || {};
        const scenes = guide.round && Array.isArray(guide.round.scenes)
            ? guide.round.scenes
            : [];
        for (const scene of scenes) {
            if (typeof scene.voiceKey === 'string' && scene.voiceKey.trim()) {
                keys.push({ day, sceneId: scene.id, voiceKey: scene.voiceKey.trim() });
            }
        }
    }
    return keys;
}

function mergeAudioFilesByKey(guides) {
    const result = {};
    for (const day of [1, 2, 3, 4, 5, 6, 7]) {
        Object.assign(result, (guides[day] && guides[day].audioFilesByKey) || {});
    }
    return result;
}

test('daily tutorial round scenes have recorded audio files for supported locales', () => {
    const guides = loadGuides();
    const audioFilesByKey = mergeAudioFilesByKey(guides);
    const missing = [];

    for (const entry of collectRoundVoiceKeys(guides)) {
        const files = audioFilesByKey[entry.voiceKey];
        const missingKey = `${entry.day}:${entry.sceneId}:${entry.voiceKey}`;
        for (const locale of supportedRecordedLocales) {
            const audioFile = files && typeof files[locale] === 'string' ? files[locale] : '';
            if (!audioFile || !fs.existsSync(path.join(guideAudioRoot, locale, audioFile))) {
                missing.push(`${missingKey}:${locale}:${audioFile || '<no file>'}`);
            }
        }
    }

    assert.deepEqual(missing, []);
});

test('day2 proactive chat uses its own recorded line instead of the detail narration', () => {
    const guides = loadGuides();
    const day2Scenes = guides[2].round.scenes;
    const proactiveScene = day2Scenes.find(scene => scene.id === 'day2_proactive_chat');

    assert.equal(proactiveScene.voiceKey, 'takeover_settings_peek_detail_part_2');
});

test('day2 voice-used intro has recorded audio files for supported locales', () => {
    const guides = loadGuides();
    const audioFilesByKey = mergeAudioFilesByKey(guides);
    const files = audioFilesByKey.avatar_floating_day2_intro_voice_used || {};
    const missing = [];

    for (const locale of supportedRecordedLocales) {
        const audioFile = typeof files[locale] === 'string' ? files[locale] : '';
        if (!audioFile || !fs.existsSync(path.join(guideAudioRoot, locale, audioFile))) {
            missing.push(`${locale}:${audioFile || '<no file>'}`);
        }
    }

    assert.deepEqual(missing, []);
});

test('director merges audio maps from all registered daily guides', () => {
    const directorSource = fs.readFileSync(path.join(__dirname, 'yui-guide-director.js'), 'utf8');

    assert.match(directorSource, /function collectGuideAudioFilesByKey\(\)/);
    assert.match(directorSource, /window\.YuiGuideDailyGuides/);
    assert.doesNotMatch(directorSource, /GUIDE_AUDIO_FILES_BY_KEY = Object\.freeze\(Object\.assign\(\{\}, DAY1_HOME_GUIDE\.audioFilesByKey/);
});
