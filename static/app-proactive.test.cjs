const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'app-proactive.js'), 'utf8');

test('proactive scheduler re-arms after new-user icebreaker suppression', () => {
    assert.match(source, /function getNewUserIcebreakerRetryDelayMs\(\)/);
    assert.match(source, /mod\.getNewUserIcebreakerRetryDelayMs = getNewUserIcebreakerRetryDelayMs/);

    const preconditionStart = source.indexOf('if (!canTriggerProactively()) {');
    assert.notEqual(preconditionStart, -1, 'missing proactive precondition guard block');
    const preconditionEnd = source.indexOf('S.proactiveChatBackoffLevel = 0;', preconditionStart);
    assert.notEqual(preconditionEnd, -1, 'missing proactive backoff reset in precondition block');
    const preconditionBlock = source.slice(preconditionStart, preconditionEnd);

    assert.match(preconditionBlock, /const icebreakerRetryDelayMs = getNewUserIcebreakerRetryDelayMs\(\);/);
    assert.match(preconditionBlock, /S\.proactiveChatTimer = setTimeout\(scheduleProactiveChat, retryDelayMs\);/);
});
