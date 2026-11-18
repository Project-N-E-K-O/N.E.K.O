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

test('no-match toast uses the selected locale', async () => {
  const toasts = [];
  const window = { _musicSearchEpoch:0, safeT:(key) => `translated:${key}`, showStatusToast:text=>toasts.push(text) };
  runInNewContext(command, {window, console, fetch:async () => ({json:async () => ({success:true,data:[{name:'Wrong',artist:'Other'}]})})});
  await window.processMusicCommands('[play_music: {"name":"Song","artist":"Band"}]');
  assert.deepEqual(toasts, ['translated:music.noExactRecording']);
});

const websocketSource = readFileSync(require.resolve('../../static/app/app-websocket.js'), 'utf8');
const pluginDispatch = websocketSource.slice(websocketSource.indexOf('async function dispatchMusicPlayUrlResponse('),
  websocketSource.indexOf('function handleMusicPlayUrlResponse('));
for (const proactive of [true, false]) test(`plugin websocket dispatch preserves identity and source (proactive=${proactive})`, async () => {
  const played = [], claims = [];
  const context = {window:{dispatchMusicPlay:async (track, options)=>{played.push({track,options});return true;}},
    console, musicPlayCommandEpoch:1, getMusicPlayUrlClaimKey:()=> 'key', claimMusicPlayUrl:key=>claims.push(key)};
  runInNewContext(pluginDispatch, context);
  await context.dispatchMusicPlayUrlResponse({url:'https://m10.music.126.net/song.mp3',netease_id:123,
    ...(proactive ? {playback_source:'proactive'} : {})}, 'websocket', 1);
  assert.equal(played[0].track.neteaseId,123);
  assert.equal(played[0].options.source,proactive ? 'proactive' : 'music_play_url');
  assert.deepEqual(claims,['key']);
});
