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
// Structural typing cannot distinguish these prototypes. These compile, but the
// runtime suite verifies rejection before dispatch across every lifecycle path.
class StartPayloadInstance implements StartPayload { game_started = true; }
const sameShape: StartPayload = new StartPayloadInstance();
for (const nonPlain of [[], new Date(), new Map(), () => ({}), sameShape]) {
  runtime.start(nonPlain);
  runtime.end(nonPlain);
  runtime.configure({ payload: () => nonPlain, pageExit: { payload: () => nonPlain } });
}
// @ts-expect-error Serialized JSON is not an object payload.
runtime.start('{"game_started":true}');
runtime.bindCharacter();
runtime.bindCharacter('Example', { signal: new AbortController().signal }).then(character => {
  const locale: string | undefined = character?.languagePreference?.locale;
  const resolved: boolean | undefined = character?.languagePreference?.resolved;
  const fallback: Readonly<NekoMiniGame.AvatarModel> | undefined = character?.fallbackModels?.[0];
  void [locale, resolved, fallback];
});
// @ts-expect-error Binding accepts a name, not a raw private character payload.
runtime.bindCharacter({ lanlan_name: 'Example' });
// @ts-expect-error Null is not an object payload.
runtime.end(null);
// @ts-expect-error Scalar callbacks must not satisfy the payload contract.
runtime.configure({ payload: () => 42 });
// @ts-expect-error Scalar page-exit callbacks must not satisfy the payload contract.
runtime.configure({ pageExit: { payload: () => false } });

// Nested declarations accept the same readonly string shorthand as the root.
const states = ['ready', 'done'] as const;
const nestedContract: NekoMiniGame.ContractDeclaration = {
  type: 'object',
  properties: {
    state: states,
    history: { type: 'array', items: states },
    rounds: { type: 'array', items: { type: 'object', properties: { state: states } } },
  },
};
const manifestContracts: NekoMiniGame.ManifestContracts = { events: { progress: nestedContract } };
void manifestContracts;
// @ts-expect-error Shorthand enum values must remain strings.
const invalidItems: NekoMiniGame.ContractSchema = { type: 'array', items: [1, 2] };
// @ts-expect-error Nested properties must declare a schema or string shorthand.
const invalidProperty: NekoMiniGame.ContractSchema = { type: 'object', properties: { state: 42 } };
void invalidItems;
void invalidProperty;

declare const game: NekoMiniGame.Client;
declare const avatar: NekoMiniGame.AvatarController;
const viewResult: Promise<unknown> = avatar.setView({ scale: 1, x: 0, y: 0 });
const speakingResult: Promise<unknown> = avatar.setSpeaking(true);
const generation: string = game.runtime.session.routeInstanceId;
game.avatar.getCharacter('Example', { signal: new AbortController().signal, timeoutMs: 1000 });
game.avatar.mount({ slot: 'example', characterName: 'Example',
  model: { type: 'mmd', path: '/example.pmx' }, viewport: { mode: 'fixed', width: 200, height: 300 } });
game.avatar.mount({ slot: 'example-png',
  model: { type: 'pngtuber', path: '/example.png' }, viewport: { mode: 'container' } });
game.commands.execute('round:input', { text: 'example' }, { timeoutMs: 1000 });
// @ts-expect-error Speaking state is a boolean, not a display string.
avatar.setSpeaking('true');
void [viewResult, speakingResult, generation];
const visionResult: Promise<NekoMiniGame.VisionResult> = game.vision.analyze({
  region: { kind: 'rect', unit: 'percent', x: 0, y: 0, width: 50, height: 100 }, prompt: 'Describe the board.',
});
game.vision.analyze({ region: { kind: 'element', selector: '#board' }, prompt: 'What changed?' });
game.vision.analyze({ region: { kind: 'edges', unit: 'px', top: 10, left: 10, right: 10, bottom: 10 }, prompt: 'Observe.' });
// @ts-expect-error Coordinates have explicit units, not an assumed device-pixel convention.
game.vision.analyze({ region: { kind: 'rect', x: 0, y: 0, width: 50, height: 50 }, prompt: 'Observe.' });
void visionResult;
const imagesResult: Promise<NekoMiniGame.VisionAnalysisResult> = game.vision.analyze({
  text:'Compare the two states', attachments:[
    {type:'image',source:'/before.png',label:'before'},
    {type:'image',source:new Blob(),label:'after'},
    {type:'image',source:new Uint8Array(),mimeType:'image/png'},
  ],
});
// @ts-expect-error Raw binary input requires its image MIME type.
game.vision.analyze({text:'Observe',attachments:[{type:'image',source:new Uint8Array()}]});
// @ts-expect-error Future modality discriminators are not implemented yet.
game.vision.analyze({text:'Listen',attachments:[{type:'audio',source:'/audio.wav'}]});
void imagesResult;
game.avatar.mount({ slot: 'fit-example', model: { type: 'mmd', path: '/example.pmx' },
  viewport: { mode: 'fixed', width: 200, height: 300 },
  fit: { mode: 'height', autoScale: true, minHeight: 180, align: 'bottom-center' } });
