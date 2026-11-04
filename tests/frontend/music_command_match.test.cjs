const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');
const src = readFileSync(require.resolve('../../static/app/app-chat.js'), 'utf8');
const command = src.slice(src.indexOf('window.processMusicCommands ='), src.indexOf('// ======================== appendMessage'));
test('legacy chat directive never plays a same-title recording by the wrong artist', async () => {
  const requested = '[play_music: {"name":"Night Sky","artist":"Requested Artist"}]';
  const wrong = {name:'Night Sky',artist:'Other Artist'};
  const right = {name:'Night Sky',artist:'Requested Artist'};
  const results = [wrong], played = [];
  const window = { _musicSearchEpoch:0, dispatchMusicPlay: track => played.push(track) };
  runInNewContext(command, {window, console, fetch:async () => ({json:async () => ({success:true,data:results})})});
  await window.processMusicCommands(requested);
  assert.deepEqual(played, []);
  results.push(right);
  await window.processMusicCommands(requested);
  assert.deepEqual(played, [right]);
});
