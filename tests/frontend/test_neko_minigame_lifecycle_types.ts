import NekoMiniGame = require('../../static/game/sdk/neko-minigame-sdk');

declare const runtime: NekoMiniGame.Runtime;
interface StartPayload { game_started: boolean; state?: { score: number } }
interface EndPayload { reason: string }
const start: StartPayload = { game_started: true };
const end: EndPayload = { reason: 'finished' };
runtime.start(start);
runtime.end(end);
runtime.start();
runtime.end();
runtime.configure({ payload: () => start, pageExit: { payload: () => end } });
// @ts-expect-error Serialized JSON is not an object payload.
runtime.start('{"game_started":true}');
// @ts-expect-error Null is not an object payload.
runtime.end(null);
// @ts-expect-error Scalar callbacks must not satisfy the payload contract.
runtime.configure({ payload: () => 42 });
// @ts-expect-error Scalar page-exit callbacks must not satisfy the payload contract.
runtime.configure({ pageExit: { payload: () => false } });
