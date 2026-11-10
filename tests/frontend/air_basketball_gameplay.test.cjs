// Run the actual 1.0 orchestration against a minimal DOM/SDK host. Browser
// checks separately cover real layout, rendering, pointer input and the avatar.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const gameRoot = path.join(root, 'static/game/games/air_basketball');

async function harness(mode = 'endless') {
  let now=0, serial=0;
  const timers=new Map(), frames=[], routes=[];
  const schedule=(fn,ms)=>{ const id=++serial; timers.set(id,{fn,due:now+ms}); return id; };
  const cancel=id=>timers.delete(id);
  const eventTarget=()=>({ listeners:{}, addEventListener(type,fn){ (this.listeners[type] ||= []).push(fn); }, emit(type,event={}){ for(const fn of this.listeners[type]||[])fn({currentTarget:this,preventDefault(){},stopPropagation(){},...event}); } });
  const elements=new Map();
  const rect=(left=0,top=0,width=320,height=500)=>({left,top,width,height,right:left+width,bottom:top+height});
  function node(id) {
    if(elements.has(id))return elements.get(id);
    const classes=new Set();
    const el=Object.assign(eventTarget(),{
      id,hidden:false,value:'',textContent:'',dataset:{},style:{setProperty(){}},
      classList:{add(...keys){keys.forEach(k=>classes.add(k));},remove(...keys){keys.forEach(k=>classes.delete(k));},contains:k=>classes.has(k),toggle(k,force){const value=force??!classes.has(k);value?classes.add(k):classes.delete(k);return value;}},
      setAttribute(key,value){this[key]=value;},getAttribute(key){return this[key];},
      removeAttribute(key){if(key!=='style')delete this[key];},
      appendChild(){},setPointerCapture(){},cloneNode(){return node(id+'-clone');},remove(){},
      getBoundingClientRect:()=>rect(),
      closest:selector=>node((id.startsWith('player')?'player':'neko')+selector),
      querySelector:selector=>node(id+selector),
      querySelectorAll:selector=>selector==='.avatar-hit-zone'?['head','torso','lower'].map(k=>node('hit-'+k)):[],
      getContext:()=>new Proxy({createLinearGradient:()=>({addColorStop(){}}),createRadialGradient:()=>({addColorStop(){}})},{get:(obj,key)=>obj[key]||(()=>{})})
    });
    elements.set(id,el);return el;
  }
  const html=fs.readFileSync(path.join(root,'templates/air_basketball.html'),'utf8');
  for(const match of html.matchAll(/id="([^"]+)"/g))node(match[1]);
  const player=node('player-court'),neko=node('neko-court');
  player.getBoundingClientRect=()=>rect(80,100);
  neko.getBoundingClientRect=()=>rect(400,100);
  const avatar=node('air-neko-avatar');
  avatar.getBoundingClientRect=()=>rect(720,230,180,390);
  avatar.classList.add('is-ready');avatar.dataset.avatarReady='true';
  for(const [index,key] of ['head','torso','lower'].entries())node('hit-'+key).getBoundingClientRect=()=>rect(760,250+index*80,80,150);
  node('result-board').hidden=true;
  const radio=node('mode');radio.value=mode;
  const document=Object.assign(eventTarget(),{
    body:node('body'),documentElement:{clientWidth:1000,lang:'zh-CN'},hidden:false,
    getElementById:node,
    querySelector:selector=>selector.includes('match-mode')?radio:node(selector),
    querySelectorAll:selector=>selector.includes('match-mode')?[radio]:[]
  });
  const window=Object.assign(eventTarget(),{innerWidth:1000,innerHeight:700,devicePixelRatio:1,location:{search:'?test_mode=1'},lanlan_config:{},performance:{now:()=>now}});
  global.window=window;global.Image=class{};
  const physics=await import(pathToFileURL(path.join(gameRoot,'physics.js')));
  const shared=await import(pathToFileURL(path.join(gameRoot,'shared-court.js')));
  const {MatchTimers}=await import(pathToFileURL(path.join(gameRoot,'match-timers.js')));
  const strings=JSON.parse(fs.readFileSync(path.join(root,'static/locales/zh-CN.json'),'utf8')).airBasketball;
  const runtime={
    airBasketballSdkReady:Promise.resolve({game:{},identity:{name:'Test Neko'}}),
    configureGameRuntime:async()=>{},disposeGameSdk(){},
    endGameRuntime:async payload=>routes.push({end:payload}),
    startGameRuntime:async payload=>routes.push({start:payload}),
    playGameTone:async()=>{},preloadNekoSpeech:async()=>({ok:true}),speakNekoSpeech:async()=>({ok:true}),
    unlockGameAudio:async()=>{},waitForNekoSpeechPlayback:async()=>{}
  };
  const dependencies=[
    {applyTranslations(){},t:(key,args={})=>String(strings[key]||key).replace(/\{(\w+)\}/g,(_,name)=>args[name]??'Test Neko'),voiceLine:()=>'',voiceLines:()=>[]},
    physics,{initNekoAvatar(){},reactNeko(){},setAvatarUnavailableLabel(){}},runtime,shared,
    {MatchTimers:class extends MatchTimers {constructor(){super({now:()=>now,schedule,cancel});}}}
  ];
  let source=fs.readFileSync(path.join(gameRoot,'game.js'),'utf8');
  source=source.replace(/const \[i18nModule,[\s\S]*?\]\);/, 'const [i18nModule, physicsModule, avatarModule, sdkModule, sharedCourtModule, timersModule] = deps;');
  source=source.replaceAll('import.meta.url',JSON.stringify(pathToFileURL(path.join(gameRoot,'game.js')).href));
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  const lanes = await new AsyncFunction('deps','window','document','performance','requestAnimationFrame','cancelAnimationFrame','setTimeout','clearTimeout','navigator',source+'\nreturn {playerLane,nekoLane};')(
    dependencies,window,document,{now:()=>now},fn=>(frames.push(fn),frames.length),()=>{},schedule,cancel,{language:'zh-CN'}
  );
  await Promise.resolve();
  const advance=ms=>{
    for(let remaining=ms;remaining>1e-8;){const dt=Math.min(1000/60,remaining);now+=dt;remaining-=dt;
      for(const [id,timer] of [...timers])if(timer.due<=now){timers.delete(id);timer.fn();}
      const frame=frames.shift();frame?.(now);
    }
  };
  return {api:window.AirBasketballMVP,node,advance,routes,window,document,...lanes};
}

test('the same four player balls remain visible through returns, possession changes and resize',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const visual=h.node('cross-ball'), lane=h.playerLane;
  assert.equal(lane.ball.pageOverlay,false);
  assert.equal(visual.classList.contains('is-crossing'),false);
  const original=[...lane.ballPool];
  for(let i=0;i<12;i++) {
    const old=lane.ball;
    Object.assign(old,{flying:true,y:lane.height+old.r+2,owner:i%2?'neko':'player'});
    h.advance(20);
    assert.equal(old.poolState,'returning');
    assert(original.includes(lane.ball));
    assert.equal(lane.ball.pageOverlay,false);
    lane.canvas.getBoundingClientRect=()=>({left:80,top:100,width:240+i*12,height:360+i*18});
    h.window.emit('resize');h.advance(20);
    assert.equal(lane.ball.pageOverlay,false);
    assert.equal(visual.classList.contains('is-crossing'),false);
    assert(lane.ball.x>0&&lane.ball.x<lane.width&&lane.ball.y<lane.height);
    h.advance(900);
    assert.deepEqual(lane.ballPool,original);
  }
});

test('a player ball leaving the scene switches to the page sprite and back without duplication',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const ball=h.playerLane.ball, visual=h.node('cross-ball');
  Object.assign(ball,{flying:true,x:h.playerLane.width+h.nekoLane.width+5,y:400,allowOuterExit:true,vx:0,vy:0});
  h.advance(1);
  assert.equal(ball.pageOverlay,true);
  Object.assign(ball,{x:200,y:300,allowOuterExit:false});h.advance(1);
  assert.equal(ball.pageOverlay,false);assert.equal(visual.classList.contains('is-crossing'),false);
});

test('real pointer handlers preserve charging, scoring and focus cost while supplying the next ball',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court');
  const {x,y}=h.playerLane.ball;
  const hoop=h.playerLane.getHoopPose();
  const dx=200*(hoop.x-x)/(y-hoop.rimY);
  canvas.emit('pointerdown',{clientX:80+x,clientY:100+y,pointerId:1});
  canvas.emit('pointermove',{clientX:80+x+dx,clientY:100+y-200,pointerId:1});
  canvas.emit('pointerup',{pointerId:1});
  assert(h.api.getState().playerBallFlying);
  assert.equal(h.api.getState().ballPools.player.filter(ball=>ball.state==='ready').length,3);
  assert.equal(h.api.getState().player.focus,82);
  h.advance(2100);
  const state=h.api.getState();assert.equal(state.player.makes,1);assert(state.player.score>=2);
  assert.equal(state.neko.score,0);assert.equal(state.player.attempts,1);
  assert.equal(h.routes.filter(route=>route.start).length,1);
});

test('pause freezes physics and match time, then resumes without including the paused duration',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  h.api.shootPlayer(30,-500);h.advance(100);
  h.node('pause-match').emit('click');const before=h.api.getState();h.advance(5000);
  const paused=h.api.getState();assert(paused.paused);
  assert.deepEqual(paused.playerBallMotion,before.playerBallMotion);assert.equal(paused.elapsed,before.elapsed);
  assert.deepEqual(paused.ballPools,before.ballPools);
  assert.equal(h.api.shootPlayer(0,-700),false);
  h.node('resume-match').emit('click');h.advance(1200);
  assert.equal(h.api.getState().paused,false);assert(h.api.getState().elapsed<=before.elapsed+1);
});

test('a pending aimed prank pauses its deadline and still launches after resuming',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();
  // The isolated-cross helper parks Neko's ready ball; a normal start restores it.
  h.api.start();assert.equal(h.api.prankNeko(),true);
  h.node('pause-match').emit('click');h.advance(2000);
  assert(h.api.getState().nekoCounterPending);assert.equal(h.api.getState().nekoActiveBalls,0);
  h.node('resume-match').emit('click');h.advance(600);
  assert.equal(h.api.getState().nekoCounterPending,false);assert(h.api.getState().nekoActiveBalls>0);
  h.advance(1200);assert.equal(h.api.getState().ballPools.neko.length+h.api.getState().ballPools.player.length,8);
});

test('mouse theft retains its release deadline through pause and allows escape afterward',async()=>{
  const h=await harness();h.api.start();assert.equal(h.api.stealMouse(),true);
  h.advance(200);h.node('pause-match').emit('click');h.advance(6000);
  assert(h.api.getState().mouseStealActive);
  h.node('resume-match').emit('click');assert.equal(h.api.releaseMouse(),true);
  assert.equal(h.api.getState().mouseStealActive,false);
});

test('the original ball-to-avatar interference remains reachable through the avatar-side exit',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  assert(h.api.shootPlayer(880,-300));h.advance(900);
  const state=h.api.getState();assert.equal(state.nekoBallHitCount,1);assert(state.interferenceCount>=1);
});

test('timed mode settles at 60 seconds and replay resets without duplicating the runtime start',async()=>{
  const h=await harness('timed');h.api.start();h.advance(60100);
  assert.equal(h.api.getState().running,false);assert.equal(h.api.getState().remaining,0);
  assert.equal(h.node('result-board').hidden,false);
  h.api.start();assert.equal(h.api.getState().player.score,0);assert.equal(h.api.getState().elapsed,0);
  assert.equal(h.api.getState().paused,false);assert.equal(h.routes.filter(route=>route.start).length,2);
});

test('four player shots consume the existing supply, block a fifth and recover without new objects',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const original=[...h.playerLane.ballPool];
  assert(h.api.shootPlayer(0,-400));assert(h.api.shootPlayer(0,-450));assert(h.api.shootPlayer(0,-500));
  assert(h.api.shootPlayer(0,-550));
  assert.equal(h.api.shootPlayer(0,-600),false);
  assert.equal(h.api.getState().player.attempts,4);
  assert.equal(h.api.getState().ballPools.player.filter(ball=>ball.state==='flying').length,4);
  h.advance(9000);
  assert.equal(h.api.getState().ballPools.player.filter(ball=>ball.state==='ready').length,4);
  assert.deepEqual(h.playerLane.ballPool,original);
  assert(h.api.shootPlayer(0,-400));
});

test('the next ready ball takes pointer priority over a nearby flying ball',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court');
  const pointerShoot=()=>{
    const ball=h.playerLane.ball;
    canvas.emit('pointerdown',{clientX:80+ball.x,clientY:100+ball.y,pointerId:1});
    assert(h.playerLane.aim);
    canvas.emit('pointermove',{clientX:80+ball.x+34,clientY:100+ball.y-154,pointerId:1});
    canvas.emit('pointerup',{pointerId:1});
  };
  pointerShoot();h.advance(170);pointerShoot();
  assert.equal(h.api.getState().player.attempts,2);
  assert.equal(h.api.getState().ballPools.player.filter(ball=>ball.state==='flying').length,2);
});

test('two minutes of actual AI, pranks and repeated player input never grow the eight-ball inventory',async()=>{
  const h=await harness();h.api.start();
  const original=[...h.playerLane.ballPool,...h.nekoLane.ballPool];
  for(let tick=0;tick<240;tick++){
    h.api.shootPlayer(100,-600);h.advance(500);
    const balls=[...h.playerLane.ballPool,...h.nekoLane.ballPool];
    assert.equal(balls.length,8);assert.equal(new Set(balls).size,8);
    assert(balls.every(ball=>original.includes(ball)&&!ball.expired));
    for(const side of [-1,1]){
      const bottom=balls.filter(ball=>!ball.flying&&!ball.inTransit&&ball.roll?.phase==='tray'
        && !ball.roll.stacked&&(ball.roll.side||Math.sign(ball.roll.x)||-1)===side);
      assert(bottom.length<=6,'a seventh ball must have physical support above the bottom row');
    }
  }
  assert(h.api.getState().player.attempts>20);assert(h.api.getState().neko.attempts>20);
  h.api.start();assert.deepEqual([...h.playerLane.ballPool,...h.nekoLane.ballPool],original);
  assert(h.api.getState().ballPools.player.every(ball=>ball.state==='ready'));
});

function putAllBallsInPlayerTray(h) {
  const { playerLane, nekoLane } = h;
  const court = playerLane.sharedCourt;
  for (const ball of [...nekoLane.ballPool]) {
    ball.inTransit = false;
    court.moveSupplyBall(ball, nekoLane, playerLane);
  }
  for (const [index, ball] of playerLane.ballPool.entries()) {
    const x = playerLane.width * (.16 + index * .70 / (playerLane.ballPool.length - 1)), y = playerLane.height * .892;
    Object.assign(ball, { x, y, readyPosition:{ x, y } });
  }
}

test('Neko resumes automatically from a full blue tray and leaves the held ball untouched', async () => {
  const h = await harness();
  h.api.start();
  putAllBallsInPlayerTray(h);
  h.advance(1);
  const { playerLane, nekoLane } = h;
  const held = playerLane.ballPool.at(-1), borrowed = playerLane.ballPool.at(-2);
  const canvas = h.node('player-court');
  canvas.emit('pointerdown', { clientX:80 + held.x, clientY:100 + held.y, pointerId:31 });
  assert.equal(playerLane.heldBall, held);
  const random = Math.random;
  try {
    Math.random = () => .5;
    h.advance(730);
  } finally {
    Math.random = random;
  }
  assert.equal(playerLane.heldBall, held);
  assert.equal(h.api.getState().neko.attempts, 1);
  assert.equal(borrowed.owner, 'neko');
  assert(borrowed.flying && playerLane.ballPool.includes(borrowed));
  assert.equal(nekoLane.ballPool.length, 0, 'the shot starts in place');
  h.advance(1500);
  const state = h.api.getState();
  assert.equal(state.player.score, 0);
  assert.equal(state.neko.makes, 1);
  assert.equal(state.neko.score, 3, 'a normal shot from the blue tray has no cross-court bonus');
});

test('Neko leaves four unheld blue balls available and never takes a held fifth ball', async () => {
  const h = await harness();
  h.api.test.prepareIsolatedCrossTest();
  putAllBallsInPlayerTray(h);
  const { playerLane } = h;
  const held = playerLane.ballPool.at(-1);
  playerLane.ballPool.slice(0,3).forEach(ball=>{ball.inTransit=true;});
  assert(playerLane.beginAim({ x:held.x, y:held.y }));
  assert.equal(h.api.shootNeko(), false);
  assert.equal(h.api.getState().neko.attempts, 0);
  playerLane.cancelAim();
  assert(h.api.shootNeko());
  assert.equal(playerLane.ballPool.filter(ball => playerLane.isReadyBall(ball)).length, 4);
});

test('secondary pointers cannot steal or release the primary held ball',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),held=h.playerLane.ball,other=h.playerLane.guests[0];
  canvas.emit('pointerdown',{clientX:80+held.x,clientY:100+held.y,pointerId:11});
  canvas.emit('pointermove',{clientX:80+held.x+30,clientY:100+held.y-150,pointerId:11});
  const aim={...h.playerLane.aim};
  canvas.emit('pointerdown',{clientX:80+other.x,clientY:100+other.y,pointerId:12});
  canvas.emit('pointermove',{clientX:0,clientY:0,pointerId:12});
  canvas.emit('pointercancel',{pointerId:12});canvas.emit('pointerup',{pointerId:12});
  assert.equal(h.playerLane.heldBall,held);assert.equal(h.playerLane.ball,held);
  assert.deepEqual(h.playerLane.aim,aim);assert.equal(h.api.getState().player.attempts,0);
  canvas.emit('pointerup',{pointerId:11});
  assert.equal(h.api.getState().player.attempts,1);assert(held.flying);assert.equal(held.held,false);
});

for(const event of ['pointercancel','lostpointercapture'])test(`${event} releases the held lock without shooting`,async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),held=h.playerLane.ball;
  canvas.emit('pointerdown',{clientX:80+held.x,clientY:100+held.y,pointerId:9});
  canvas.emit('pointermove',{clientX:80+held.x+30,clientY:100+held.y-150,pointerId:9});
  canvas.emit(event,{pointerId:9});canvas.emit('pointerup',{pointerId:9});
  assert.equal(h.playerLane.heldBall,null);assert.equal(held.held,false);assert(!held.flying);
  assert.equal(h.api.getState().player.attempts,0);
  canvas.emit('pointerdown',{clientX:80+held.x,clientY:100+held.y,pointerId:10});
  assert.equal(h.playerLane.heldBall,held);
});

test('a return into a four-ball pool preserves the primary pointer’s held ball and aim through release',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),lane=h.playerLane;
  const held=lane.ballPool.reduce((rightmost, ball)=>ball.x>rightmost.x?ball:rightmost);
  const displaced=lane.ball;
  canvas.emit('pointerdown',{clientX:80+held.x,clientY:100+held.y,pointerId:9});
  canvas.emit('pointermove',{clientX:80+held.x+30,clientY:100+held.y-150,pointerId:9});
  const before={x:held.x,y:held.y,telemetry:lane.getAimTelemetry()};
  const incoming=h.nekoLane.ball;
  Object.assign(incoming,{inTransit:false,flying:true,poolState:'flying',poolSlot:null,
    owner:'neko',x:50-lane.width,y:lane.height*.6,vx:0,vy:60});
  h.advance(1500);
  assert.equal(lane.heldBall,held);assert.equal(lane.ball,held);
  assert.equal(held.x,before.x);assert.equal(held.y,before.y);
  assert.deepEqual(lane.getAimTelemetry(),before.telemetry);
  assert(lane.ballPool.includes(incoming));assert(lane.ballPool.includes(displaced));
  assert.equal(lane.ballPool.length,5);
  canvas.emit('pointerup',{pointerId:9});
  assert(held.flying);assert.equal(held.held,false);assert.equal(lane.heldBall,null);
  assert.equal(h.api.getState().player.attempts,1);
  assert.equal(h.api.getState().ballPools.player.length+h.api.getState().ballPools.neko.length,8);
});

async function interferenceHarness(){
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  for(const ball of h.nekoLane.ballPool)Object.assign(ball,{inTransit:false,owner:'neko'});
  h.nekoLane.selectReadyBall();return h;
}

test('Neko can launch all four original balls and the fourth is inside the active-ball budget',async()=>{
  const h=await interferenceHarness(),lane=h.nekoLane;
  const original=[...lane.ballPool];
  assert.equal(original.length,4);
  for(let i=0;i<4;i++) {
    assert(h.api.shootNeko(),`native shot ${i+1}`);
    assert.equal(original.filter(ball=>ball.flying).length,i+1);
  }
  assert(original.every(ball=>ball.nativeShot&&ball.owner==='neko'));
  assert.equal(h.api.shootNeko(),false);
  h.advance(9000);
  const all=[...h.playerLane.ballPool,...h.nekoLane.ballPool];
  assert.equal(new Set(all).size,8);
  assert(original.every(ball=>all.includes(ball)));
});

for(const slot of [0,1,2,3])test(`the opponent ready ball in slot ${slot} can be grabbed and thrown by the player`,async()=>{
  const h=await interferenceHarness(),canvas=h.node('neko-court'),ball=h.nekoLane.ballPool[slot];
  const others=h.nekoLane.ballPool.filter(candidate=>candidate!==ball).map(candidate=>({ball:candidate,x:candidate.x,y:candidate.y}));
  const x=ball.x,y=ball.y;
  canvas.emit('pointerdown',{clientX:400+x,clientY:100+y,pointerId:21});
  canvas.emit('pointermove',{clientX:430+x,clientY:80+y,pointerId:21});
  assert.equal(h.nekoLane.heldBall,ball);assert.equal(ball.held,true);
  assert.equal(ball.x,x);assert.equal(ball.y,y);assert(h.nekoLane.getAimTelemetry().power>0);
  h.advance(200);
  assert.equal(h.nekoLane.heldBall,ball);
  canvas.emit('pointerup',{pointerId:21});
  assert.equal(ball.held,false);assert(ball.flying);assert.equal(ball.owner,'player');
  assert(ball.vx>0);assert(ball.vy<0);
  for(const other of others){assert.equal(other.ball.x,other.x);assert.equal(other.ball.y,other.y);}
  assert.equal(h.api.getState().player.attempts,1);assert(h.api.getState().player.focus<100);
  assert.equal(h.api.getState().neko.attempts,0);
});

test('a ready-ball drag stays on the selected ball even with a flying ball nearby',async()=>{
  const h=await interferenceHarness(),lane=h.nekoLane,canvas=h.node('neko-court');
  const projectile=lane.ball;lane.shoot(20,-300);
  const target=lane.ballPool.find(ball=>lane.isReadyBall(ball)&&ball.poolSlot===1);
  Object.assign(projectile,{x:target.x+60,y:target.y,vx:20,vy:-30});
  canvas.emit('pointerdown',{clientX:400+target.x,clientY:100+target.y,pointerId:22});
  const x=target.x;
  canvas.emit('pointermove',{clientX:430+x,clientY:100+target.y,pointerId:22});
  assert.equal(h.nekoLane.heldBall,target);assert.equal(target.x,x);assert.equal(projectile.vx,20);assert.equal(projectile.vy,-30);
  canvas.emit('pointerup',{pointerId:22});
});

test('airborne opponent balls still receive the original drag impulse',async()=>{
  const h=await interferenceHarness(),lane=h.nekoLane,canvas=h.node('neko-court');
  const ball=lane.ball;lane.shoot(20,-300);
  Object.assign(ball,{x:100,y:200,vx:20,vy:-30});
  canvas.emit('pointerdown',{clientX:500,clientY:300,pointerId:23});
  canvas.emit('pointermove',{clientX:520,clientY:290,pointerId:23});
  assert(ball.vx>20);assert(ball.vy<-30);assert.equal(ball.dragged,undefined);
  canvas.emit('pointerup',{pointerId:23});assert.equal(h.api.getState().player.action,'idle');
});

for(const kind of ['lostpointercapture','pointerup','pointercancel','blur'])test(`interference ${kind} cleanup allows the next player grab`,async()=>{
  const h=await interferenceHarness(),canvas=h.node('neko-court'),target=h.nekoLane.ball;
  h.nekoLane.shoot(0,-300);Object.assign(target,{x:100,y:200});
  canvas.emit('pointerdown',{clientX:400+target.x,clientY:100+target.y,pointerId:24});
  assert.equal(h.api.getState().player.action,'player');
  (kind==='blur'?h.window:h.document).emit(kind,{pointerId:24});
  assert.equal(h.api.getState().player.action,'idle');
  const ball=h.playerLane.ball;
  h.node('player-court').emit('pointerdown',{clientX:80+ball.x,clientY:100+ball.y,pointerId:25});
  assert.equal(h.playerLane.heldBall,ball);
});

test('slow sub-pixel opponent drags accumulate instead of being discarded at every input event',async()=>{
  const h=await interferenceHarness(),canvas=h.node('neko-court'),ball=h.nekoLane.ball;
  h.nekoLane.shoot(20,-300);Object.assign(ball,{x:100,y:200});
  const x=ball.x,y=ball.y;
  canvas.emit('pointerdown',{clientX:400+x,clientY:100+y,pointerId:26});
  for(let step=1;step<=60;step++)canvas.emit('pointermove',{clientX:400+x+step*.5,clientY:100+y,pointerId:26});
  assert(ball.vx>20);
  canvas.emit('pointerup',{pointerId:26});assert.equal(h.api.getState().player.action,'idle');
});

test('AI supply selection and secondary pointers cannot take the opponent ball during a drag',async()=>{
  const h=await interferenceHarness(),canvas=h.node('neko-court'),ball=h.nekoLane.ball;
  canvas.emit('pointerdown',{clientX:400+ball.x,clientY:100+ball.y,pointerId:27});
  assert.equal(h.nekoLane.heldBall,ball);
  assert(h.api.shootNeko());assert.equal(ball.flying,false);assert(ball.held);
  assert.equal(h.nekoLane.heldBall,ball);assert(h.nekoLane.aim);
  canvas.emit('pointerup',{pointerId:28});assert(ball.held);
  canvas.emit('pointercancel',{pointerId:28});assert(ball.held);
  canvas.emit('pointermove',{clientX:400+ball.x+20,clientY:100+ball.y-100,pointerId:27});
  canvas.emit('pointerup',{pointerId:27});assert.equal(ball.held,false);assert(ball.flying);assert.equal(ball.owner,'player');
});

for(const distance of [5,10,17])test(`a ${distance}-pixel short player drag releases the held ball as a light throw`,async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),ball=h.playerLane.ball;
  canvas.emit('pointerdown',{clientX:80+ball.x,clientY:100+ball.y,pointerId:31});
  canvas.emit('pointermove',{clientX:80+ball.x,clientY:100+ball.y-distance,pointerId:31});
  canvas.emit('pointerup',{clientX:80+ball.x,clientY:100+ball.y-distance,pointerId:31});
  assert(ball.flying);assert(ball.vy<0);assert.equal(h.api.getState().player.attempts,1);
});

test('a quick release uses its final position even when no pointermove arrived',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),ball=h.playerLane.ball;
  canvas.emit('pointerdown',{clientX:80+ball.x,clientY:100+ball.y,pointerId:32});
  canvas.emit('pointerup',{clientX:80+ball.x+6,clientY:100+ball.y-8,pointerId:32});
  assert(ball.flying);assert(ball.vx>0);assert(ball.vy<0);
  assert.equal(h.api.getState().player.attempts,1);
});

test('real pointer timestamps preserve a clear late sideways finish on either outer pool ball',async()=>{
  for (const side of ['player', 'neko']) for (const coalesced of [false, true]) {
    const h=await interferenceHarness(), lane=side==='player'?h.playerLane:h.nekoLane;
    const canvas=h.node(side+'-court'),ball=lane.ballPool[side==='player'?1:2];
    const sign=side==='player'?1:-1, left=side==='player'?80:400;
    const start={x:left+ball.x,y:100+ball.y};
    const emit=(type,dx,dy)=>canvas.emit(type,{clientX:start.x+sign*dx,clientY:start.y+dy,
      pointerId:71,timeStamp:h.window.performance.now()});
    emit('pointerdown',0,0);
    h.advance(24);emit('pointermove',-24,-70);
    h.advance(28);
    const middle={clientX:start.x-sign*8,clientY:start.y-93,timeStamp:h.window.performance.now()};
    if (!coalesced) emit('pointermove',-8,-93);
    h.advance(26);
    if (coalesced) canvas.emit('pointermove',{clientX:start.x+sign*12,clientY:start.y-117,
      pointerId:71,timeStamp:h.window.performance.now(),getCoalescedEvents:()=>[middle]});
    emit('pointerup',12,-117);
    assert(ball.flying);
    assert(sign*ball.flight.vx>.06, 'the release event carries the final diagonal into flight');
    assert.equal(h.api.getState().player.attempts,1);
    assert.equal(lane.aimMotion,null);
    assert.equal(lane.heldBall,null);
  }
});

test('a click with only tiny pointer jitter still does not throw accidentally',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),ball=h.playerLane.ball;
  canvas.emit('pointerdown',{clientX:80+ball.x,clientY:100+ball.y,pointerId:33});
  canvas.emit('pointerup',{clientX:80+ball.x+1,clientY:100+ball.y-1,pointerId:33});
  assert(!ball.flying);assert.equal(h.api.getState().player.attempts,0);
  assert.equal(h.playerLane.heldBall,null);
});

test('an off-centre grab without a drag does not accidentally launch',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),ball=h.playerLane.ball;
  canvas.emit('pointerdown',{clientX:80+ball.x+ball.r,clientY:100+ball.y,pointerId:34});
  canvas.emit('pointerup',{clientX:80+ball.x+ball.r,clientY:100+ball.y,pointerId:34});
  assert(!ball.flying);assert.equal(h.api.getState().player.attempts,0);
});

test('a short drag ending at the ball centre uses the actual drag direction',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court'),ball=h.playerLane.ball;
  canvas.emit('pointerdown',{clientX:80+ball.x,clientY:100+ball.y+10,pointerId:35});
  canvas.emit('pointerup',{clientX:80+ball.x,clientY:100+ball.y,pointerId:35});
  assert(ball.flying);assert(ball.vy<0);assert.equal(h.api.getState().player.attempts,1);
});

for(const kind of ['pointercancel','lostpointercapture','blur'])test(`an opponent-ball aim cleans up on ${kind} and permits the next own-ball grab`,async()=>{
  const h=await interferenceHarness(),canvas=h.node('neko-court'),ball=h.nekoLane.ball;
  canvas.emit('pointerdown',{clientX:400+ball.x,clientY:100+ball.y,pointerId:36});
  canvas.emit('pointermove',{clientX:400+ball.x+20,clientY:100+ball.y-100,pointerId:36});
  (kind==='blur'?h.window:h.document).emit(kind,{pointerId:36});
  assert.equal(h.nekoLane.heldBall,null);assert.equal(ball.held,false);assert(!ball.flying);
  const own=h.playerLane.ball;
  h.node('player-court').emit('pointerdown',{clientX:80+own.x,clientY:100+own.y,pointerId:37});
  assert.equal(h.playerLane.heldBall,own);
});

test('a short upward drag from below an opponent ball aims up and throws that ball',async()=>{
  const h=await interferenceHarness(),canvas=h.node('neko-court'),ball=h.nekoLane.ball;
  const y=100+ball.y+18;
  canvas.emit('pointerdown',{clientX:400+ball.x,clientY:y,pointerId:38});
  canvas.emit('pointermove',{clientX:400+ball.x,clientY:y-5,pointerId:38});
  assert(h.nekoLane.getAimTelemetry().power>0);assert.equal(h.nekoLane.getAimTelemetry().angle,90);
  canvas.emit('pointerup',{clientX:400+ball.x,clientY:y-5,pointerId:38});
  assert(ball.flying);assert(ball.vy<0);assert(ball.flight.vDepth>0);assert.equal(ball.owner,'player');
  assert.equal(h.api.getState().player.attempts,1);
});

test('downward cancellation and low focus leave the held ball in place without charging a shot',async()=>{
  const h=await harness();h.api.test.prepareIsolatedCrossTest();h.advance(1);
  const canvas=h.node('player-court');
  let ball=h.playerLane.ball;
  let x=80+ball.x,y=100+ball.y;
  canvas.emit('pointerdown',{clientX:x,clientY:y,pointerId:40});
  canvas.emit('pointermove',{clientX:x,clientY:y+60,pointerId:40});
  assert.equal(h.playerLane.getAimTelemetry().power,0);
  canvas.emit('pointerup',{clientX:x,clientY:y+60,pointerId:40});
  assert(!ball.flying);assert.equal(h.api.getState().player.focus,100);
  assert.equal(h.api.getState().player.attempts,0);

  for(let i=0;i<3;i++)assert(h.api.shootPlayer(0,180));
  h.advance(960);
  for(let i=0;i<2;i++)assert(h.api.shootPlayer(0,180));
  ball=h.playerLane.readyForLaunch();x=80+ball.x;y=100+ball.y;
  canvas.emit('pointerdown',{clientX:x,clientY:y,pointerId:41});
  canvas.emit('pointermove',{clientX:x,clientY:y-50,pointerId:41});
  h.advance(20);
  const focus=h.api.getState().player.focus;
  assert(focus<18);assert.equal(h.node('shot-hint').hidden,false);
  assert.equal(h.node('shot-hint').textContent,'等上方的体力条回一点，就能再投。');
  canvas.emit('pointerup',{clientX:x,clientY:y-50,pointerId:41});
  assert(!ball.flying);assert.equal(h.api.getState().player.focus,focus);
  assert.equal(h.api.getState().player.attempts,5);
});

test('a player-thrown opponent ball is credited to the player once when it scores',async()=>{
  const h=await interferenceHarness(),lane=h.nekoLane,canvas=h.node('neko-court'),ball=lane.ball;
  const hoop=lane.getHoopPose();
  const dx=200*(hoop.x-ball.x)/(ball.y-hoop.rimY);
  canvas.emit('pointerdown',{clientX:400+ball.x,clientY:100+ball.y,pointerId:39});
  canvas.emit('pointerup',{clientX:400+ball.x+dx,clientY:100+ball.y-200,pointerId:39});
  h.advance(2400);
  assert.equal(h.api.getState().player.makes,1);assert(h.api.getState().player.score>=2);
  assert.equal(h.api.getState().neko.makes,0);assert.equal(h.api.getState().neko.score,0);
});

test('a normal player basket scores the same from either tray', async () => {
  for (const side of ['player', 'neko']) {
    const h = await interferenceHarness();
    const lane = side === 'player' ? h.playerLane : h.nekoLane;
    const ball = lane.ballPool[0];
    const canvas = lane.canvas;
    const { left, top } = canvas.getBoundingClientRect();
    const { x, y } = ball;
    const hoop = lane.getHoopPose();
    const dx = 200 * (hoop.x - x) / (y - hoop.rimY);
    canvas.emit('pointerdown', { clientX:left + x, clientY:top + y, pointerId:1 });
    canvas.emit('pointermove', { clientX:left + x + dx, clientY:top + y - 200, pointerId:1 });
    canvas.emit('pointerup', { pointerId:1 });
    assert(ball.flying);
    lane.scoreBall(ball, data => lane.onScore(data, ball));
    lane.beginBallReturn(ball);
    const { player, neko } = h.api.getState();
    assert.equal(player.score, 3);
    assert.equal(player.makes, 1);
    assert.equal(player.attempts, 1);
    assert.equal(player.combo, 1);
    assert.equal(neko.score, 0);
  }
});

test('returning prank and collision balls do not interrupt a combo', async () => {
  const h = await interferenceHarness();
  const lane = h.nekoLane;
  lane.onScore({ clean:true, owner:'neko' }, lane.ball);
  const prank = lane.launchPoolBall(0, -200, { owner:'neko' });
  assert(prank);
  const bumped = lane.readyForLaunch();
  lane.liftPoolBall(bumped, prank);
  lane.beginBallReturn(prank);
  lane.beginBallReturn(bumped);
  const { neko } = h.api.getState();
  assert.equal(neko.combo, 1);
  assert.equal(neko.feverCharge, 1);
  assert.equal(neko.shotMissStreak, 0);
});

test('a genuine miss is charged once to its shooter even after possession changes', async () => {
  for (const side of ['player', 'neko']) {
    const h = await interferenceHarness();
    for (const lane of [h.playerLane, h.nekoLane]) {
      lane.onScore({ clean:true, owner:lane.side }, lane.ball);
    }
    const lane = side === 'player' ? h.playerLane : h.nekoLane;
    const otherSide = side === 'player' ? 'neko' : 'player';
    assert(side === 'player' ? h.api.shootPlayer(0, -300) : h.api.shootNeko());
    const shot = lane.ballPool.find(ball => ball.flying);
    assert(shot);
    shot.owner = otherSide;
    const misses = [];
    const onMiss = lane.onMiss;
    lane.onMiss = data => { misses.push(data.owner); onMiss(data); };
    lane.beginBallReturn(shot);
    lane.beginBallReturn(shot);
    assert.deepEqual(misses, [side]);
    const state = h.api.getState();
    assert.equal(state[side].combo, 0);
    assert.equal(state[otherSide].combo, 1);
    assert.equal(state.neko.shotMissStreak, side === 'neko' ? 1 : 0);
  }
});
