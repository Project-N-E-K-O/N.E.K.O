// Trusted page composition. The scene only receives the SDK client.
import '../../sdk/neko-minigame-media-host.mjs';
import { run } from './scene.mjs';
import { create as createLive2D } from './live2d-host.mjs';
import { create as createVRM } from './vrm-host.mjs';
let renderer = null;
const container = document.getElementById('avatar');
const avatarHost = NekoMiniGameAvatarHost.create({slots:{companion:{container,createController:({config})=>{
  renderer = (config.model.type==='vrm'?createVRM:createLive2D)(container);return renderer;
}}}});
const mediaHost = {mount:config=>NekoMiniGameMediaHost.mount({...config,onMouth:level=>renderer?.mouth(level)})};
const factory = await window.nekoMiniGameSameOriginHostReady;
const query = new URLSearchParams(location.search);
const transport = factory({gameType:'watch-together', gameVersion:'1.0.0', avatarHost,mediaHost,sessionId:query.get('session_id') || undefined});
const game = await NekoMiniGame.connect({id:'watch-together',version:'1.0.0',requiredCapabilities:['logging','runtime','media-timeline','speech-output','voice-input','avatar-renderer']},{transport});
await run(game, query.get('lanlan_name') || '');
