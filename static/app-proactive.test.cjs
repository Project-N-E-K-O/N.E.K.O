const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'app/app-proactive.js'), 'utf8');

test('proactive scheduler re-arms after new-user icebreaker suppression', () => {
    assert.match(source, /function getNewUserIcebreakerRetryDelayMs\(\)/);
    assert.match(source, /function getNewUserIcebreakerBlockingRetryMs\(\)/);

    const blockingStart = source.indexOf('function getNewUserIcebreakerBlockingRetryMs()');
    assert.notEqual(blockingStart, -1, 'missing blocking retry helper');
    const blockingEnd = source.indexOf('const store = readNewUserIcebreakerStore();', blockingStart);
    assert.notEqual(blockingEnd, -1, 'missing blocking retry persisted-store branch');
    const activeSessionBlock = source.slice(blockingStart, blockingEnd);
    assert.match(activeSessionBlock, /return getNewUserIcebreakerRetryDelayMs\(\);/);
    assert.doesNotMatch(activeSessionBlock, /return NEW_USER_ICEBREAKER_BLOCKING_WINDOW_MS;/);

    const scheduleStart = source.indexOf('function scheduleProactiveChat()');
    assert.notEqual(scheduleStart, -1, 'missing proactive scheduler function');

    const preconditionStart = source.indexOf('if (!canTriggerProactively()) {', scheduleStart);
    assert.notEqual(preconditionStart, -1, 'missing proactive precondition guard block');
    const preconditionEnd = source.indexOf('return;', preconditionStart);
    assert.notEqual(preconditionEnd, -1, 'missing proactive return in precondition block');
    const preconditionBlock = source.slice(preconditionStart, preconditionEnd);

    assert.match(preconditionBlock, /var icebreakerRetryMs = getNewUserIcebreakerBlockingRetryMs\(\);/);
    assert.match(preconditionBlock, /S\.proactiveChatTimer = setTimeout\(scheduleProactiveChat, icebreakerRetryMs \+ 250\);/);

    const activeStart = source.indexOf('if (isNewUserIcebreakerPeriodActive()) {', scheduleStart);
    assert.notEqual(activeStart, -1, 'missing active icebreaker suppression block');
    const activeEnd = source.indexOf('return;', activeStart);
    assert.notEqual(activeEnd, -1, 'missing active icebreaker return');
    const activeBlock = source.slice(activeStart, activeEnd);
    assert.match(activeBlock, /getNewUserIcebreakerBlockingRetryMs\(\) \|\| getNewUserIcebreakerRetryDelayMs\(\)/);
});

test('youtube login does not enable the unrelated personal dynamics mode', () => {
    assert.match(
        source,
        /new Set\(\['bilibili', 'douyin', 'kuaishou', 'weibo', 'reddit', 'twitter'\]\)/
    );
    const personalPlatformStart = source.indexOf('async function getAvailablePersonalPlatforms()');
    const personalPlatformEnd = source.indexOf('mod.getAvailablePersonalPlatforms', personalPlatformStart);
    const personalPlatformBlock = source.slice(personalPlatformStart, personalPlatformEnd);
    assert.match(personalPlatformBlock, /personalFeedPlatforms\.has\(platform\) && info\.has_cookies/);
    assert.doesNotMatch(personalPlatformBlock, /personalFeedPlatforms[^;]*youtube/);
});
