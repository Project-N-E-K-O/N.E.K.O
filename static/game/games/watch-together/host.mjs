// Trusted page composition. The scene only receives the SDK client and presentation hooks.
import {unlock} from '../../sdk/neko-minigame-media-host.mjs';
import { run } from './scene.mjs';
import { initializeDisplay } from './display.mjs';
import { initializeAvatarPlacement } from './avatar-placement.mjs';
import { createPresentation } from './presentation.mjs';
import { create as createLive2D } from './live2d-host.mjs';
import { create as createVRM } from './vrm-host.mjs';
let renderer = null;
const query = new URLSearchParams(location.search);
// The bootstrap exports window.i18n before localechange on success or fallback.
if (!window.i18n) await new Promise(resolve=>window.addEventListener('localechange',resolve,{once:true}));
const display = initializeDisplay();
const ui = createPresentation({companion: query.get('lanlan_name') || '', restore: display.restore});
document.getElementById('play').addEventListener('click',()=>{
  try {unlock(document.getElementById('video'));} catch (_) { /* Normal playback reports failures. */ }
},{capture:true});
const container = document.getElementById('avatar');
const placement=document.getElementById('avatar-placement');
// The companion stays within the picture, clear of the title row and the dock.
const disposePlacement=initializeAvatarPlacement(document.getElementById('screen'),placement,document.getElementById('avatar-resize'));
const resizeLabel=()=>{const button=document.getElementById('avatar-resize');button.title=window.i18n.t('watchTogether.resizeAvatar');button.setAttribute('aria-label',button.title);};
resizeLabel();window.addEventListener('localechange',resizeLabel);
window.addEventListener('pagehide',disposePlacement,{once:true});
document.getElementById('automatic-enabled').addEventListener('change',event=>{
  if(event.target.checked)try{unlock(document.getElementById('video'));}catch(_){}
},{capture:true});
// The registered avatarHostFactory calls this while the adapter is created below.
window.createWatchTogetherAvatarHost = () => NekoMiniGameAvatarHost.create({slots:{companion:{container,createController:({config})=>{
  renderer = (config.model.type==='vrm'?createVRM:createLive2D)(container);return renderer;
}}}});
const mediaHost = {mount:config=>NekoMiniGameMediaHost.mount({...config,keepPlayingWhenHidden:()=>document.getElementById('automatic-enabled').checked,onMouth:level=>renderer?.mouth(level)})};
const factory = await window.nekoMiniGameSameOriginHostReady;
const transport = factory({gameType:'watch-together', gameVersion:'1.0.0', mediaHost,sessionId:query.get('session_id') || undefined});
const game = await NekoMiniGame.connect({id:'watch-together',version:'1.0.0',requiredCapabilities:['logging','runtime','media-timeline','speech-output','voice-input','avatar-renderer']},{transport});
await run(game, query.get('lanlan_name') || '', ui);
