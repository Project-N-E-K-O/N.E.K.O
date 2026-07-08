'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, 'app-interpage.js'), 'utf8');

test('voice chat composer sync posts through BroadcastChannel and Electron bridge', () => {
    assert.match(source, /function getVoiceChatComposerHiddenElectronBridge\(\)/);
    assert.match(source, /function postVoiceChatComposerHiddenElectron\(payload\)/);
    assert.match(source, /function postVoiceChatComposerHiddenPayload\(payload\)/);
    assert.match(source, /postInterpageMessage\(payload\);/);
    assert.match(source, /postVoiceChatComposerHiddenElectron\(payload\);/);
    assert.match(source, /postVoiceChatComposerHiddenPayload\(\{\s*action: 'voice_chat_active'/);
});

test('voice chat composer sync handles Electron restore events without rebroadcasting', () => {
    assert.match(source, /function handleVoiceChatComposerHiddenMessage\(data, via\)/);
    assert.match(source, /window\.addEventListener\('neko:electron-voice-chat-composer-hidden'/);
    assert.match(source, /handleVoiceChatComposerHiddenMessage\(\(event && event\.detail\) \|\| \{\}, 'electron-ipc'\);/);
});

test('voice chat composer restore is allowed before full chat config hydration', () => {
    assert.match(
        source,
        /function isVoiceChatComposerHiddenMessageForCurrentLanlan\(data\) \{[\s\S]*?if \(currentName\) return data\.lanlan_name === currentName;[\s\S]*?return data\.active === false;[\s\S]*?\}/,
    );
});
