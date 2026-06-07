const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const standIn = require('./yui-guide-avatar-standin.js');
const directorSource = fs.readFileSync(path.join(__dirname, 'yui-guide-director.js'), 'utf8');

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
        position: 'left-bottom'
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
    const allowedResources = new Set(['peek-left-border', 'peek-right-border', 'peek-head']);
    const allowedPositions = new Set(['left-bottom', 'right-bottom', 'bottom-right', 'top-left-flipped']);

    assert.equal(Object.keys(cues).length, 6);
    for (const day of [2, 3, 4, 5, 6, 7]) {
        const dayCues = Object.values(cues[String(day)] || {});
        assert.equal(dayCues.length, 2);
        for (const cue of dayCues) {
            assert.equal(cue.delayMs, 900);
            assert.equal(cue.durationMs, 5000);
            assert.equal(allowedResources.has(cue.resource), true);
            assert.equal(allowedPositions.has(cue.position), true);
        }
    }
});

test('director fades the model before showing avatar stand-in overlay', () => {
    assert.match(directorSource, /const AVATAR_STAND_IN_MODEL_FADE_MS\s*=\s*1000;/);
    assert.match(directorSource, /this\.avatarStandInFadeTimer = null;/);
    assert.match(directorSource, /element\.style\.setProperty\('transition', 'opacity ' \+ AVATAR_STAND_IN_MODEL_FADE_MS \+ 'ms ease', 'important'\);/);
    assert.match(directorSource, /element\.style\.setProperty\('transition', 'opacity ' \+ AVATAR_STAND_IN_MODEL_FADE_MS \+ 'ms ease', 'important'\);[\s\S]*void element\.offsetWidth;[\s\S]*element\.style\.setProperty\('opacity', '0', 'important'\);/);
    assert.match(directorSource, /this\.prepareAvatarStandInOpacityTargets\(\);[\s\S]*this\.avatarStandInFadeTimer = window\.setTimeout\(\(\) => \{[\s\S]*this\.overlay\.showAvatarStandIn/);
    assert.match(directorSource, /window\.clearTimeout\(this\.avatarStandInFadeTimer\);[\s\S]*this\.avatarStandInFadeTimer = null;/);
});
