const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'app-proactive.js'), 'utf8');

test('proactive scheduler re-arms after new-user icebreaker suppression', () => {
    assert.match(source, /function getNewUserIcebreakerRetryDelayMs\(\)/);
    assert.match(source, /mod\.getNewUserIcebreakerRetryDelayMs = getNewUserIcebreakerRetryDelayMs/);

    const preconditionBlock = source.split('if (!canTriggerProactively()) {')[1].split(
        'S.proactiveChatBackoffLevel = 0;',
        1
    )[0];

    assert.match(preconditionBlock, /const icebreakerRetryDelayMs = getNewUserIcebreakerRetryDelayMs\(\);/);
    assert.match(preconditionBlock, /S\.proactiveChatTimer = setTimeout\(scheduleProactiveChat, retryDelayMs\);/);
});
