const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { execFileSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const physicsPath = path.join(root, 'static/game/games/air_basketball/physics.js');
const courtPath = path.join(root, 'static/game/games/air_basketball/shared-court.js');
global.window = { devicePixelRatio:1 };
global.Image = class { constructor() { this.complete = true; this.naturalWidth = 1000; this.naturalHeight = 1000; } };

function canvas(width = 320, height = 500) {
  const images = [];
  const context = new Proxy({
    drawImage:(...args) => images.push(args),
    createLinearGradient:() => ({ addColorStop() {} }),
    createRadialGradient:() => ({ addColorStop() {} })
  }, { get:(target, key) => key in target ? target[key] : () => {} });
  return { images, getContext:() => context, getBoundingClientRect:() => ({ width, height }) };
}

async function setup(width = 320, height = 500) {
  const { ShotLane } = await import(pathToFileURL(physicsPath));
  const { SharedCourt } = await import(pathToFileURL(courtPath));
  const scores = [];
  const lanes = ['player', 'neko'].map(side => new ShotLane({
    canvas:canvas(width, height), side, onScore:event => scores.push({ lane:side, ...event }),
    onGuestScore:event => scores.push({ lane:side, ...event })
  }));
  const court = new SharedCourt(...lanes);
  const tick = dt => { court.advance(dt, true); lanes.forEach(lane => lane.update(dt, true)); court.collideBalls(); };
  return { lanes, court, scores, tick };
}

function baselineClass() {
  const baseline = execFileSync('git', ['show', '4177047b7:static/game/games/air_basketball/physics.js'], { cwd:root, encoding:'utf8' });
  return new Function(baseline.replaceAll('import.meta.url', JSON.stringify(pathToFileURL(physicsPath).href))
    .replace('export const clamp', 'const clamp').replace('export class ShotLane', 'class ShotLane')
    + '\nreturn ShotLane;')();
}

function dragTowardHoop(lane, ball, length, offset = 0) {
  const hoop = lane.getHoopPose();
  const angle = Math.atan2(hoop.x - ball.x, ball.y - hoop.rimY) + offset * Math.PI / 180;
  lane.beginAim({ x:ball.x, y:ball.y });
  lane.moveAim({ x:ball.x + length * Math.sin(angle), y:ball.y - length * Math.cos(angle) });
}

test('both actors use the same fixed hoop despite personal stages, with shared interference', async () => {
  const { lanes:[player, neko], court } = await setup();
  const initialWidth = player.getHoopPose().rimW;
  for (let stage = 1; stage <= 4; stage++) {
    neko.setStage(stage); player.hoopOffset = 8; neko.hoopOffset = -3;
    for (const time of [0, .33, 1.1, 4.6]) {
      court.clock = time;
      const a = player.getHoopPose(), b = neko.getHoopPose();
      assert.equal(a.x, b.x + player.width);
      assert.equal(a.rimY, b.rimY); assert.equal(a.rimW, b.rimW);
      assert.equal(a.x,court.width/2+5);
      assert.equal(a.rimW,initialWidth);
      const assemblySize=a.boardRimW*1.84;
      const top=a.rimY-assemblySize*.595;
      assert(top>0,'the raised backboard stays inside the scene');
      assert(top+assemblySize<player.height*.54,'the net clears the rear floor');
    }
  }
});

test('1.0 drag mapping is unchanged, regardless of shared-hoop position', async () => {
  const Baseline = baselineClass();
  const old = new Baseline({ canvas:canvas(), side:'player' });
  const { lanes:[player] } = await setup();
  for (const [dx, dy] of [[0,-20], [40,-100], [100,-180], [-70,-150], [300,-70], [50,90]]) {
    const point = { x:player.ball.x + dx, y:player.ball.y + dy };
    assert.deepEqual(player.shotVector(point), old.shotVector(point));
    assert.deepEqual(player.getAimTelemetry(point), old.getAimTelemetry(point));
  }
});

test('unobstructed 1.0 flight retains gravity, damping and the original supply gate', async () => {
  const Baseline = baselineClass(), old = new Baseline({ canvas:canvas(), side:'player' });
  const { lanes:[player], tick } = await setup();
  old.shoot(30,-350); player.shoot(30,-350);
  assert.equal(player.shoot(0,-700), false);
  for (let i=0; i<24; i++) {
    old.update(1/240,true); tick(1/240);
    for (const key of ['x','y','vx','vy']) assert.equal(player.ball[key], old.ball[key]);
  }
});

test('the former centre seam neither reflects nor transfers or respawns a ball', async () => {
  const { lanes:[player], tick } = await setup();
  const ball = player.ball;
  Object.assign(ball, { flying:true, x:player.width-5, y:350, vx:400, vy:0 });
  player.onCross = () => { throw new Error('No cross-cabinet transfer in a shared court'); };
  tick(.02);
  assert.equal(player.ball, ball); assert(ball.x>player.width); assert(ball.vx>0);
  assert.equal(ball.inTransit,false);
});

test('both inventories reflect at the same real outer edges, with original restitution', async () => {
  const { lanes, court } = await setup();
  for (const lane of lanes) for (const edge of ['left','right']) {
    const bounds = court.bounds(lane), b = lane.ball;
    Object.assign(b, { x:edge==='left'?bounds.left-30:bounds.right+30, vx:edge==='left'?-500:500, vy:-123 });
    lane.containOuterEdge(b);
    assert.equal(b.x, edge==='left'?bounds.left+b.r:bounds.right-b.r);
    assert.equal(b.vx, edge==='left'?380:-380); assert.equal(b.vy,-123);
  }
});

test('top bounce remains at the visible top frame; no lower perspective-net barrier', async () => {
  const { lanes:[player] } = await setup();
  const b = player.ball;
  Object.assign(b, { y:120, vy:-500 }); player.containTopEdge(b);
  assert.equal(b.y,120); assert.equal(b.vy,-500);
  b.y=-30; player.containTopEdge(b);
  assert.equal(b.y,b.r); assert.equal(b.vy,370);
});

test('the avatar-side exit permits player interference without opening the whole wall', async () => {
  const { lanes:[player], court } = await setup();
  court.rightOpening = { top:200, bottom:480 };
  let exits = 0; court.onExit = () => exits++;
  const attempt = (y, owner) => {
    const b = { x:court.width+1, y, r:20, vx:500, vy:0, owner };
    player.containSharedEdges(b); return b;
  };
  assert.equal(attempt(100,'player').vx,-380);
  assert.equal(attempt(300,'neko').vx,-380);
  const ball=attempt(300,'player'); assert.equal(ball.vx,500); assert(ball.allowOuterExit);
  assert.equal(exits,1);
});

for (const fps of [15,30,60,144]) test(`both actors can score in the shared hoop at ${fps} fps, once per ball`, async () => {
  for (const index of [0,1]) {
    const { lanes, scores, tick } = await setup();
    const lane = lanes[index], ball=lane.ball, hoop=lane.getHoopPose(), flight=1.2;
    const damping=-60*Math.log(.998), travel=(1-Math.exp(-damping*flight))/damping;
    lane.shoot((hoop.x-ball.x)/travel,(hoop.rimY-8-ball.y-.5*720*flight*flight)/flight);
    // Match the production bounded substeps, including low-frame-rate play.
    for (let t=0;t<2;t+=1/fps) {
      const count=Math.min(8,Math.ceil((1/fps)/.033));
      for(let i=0;i<count;i++) tick(1/fps/count);
    }
    assert.equal(scores.length,1,JSON.stringify(scores));
    assert.equal(scores[0].owner,lane.side);
  }
});

test('contact across the centre uses the same physical coordinates and changes possession', async () => {
  const { lanes:[player,neko], court } = await setup();
  const a=player.ball,b=neko.ball;
  Object.assign(a,{ x:310,y:400,vx:500,vy:0,flying:true });
  Object.assign(b,{ x:10,y:400,vx:-50,vy:0,flying:true });
  court.collideBalls();
  assert(a.vx<0); assert(b.vx>0); assert.equal(b.owner,'player');
  assert(Math.abs((b.x+player.width)-a.x)>=a.r+b.r-.001);
});

test('the existing automatic shot solver clears the diagonal near lip at ordinary window shapes', async () => {
  const random = Math.random;
  try {
    Math.random = () => .5;
    for (const [width,height] of [[240,400],[320,500],[497,530],[320,700]]) {
      const { lanes:[,neko], scores, tick } = await setup(width,height);
      neko.releaseAutoShot(1);
      for(let t=0;t<2;t+=1/60) tick(1/60);
      assert.equal(scores.filter(event=>event.owner==='neko').length,1,`${width}x${height}`);
      assert.equal(neko.ball.flying,false);
    }
  } finally { Math.random = random; }
});

test('both canvas halves render one continuous source image and one shared hoop', async () => {
  const { lanes, court } = await setup();
  lanes.forEach(lane => lane.enableBallPool(3));
  lanes.forEach(lane => lane.draw());
  const background = lanes.map(lane => lane.canvas.images.find(args=>args[0]===lane.sharedBackgroundImage));
  assert.equal(background[0][7],court.width); assert.equal(background[1][7],court.width);
  assert(Math.abs(background[0][5])<1e-9); assert.equal(background[1][5],-lanes[0].width);
  const boards=lanes.map(lane=>lane.canvas.images.find(args=>args[0]===court.assembly.boardImage));
  assert.equal(boards[0][5],boards[1][5]+lanes[0].width);
  const rims=lanes.map(lane=>lane.canvas.images.find(args=>args[0]===court.assembly.rimImage));
  assert.equal(rims[0][5],rims[1][5]+lanes[0].width);
  assert.equal(rims[0][7],lanes[0].getHoopPose().rimW);
  for (const lane of lanes) {
    const front = lane.canvas.images.findIndex(args=>args[0]===court.frontLayer.image);
    const resting = lane.canvas.images.findLastIndex(args=>court.lanes.some(source=>args[0]===source.ballImage));
    assert(resting>=0 && front>resting,'front rail occludes resting balls on both halves');
  }
  assert(lanes.every(lane=>!lane.canvas.images.some(args=>args[0]===lane.hoopImage)),'shared view never draws the old combined sprite');
  for (const args of background) {
    assert.equal(args[1],0); assert.equal(args[2],0);
    assert.equal(args[3],args[0].naturalWidth); assert.equal(args[4],args[0].naturalHeight);
  }
});

test('rim reduction leaves the backboard and basketball dimensions unchanged and narrows scoring',async()=>{
  const {lanes:[player],court}=await setup();
  court.rimScale=1;
  const original=player.getHoopPose(), board=court.assembly.boardRect(original), ballRadius=player.ball.r;
  court.rimScale=.9;
  const reduced=player.getHoopPose();
  assert.equal(reduced.rimW,original.rimW*.9);
  assert.deepEqual(court.assembly.boardRect(reduced),board);
  assert.equal(player.ball.r,ballRadius);
  player.collideRim=()=>{};
  let scored=0;
  Object.assign(player.ball,{x:reduced.x+original.rimW*.36,y:reduced.rimY+1,vy:100,scored:false});
  player.collideWithCourt(player.ball,reduced.rimY-1,()=>scored++);
  assert.equal(scored,0,'a position accepted by the old larger mouth is outside the new one');
  player.ball.x=reduced.x;
  player.collideWithCourt(player.ball,reduced.rimY-1,()=>scored++);
  assert.equal(scored,1);
});

test('a shared hoop counts only descending balls that fit inside the visible mouth', async () => {
  const {lanes:[player]} = await setup();
  player.enableBallPool(3);
  const hoop = player.getHoopPose();
  const radius = player.ball.r;
  const attempt = (offset, r = radius) => {
    let points = 0;
    const ball = {x:hoop.x+offset, y:hoop.rimY+1, vx:0, vy:100, r, scored:false};
    player.collideWithCourt(ball, hoop.rimY-1, () => points++);
    return points;
  };
  assert.equal(attempt(0), 1, 'a centered regulation ball scores');
  assert.equal(attempt(hoop.rimW * .23), 0, 'a center inside the rim is insufficient if the ball overlaps the rim');
  assert.equal(attempt(0, hoop.rimW * .45), 0, 'a ball wider than the mouth cannot score even when centered');
});

test('short spatial shots return from in front of the hoop and shrink with distance', async () => {
  const {lanes:[player],court,scores,tick} = await setup();
  player.enableBallPool(3);
  const shot = player.launchPoolBall(0, -180, {spatial:{vx:0,vy:-180}});
  const startingRadius = shot.r;
  let farthest = 0;
  for (let frame=0; frame<120 && shot.flying; frame++) {
    tick(1/120);
    if (shot.flight) farthest = Math.max(farthest, shot.flight.depth);
  }
  assert.equal(shot.poolState,'returning');
  assert(farthest > .10 && farthest < court.hoopDepth * .5);
  assert(shot.r < startingRadius);
  assert(shot.y > player.getHoopPose().rimY + player.height * .35);
  assert.equal(scores.length,0);
});

test('a ball descending over the projected hoop must also be at its depth to score', async () => {
  const {lanes:[player],court,scores,tick} = await setup();
  player.enableBallPool(3);
  const shot = player.launchPoolBall(0, -300, {spatial:{vx:0,vy:-300}});
  const pose = player.getHoopPose();
  Object.assign(shot.flight,{x:0, depth:.35, vDepth:0, vx:0, vHeight:-.5,
    height:(court.floorY(player,.35)-pose.rimY+1)/(court.width/(1+.48*.35))});
  court.projectFlight(player,shot);
  assert(shot.y < pose.rimY);
  tick(1/60);
  assert(shot.y > pose.rimY);
  assert.equal(scores.length,0);
});

test('manual swishes tolerate nearby drag directions and lengths from each pickup position', async () => {
  for (const slot of [0, 1, 2]) {
    for (const offset of [-1, 0, 1]) {
      for (const length of [180, 190, 200]) {
        const { lanes:[player], scores, tick } = await setup(480, 720);
        player.enableBallPool(3);
        const ball = player.ballPool[slot];
        dragTowardHoop(player, ball, length, offset);
        assert(player.releaseAim());
        for (let frame = 0; frame < 144 && ball.flying; frame++) tick(1 / 60);
        assert.deepEqual(scores, [{ lane:'player', owner:'player', clean:true }],
          `slot ${slot}, ${length}px drag with ${offset}° offset`);
      }
    }
  }
});

test('regular manual strengths progress from a short ball to a swish, a rim make and a bank', async () => {
  for (const fps of [30, 60, 120]) {
    for (const slot of [0, 1, 2]) {
      for (const [length, expected, contact] of [[90,0], [180,3], [234,2,'rim'], [270,2,'board']]) {
        const { lanes:[player], court, scores, tick } = await setup(480, 720);
        player.enableBallPool(3);
        const ball = player.ballPool[slot];
        dragTowardHoop(player, ball, length);
        assert(player.releaseAim());
        const flight = ball.flight;
        let struckBoard = false;
        court.onBoardImpact = () => { struckBoard = true; };
        for (let frame = 0; frame < fps * 6 && ball.flying; frame++) tick(1 / fps);
        assert.equal(ball.poolState, 'returning');
        assert.equal(struckBoard, contact === 'board');
        if (contact === 'rim') assert(flight.hoopContacts?.rim);
        assert.deepEqual(scores, expected
          ? [{ lane:'player', owner:'player', clean:expected === 3 }] : [],
        `${fps} fps, slot ${slot}, ${length}-pixel drag`);
      }
    }
  }
});

test('ordinary increases in drag length advance the shot through the hoop opening', async () => {
  for (const side of [0, 1]) {
    for (const slot of [0, 1, 2]) {
      const depths = [];
      for (const length of [162, 198, 234]) {
        const { lanes, court } = await setup(480, 720);
        const lane = lanes[side];
        lane.enableBallPool(3);
        const ball = lane.ballPool[slot];
        dragTowardHoop(lane, ball, length);
        assert(lane.releaseAim('player'));
        const { height, vHeight, vDepth } = ball.flight;
        const time = (vHeight + Math.sqrt(vHeight ** 2 - 2 * 1.1 * (court.hoopHeight(lane) - height))) / 1.1;
        depths.push(vDepth * time);
      }
      assert(depths.every(depth => Math.abs(depth - .90) < .025), `range at hoop height: ${depths}`);
      assert(depths[1] - depths[0] >= .007 && depths[2] - depths[1] >= .007,
        `equal increases in strength must advance the shot: ${depths}`);
    }
  }
});

test('ordinary drags with small directional errors usually score from each pickup position', async () => {
  for (const side of [0, 1]) {
    for (const slot of [0, 1, 2]) {
      let makes = 0;
      for (const offset of [-3, 0, 3]) {
        for (const length of [180, 200, 220, 240]) {
          const { lanes, scores, tick } = await setup(480, 720);
          const lane = lanes[side];
          lane.enableBallPool(3);
          const ball = lane.ballPool[slot];
          dragTowardHoop(lane, ball, length, offset);
          assert(lane.releaseAim('player'));
          for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 60);
          assert(scores.length <= 1);
          if (scores.length) assert.equal(scores[0].owner, 'player');
          makes += scores.length;
        }
      }
      assert(makes >= 8, `side ${side}, slot ${slot}: ${makes}/12 makes`);
    }
  }
});

test('left, centre and right drags strike visibly different parts of the board from shifted pickups', async () => {
  for (const shift of [-30, 0, 30]) {
    const contacts = [];
    for (const offset of [-6, 0, 6]) {
      const { lanes:[lane], tick } = await setup(480, 720);
      lane.enableBallPool(1);
      const ball = lane.ballPool[0];
      ball.x += shift;
      dragTowardHoop(lane, ball, 302.4, offset);
      assert(lane.releaseAim());
      for (let frame = 0; frame < 240 && ball.flying && !ball.flight.hoopContacts?.board; frame++) {
        tick(1 / 60);
      }
      const hit = ball.flight?.hoopContacts?.board;
      assert(hit, `pickup shift ${shift}px, aim offset ${offset}°`);
      contacts.push(hit.x);
    }
    assert(contacts[1] - contacts[0] > .025 && contacts[2] - contacts[1] > .025,
      `board impacts should spread across the aim directions: ${contacts}`);
  }
});

test('rough swipes toward the visible hoop can score from either pool', async () => {
  for (const side of [0, 1]) {
    for (const slot of [0, 1, 2]) {
      const { lanes, scores, tick } = await setup(480, 720);
      const lane = lanes[side];
      lane.enableBallPool(3);
      const ball = lane.ballPool[slot], hoop = lane.getHoopPose();
      const direction = Math.atan2(hoop.x - ball.x, ball.y - hoop.rimY);
      lane.beginAim({ x:ball.x, y:ball.y });
      lane.moveAim({ x:ball.x + 220 * Math.sin(direction), y:ball.y - 220 * Math.cos(direction) });
      assert(lane.releaseAim('player'));
      for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 60);
      assert.equal(scores.length, 1, `${lane.side}, slot ${slot}`);
      assert.equal(scores[0].owner, 'player');
    }
  }
});

test('firm diagonal swipes can bank from every pickup position below full power', async () => {
  for (const fps of [30, 120]) {
    for (const side of [0, 1]) {
      for (const slot of [0, 1, 2]) {
        for (const length of [288, 302.4, 316.8]) {
          for (const offset of [-6, 0, 6]) {
            const { lanes, scores, tick } = await setup(480, 720);
            const lane = lanes[side];
            lane.enableBallPool(3);
            const ball = lane.ballPool[slot];
            dragTowardHoop(lane, ball, length, offset);
            assert(lane.releaseAim('player'));
            let hitBoard = false;
            for (let frame = 0; frame < fps * 6 && ball.flying; frame++) {
              tick(1 / fps);
              hitBoard ||= Boolean(ball.flight?.hoopContacts?.board);
            }
            const shot = `${fps} fps, ${lane.side} slot ${slot}, ${length}px at ${offset}° offset`;
            assert(hitBoard, `missed the board: ${shot}`);
            assert.equal(ball.flying, false, shot);
            if (offset === 0 || scores.length) {
              assert.deepEqual(scores, [{ lane:lane.side, owner:'player', clean:false }], shot);
            }
          }
        }
      }
    }
  }
});

test('the same half-power drag reaches the hoop from inner and outer pickup positions', async () => {
  for (const [width, height] of [[480, 720], [240, 360]]) {
    let reference;
    for (const side of [0, 1]) {
      for (const slot of [0, 1, 2]) {
        const { lanes, scores, tick } = await setup(width, height);
        const lane = lanes[side];
        lane.enableBallPool(3);
        const ball = lane.ballPool[slot];
        dragTowardHoop(lane, ball, 180);
        assert.equal(lane.getAimTelemetry().power, 50);
        assert(lane.releaseAim('player'));
        const { vDepth, vHeight } = ball.flight;
        reference ??= { vDepth, vHeight };
        assert(Math.abs(vDepth - reference.vDepth) < 1e-9, 'the outer drag should reach just as far');
        assert(Math.abs(vHeight - reference.vHeight) < 1e-9, 'the outer drag should lift just as high');
        for (let frame = 0; frame < 240 && ball.flying; frame++) tick(1 / 60);
        assert.equal(scores.length, 1, `${width}px lane, side ${side}, slot ${slot}`);
        assert.equal(scores[0].owner, 'player');
      }
    }
  }
});

test('short throws stay short and deliberate away/sideways throws keep their launch direction', async () => {
  for (const [dx, rise] of [[28,90], [-100,165], [240,50], [200,100]]) {
    const { lanes:[player], scores, tick } = await setup(480, 720);
    player.enableBallPool(3);
    const ball = player.ballPool[0];
    player.beginAim({ x:ball.x, y:ball.y });
    player.moveAim({ x:ball.x + dx, y:ball.y - rise });
    assert(player.releaseAim());
    assert.equal(Math.sign(ball.flight.vx), Math.sign(dx), 'the launch keeps its lateral direction');
    for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 60);
    if (rise === 90) assert.deepEqual(scores, [], 'a short toss still falls before the basket');
  }
});

test('a clear late diagonal on a short quick flick retains some lateral intent without adding power or lift', async () => {
  for (const width of [180, 480]) for (const side of [0, 1]) {
    const trials = [];
    for (const curve of [false, true]) {
      const { lanes, court, tick, scores } = await setup(width, width * 1.5);
      lanes.forEach(lane => lane.enableBallPool(3));
      const lane = lanes[side], ball = lane.ballPool[side ? 2 : 1], sign = side ? -1 : 1;
      const start = { x:ball.x, y:ball.y };
      lane.beginAim(start, 1000);
      const path = curve ? [[24,-24,-70], [52,-8,-93], [78,12,-117]] : [[78,12,-117]];
      for (const [time, x, y] of path) lane.moveAim({
        x:start.x + sign * x / lane.inputScale.x, y:start.y + y / lane.inputScale.y
      }, 1000 + time);
      const shot = lane.shotVector();
      assert(lane.releaseAim('player'));
      const angle = Math.atan2(sign * shot.vx, -shot.vy) * 180 / Math.PI;
      trials.push({ power:shot.power, angle, ...ball.flight });
      if (curve) assert(sign * ball.flight.vx > .07, 'the clear inward finish survives world projection');
      for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 60);
      assert.equal(scores.length, 0, 'recognizing direction does not turn a short throw into a make');
    }
    const [straight, curved] = trials;
    assert(curved.angle - straight.angle > 6 && curved.angle - straight.angle <= 12,
      'the correction is small and follows the real gesture');
    assert.equal(curved.power, straight.power);
    assert(Math.abs(curved.vDepth - straight.vDepth) < 1e-12);
    assert(Math.abs(curved.vHeight - straight.vHeight) < 1e-12);
  }
});

test('straight quick throws, slow curves and release jitter retain the accepted drag vector', async () => {
  const paths = [
    [[20,12,-40], [45,24,-80], [70,36,-120]],
    [[20,-12,-40], [45,-24,-80], [70,-36,-120]],
    [[20,0,-40], [45,0,-80], [70,0,-120]],
    [[200,-24,-70], [400,-8,-93], [600,12,-117]],
    [[20,0,-40], [45,0,-80], [70,2,-82]],
    [[20,75,-10], [45,150,-20], [70,200,-30]],
    [[20,0,20], [45,0,40], [70,0,60]]
  ];
  for (const path of paths) {
    const { lanes:[lane] } = await setup(480, 720);
    lane.enableBallPool(3);
    const ball = lane.ballPool[1], start = { x:ball.x, y:ball.y };
    const [, x, y] = path.at(-1), end = { x:start.x + x, y:start.y + y };
    lane.beginAim(start);
    const accepted = lane.shotVector(end);
    lane.cancelAim();
    lane.beginAim(start, 0);
    for (const [time, dx, dy] of path) lane.moveAim({ x:start.x + dx, y:start.y + dy }, time);
    assert.deepEqual(lane.shotVector(), accepted);
    lane.cancelAim();
    assert.equal(lane.aimMotion, null, 'a cancelled or released flick leaves no input history');
  }
});

test('late-direction weighting fades for slower grabs and a stopped hand loses its flick', async () => {
  const turns = [];
  for (const duration of [80, 130, 160, 179, 180]) {
    const { lanes:[lane] } = await setup(480, 720);
    lane.enableBallPool(3);
    const ball = lane.ballPool[1], start = { x:ball.x, y:ball.y };
    lane.beginAim(start, 0);
    for (const [time,x,y] of [[duration-54,-24,-70],[duration-26,-8,-93],[duration,12,-117]]) {
      lane.moveAim({ x:start.x+x, y:start.y+y }, time);
    }
    const raw = Math.atan2(12,117);
    const shot = lane.shotVector();
    turns.push(Math.atan2(shot.vx,-shot.vy)-raw);
    assert.equal(shot.power,Math.hypot(12,117)/360);
    lane.moveAim({ x:start.x+12, y:start.y-117 }, duration+80);
    assert(Math.abs(Math.atan2(lane.shotVector().vx,-lane.shotVector().vy)-raw)<1e-12,
      'holding still before releasing uses the ordinary drag direction');
  }
  assert(turns.every((turn,index)=>turn>=-1e-12&&(!index||turn<turns[index-1])));
  assert(turns.at(-1)<1e-12, 'the quick-gesture window closes smoothly');
});

test('identical screen drags keep the same power and trajectory below the minimum logical canvas size', async () => {
  let reference;
  for (const [width, height] of [[480,720], [320,480], [240,360], [191,286.5], [141,211.5]]) {
    const { lanes:[player] } = await setup(width, height);
    player.enableBallPool(3);
    const ball = player.ballPool[0];
    player.beginAim({ x:ball.x, y:ball.y });
    player.moveAim({ x:ball.x + 30 * player.width / width, y:ball.y - 180 * player.height / height });
    const vector = player.shotVector();
    const telemetry = player.getAimTelemetry();
    assert(player.releaseAim());
    const observed = { vx:vector.vx, vy:vector.vy, power:vector.power,
      depth:ball.flight.vDepth, rise:ball.flight.vHeight, angle:telemetry.angle };
    reference ||= observed;
    for (const key of Object.keys(observed)) {
      assert(Math.abs(observed[key] - reference[key]) < 1e-7, `${width}x${height}, ${key}`);
    }
    assert(telemetry.power >= 49 && telemetry.power <= 52, 'an ordinary drag stays around half power');
  }
});

test('full manual power requires a deliberate long screen drag and tiny jitter still cancels in a narrow field', async () => {
  const { lanes:[player] } = await setup(141,211.5);
  player.enableBallPool(3);
  for (const [distance, power] of [[90,25], [180,50], [270,75], [360,100], [420,100]]) {
    const ball = player.ballPool[0];
    player.beginAim({ x:ball.x, y:ball.y });
    player.moveAim({ x:ball.x, y:ball.y - distance * player.height / 211.5 });
    assert.equal(player.getAimTelemetry().power, power);
    player.cancelAim();
  }
  const ball = player.ballPool[0];
  player.beginAim({ x:ball.x, y:ball.y });
  player.moveAim({ x:ball.x, y:ball.y - 2 * player.height / 211.5 });
  assert.equal(player.releaseAim(), false);
  assert.equal(ball.flying, false);
});

test('normal manual directions retain roof clearance even at full power', async () => {
  for (const length of [180, 260, 360]) {
    for (const angle of [0, 30, 45]) {
      const { lanes:[player], court, tick } = await setup(480, 720);
      player.enableBallPool(3);
      const ball = player.ballPool[0], direction = angle * Math.PI / 180;
      player.beginAim({ x:ball.x, y:ball.y });
      player.moveAim({ x:ball.x + length * Math.sin(direction), y:ball.y - length * Math.cos(direction) });
      assert(player.releaseAim());
      const advance = court.advanceFlight.bind(court);
      let roofContacts = 0;
      court.advanceFlight = (lane, shot, dt) => {
        const segments = advance(lane, shot, dt);
        const front = lane.height * .937 / court.width;
        const back = lane.height * (.539 - 64 / 1086) * 1.48 / court.width;
        const slope = back - front;
        for (const { to } of segments) {
          const gap = (front + slope * to.depth - to.height) / Math.hypot(1, slope) - shot.flight.radius;
          if (Math.abs(gap) < 1e-4) roofContacts++;
        }
        return segments;
      };
      for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 60);
      assert.equal(roofContacts, 0, `${length}-pixel drag, ${angle} degrees from up`);
    }
  }
});

test('increasing screen drag changes arc height gradually and never reverses uncollided range', async () => {
  const { lanes:[player], court } = await setup(480,720);
  player.enableBallPool(3);
  let previousPeak, previousRange;
  for (let distance = 60; distance <= 360; distance++) {
    player.resetBallPool();
    const ball = player.ballPool[0];
    player.beginAim({ x:ball.x, y:ball.y });
    player.moveAim({ x:ball.x, y:ball.y - distance });
    assert(player.releaseAim());
    const f = ball.flight, gravity = 1.10;
    const peak = f.height + f.vHeight ** 2 / (2 * gravity);
    if (previousPeak !== undefined) {
      assert(peak >= previousPeak - 1e-9, `${distance}-pixel drag drops the apex`);
      assert(peak - previousPeak < f.radius * .3, `${distance}-pixel drag jumps too far in height`);
    }
    previousPeak = peak;
    const discriminant = f.vHeight ** 2 - 2 * gravity * (court.hoopHeight(player) - f.height);
    if (discriminant <= 0) continue;
    const seconds = (f.vHeight + Math.sqrt(discriminant)) / gravity;
    const range = seconds * f.vDepth;
    if (previousRange !== undefined) assert(range >= previousRange - 1e-9, `${distance}-pixel drag reverses range`);
    previousRange = range;
  }
});

async function spatialShot(side = 0) {
  const { lanes, court, scores, tick } = await setup(480, 720);
  const lane = lanes[side];
  lane.enableBallPool(3);
  const ball = lane.launchPoolBall(0, -300, { spatial:{ vx:0, vy:-300 } });
  const place = flight => {
    Object.assign(ball.flight, flight);
    court.projectFlight(lane, ball);
  };
  return { court, lane, ball, place, scores, tick };
}

test('ordinary manual arcs reach the rim quickly and peak near the backboard instead of the roof', async () => {
  for (const fps of [30, 60, 120]) {
    for (const rise of [180, 220, 240]) {
      const { lanes:[lane], court, scores, tick } = await setup(480, 720);
      lane.enableBallPool(3);
      const ball = lane.ballPool[0], pose = lane.getHoopPose();
      dragTowardHoop(lane, ball, rise);
      assert(lane.releaseAim());
      const f = ball.flight;
      const arrival = (f.vHeight + Math.sqrt(f.vHeight ** 2 - 2 * 1.10 * (court.hoopHeight(lane) - f.height))) / 1.10;
      assert(arrival > 1.3 && arrival < 1.52, `${rise}-pixel drag, arrival ${arrival}`);
      const diameterAtRim = f.radius * 2 * court.width / (1 + .48 * court.hoopDepth);
      let apex = ball.y;
      for (let frame = 0; frame < fps * 4 && ball.flying; frame++) {
        tick(1 / fps);
        if (!f.hoopContacts) apex = Math.min(apex, ball.y);
      }
      const clearance = (pose.rimY - apex) / diameterAtRim;
      assert(clearance > 1.5 && clearance < 2.4, `${rise}-pixel drag, peak ${clearance} diameters above rim`);
      assert.equal(scores.length, 1);
      assert.equal(scores[0].owner, 'player');
    }
  }
});

test('a board contact loses both normal and tangential speed without adding a score', async () => {
  const { court, lane, ball, place, scores } = await spatialShot();
  const { board } = court.hoopGeometry(lane);
  place({ x:0, depth:.978 - ball.flight.radius - .004, height:board.height,
    vx:.10, vDepth:.8, vHeight:.20 });
  court.advanceFlight(lane, ball, .01);
  assert(ball.hitRim);
  assert(ball.flight.vDepth > -.31 && ball.flight.vDepth < -.25, 'the board returns a softer bounce');
  assert(ball.flight.vx > .08 && ball.flight.vx < .09, 'a glancing hit also loses lateral speed');
  assert(ball.flight.vHeight > .14 && ball.flight.vHeight < .18, 'vertical sliding speed also loses energy');
  assert.equal(scores.length, 0);
});

test('light rim touches settle with little lift while a hard hit can still bounce away', async () => {
  const rebound = [];
  const motionSize = [];
  for (const closing of [.25, 1]) {
    const { court, lane, ball, place, scores } = await spatialShot();
    const { ring } = court.hoopGeometry(lane);
    place({ x:ring.x + ring.halfWidth, depth:court.hoopDepth,
      height:ring.height + ring.tube + ball.flight.radius + .002,
      vx:0, vDepth:0, vHeight:-closing });
    court.advanceFlight(lane, ball, .012);
    assert(ball.hitRim);
    assert.equal(scores.length, 0);
    rebound.push(Math.max(0, ball.flight.vHeight));
    assert(ball.flight.vHeight < closing * .50, 'even a hard hit sheds most of its normal energy');
    assert.equal(court.assembly.rimImpacts.length, 1);
    court.advance(.02, true);
    const motion = court.assembly.rimMotion();
    assert(motion.roll > 0, 'the rim tilts toward the struck side');
    motionSize.push(Math.hypot(motion.pitch, motion.roll));
    court.advance(.10, false);
    assert.deepEqual(court.assembly.rimMotion(), motion, 'pausing holds the feedback at its current pose');
    court.advance(.26, true);
    assert.equal(court.assembly.rimMotion(), null, 'the rim returns to its original pose');
  }
  assert(rebound[0] < .08, 'a light touch does not launch the ball back up');
  assert(rebound[1] > .35, 'hard collisions retain a visible rebound');
  assert(motionSize[0] > 0 && motionSize[1] > motionSize[0] * 2, 'the harder hit visibly moves the rim farther');
});

test('board feedback marks the real strike independently of scoring', async () => {
  for (const { length, offset, scoresExpected, rim } of [
    { length:288, offset:0, scoresExpected:1, rim:false },
    { length:288, offset:40, scoresExpected:1, rim:true },
    { length:288, offset:90, scoresExpected:0, rim:false },
    { length:200, offset:0, scoresExpected:1, rim:false }
  ]) {
    const { lanes:[lane], court, scores, tick } = await setup(480, 720);
    lane.enableBallPool(3);
    const ball = lane.ballPool[0], hoop = lane.getHoopPose();
    const direction = Math.atan2(hoop.x + offset - ball.x, ball.y - hoop.rimY);
    lane.beginAim({ x:ball.x, y:ball.y });
    lane.moveAim({ x:ball.x + length * Math.sin(direction), y:ball.y - length * Math.cos(direction) });
    assert(lane.releaseAim());
    const flight = ball.flight, marks = [], rimMarks = [];
    court.onBoardImpact = () => marks.push(court.assembly.boardImpacts.at(-1));
    const markRim = court.assembly.markRimImpact.bind(court.assembly);
    court.assembly.markRimImpact = (...args) => { rimMarks.push(args); markRim(...args); };
    for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 60);
    assert.equal(scores.length, scoresExpected);
    assert.equal(Boolean(flight.hoopContacts?.rim), rim);
    assert.equal(rimMarks.length > 0, rim, 'only a real rim contact starts vibration');
    assert.equal(marks.length, length === 288 ? 1 : 0);
    if (marks.length) {
      const { x, y } = marks[0];
      assert(y > .32 && y < .82, 'the hit stays within the square’s vertical span');
      assert.equal(x > .29 && x < .71, offset < 90, 'the pulse follows the hit inside or outside the square');
      if (scoresExpected) assert.equal(scores[0].clean, false);
    } else {
      assert.equal(scores[0].clean, true);
    }
    assert.equal(court.assembly.boardImpacts.length, 0, 'feedback fades before the ball returns');
  }
});

test('a slow glancing shot clears the rim without losing simulation time at lower frame rates', async () => {
  const lifetimes = [];
  for (const fps of [30, 60, 120, 240]) {
    const { lanes:[lane], tick } = await setup(480, 720);
    lane.enableBallPool(3);
    const ball = lane.ballPool[0];
    dragTowardHoop(lane, ball, 236, 4);
    assert(lane.releaseAim());
    let touched = false;
    for (let frame = 0; frame < fps * 4 && ball.flying; frame++) {
      tick(1 / fps);
      touched ||= Boolean(ball.flight?.hoopContacts?.rim);
      assert(Number.isFinite(ball.x) && Number.isFinite(ball.y));
    }
    assert(touched, 'this regression exercises persistent rim contact');
    assert.equal(ball.flying, false, `${fps} fps: the ball must not stall on the rim`);
    lifetimes.push(ball.life);
  }
  assert(Math.max(...lifetimes) - Math.min(...lifetimes) < .20,
    `sliding must spend the same real time at each frame rate: ${lifetimes}`);
});

test('a real drag that rolls inward after glancing off the rim scores once', async () => {
  for (const fps of [30, 60, 120, 240]) {
    const { lanes:[lane], scores, tick } = await setup(480, 720);
    lane.enableBallPool(3);
    const ball = lane.ballPool[0];
    dragTowardHoop(lane, ball, 186, -4);
    assert(lane.releaseAim());
    let touchedBeforeScoring = false;
    for (let frame = 0; frame < fps * 4 && ball.flying; frame++) {
      tick(1 / fps);
      if (scores.length === 0 && ball.flight?.hoopContacts?.rim) touchedBeforeScoring = true;
      assert(!ball.flight?.hoopContacts?.board, 'this route never needs the backboard');
    }
    assert(touchedBeforeScoring, `${fps} fps: the rim contact comes before the make`);
    assert.equal(ball.flying, false, `${fps} fps: the ball falls through and returns`);
    assert.deepEqual(scores, [{ lane:'player', owner:'player', clean:false }]);
  }
});

test('the spatial backboard reflects fast shots on either side of its face and allows a wide miss', async () => {
  for (const [side, direction] of [[0,-1], [1,1]]) {
    const { court, lane, ball, place, scores, tick } = await spatialShot(side);
    const { board } = court.hoopGeometry(lane);
    place({ x:direction * board.halfWidth * .65, depth:.86, height:board.height, vx:0, vDepth:7, vHeight:0 });
    tick(.033);
    assert(ball.flight.depth < .944 && ball.flight.vDepth < 0);
    assert(ball.hitRim);
    assert.equal(scores.length, 0);

    ball.hitRim = false;
    place({ x:direction * (board.halfWidth + ball.flight.radius + .015), depth:.86,
      height:board.height, vx:0, vDepth:7, vHeight:0 });
    const [segment] = court.advanceFlight(lane, ball, .033);
    assert(Math.abs(segment.to.depth + ball.flight.radius - 1) < .0001, 'wide shot reaches the rear wall');
    assert.equal(ball.hitRim, false);
  }
});

test('fast descending balls meet all four edges of the rim and bounce away', async () => {
  const { court, lane, ball, place, scores, tick } = await spatialShot();
  const { ring } = court.hoopGeometry(lane);
  for (const [x, depth] of [[-ring.halfWidth, 0], [ring.halfWidth, 0], [0,-ring.halfDepth], [0,ring.halfDepth]]) {
    ball.hitRim = false;
    place({ x, depth:court.hoopDepth + depth, height:ring.height + .085, vx:0, vDepth:0, vHeight:-7 });
    tick(.033);
    assert(ball.flight.vHeight > 0 && ball.flight.height > ring.height);
    assert(ball.hitRim);
    assert.equal(scores.length, 0);
  }
});

test('a spatial make waits for the whole ball, scores once, and ignores an upward pass', async () => {
  for (const side of [0, 1]) {
    const { court, lane, ball, place, scores, tick } = await spatialShot(side);
    const { ring } = court.hoopGeometry(lane);
    const centered = { x:ring.x, depth:court.hoopDepth, vx:0, vDepth:0 };
    place({ ...centered, height:ring.height - .025, vHeight:1.7 });
    tick(.025);
    assert(ball.flight.height > ring.height);
    assert.equal(scores.length, 0);

    place({ ...centered, height:ring.height + .01, vHeight:-1.2 });
    tick(.015);
    assert(ball.flight.height < ring.height);
    assert.equal(scores.length, 0, 'the top of the ball has not cleared the rim');
    tick(.03);
    assert.equal(scores.length, 1);
    assert.equal(scores[0].owner, lane.side);
    assert.equal(scores[0].clean, true);
    // Even a subsequent downward pass in this flight cannot score again.
    place({ ...centered, height:ring.height + .01, vHeight:-1.2 });
    tick(.05);
    assert.equal(scores.length, 1);
  }
});

test('spatial shots meet the side mesh farther in at greater depth, from either inventory', async () => {
  for (const [side, direction] of [[0,-1], [1,1]]) {
    for (const depth of [.12, .72]) {
      const { court, lane, ball, place } = await spatialShot(side);
      place({ x:direction * .32, depth, height:.24, vx:direction * 4, vDepth:0, vHeight:0 });
      const segments = court.advanceFlight(lane, ball, .033);
      const contact = segments[0].to;
      assert.equal(segments.length, 2);
      assert(ball.flight.vx * direction < 0);
      const canvasEdge = (.5 - Math.abs(contact.x) / (1 + .48 * contact.depth)) * court.width;
      assert(canvasEdge > (depth > .5 ? 170 : 70), 'the mesh is inside the canvas edge');
    }
  }
});

test('the ball touches the sloped roof and the rear wall with its surface before reflecting', async () => {
  const { court, lane, ball, place } = await spatialShot();
  place({ x:0, depth:.60, height:.54, vx:0, vDepth:0, vHeight:4 });
  const roofContact = court.advanceFlight(lane, ball, .033)[0].to;
  const roofY = 64 * 720 / 1086 * 1.48 * .60 / (1 + .48 * .60);
  const roofBallTop = court.floorY(lane, .60) - (roofContact.height + ball.flight.radius) * court.width / (1 + .48 * .60);
  assert(Math.abs(roofBallTop - roofY) < 1);
  assert(ball.flight.vHeight < 0);

  place({ x:.25, depth:.88, height:.28, vx:0, vDepth:4, vHeight:0 });
  const wallContact = court.advanceFlight(lane, ball, .033)[0].to;
  assert(Math.abs(wallContact.depth + ball.flight.radius - 1) < .0001);
  assert(Math.abs(ball.flight.vDepth + 1.8) < .0001);
  assert(ball.flight.depth < wallContact.depth, 'the rest of the step travels away from the wall');
});

test('a fast shot into a rear corner resolves all contacts within the same step', async () => {
  const { court, lane, ball, place } = await spatialShot();
  place({ x:.37, depth:.88, height:.49, vx:4.2, vDepth:4.5, vHeight:3.1 });
  const segments = court.advanceFlight(lane, ball, .033);
  assert.equal(segments.length, 4);
  assert(ball.flight.vx < 0 && ball.flight.vDepth < 0 && ball.flight.vHeight < 0);
  assert(ball.flight.depth < 1 - ball.flight.radius);
  assert(ball.y - ball.r > 40 && ball.x + ball.r < court.width * .83);
});

test('the spatial exit is near the avatar and closes after re-entry', async () => {
  const { court, lane, ball, place } = await spatialShot();
  court.rightOpening = { top:230, bottom:710 };
  let exits = 0;
  court.onExit = () => exits++;
  place({ x:.38, depth:.12, height:.15, vx:5, vDepth:0, vHeight:0 });
  court.advanceFlight(lane, ball, .033);
  assert(ball.allowOuterExit && ball.flight.vx > 0);
  assert.equal(exits, 1);

  place({ x:.20, depth:.12, height:.15, vx:-1, vDepth:0, vHeight:0 });
  court.advanceFlight(lane, ball, .033);
  assert.equal(ball.allowOuterExit, false);

  place({ x:.30, depth:.70, height:.15, vx:5, vDepth:0, vHeight:0 });
  court.advanceFlight(lane, ball, .033);
  assert(ball.flight.vx < 0);
  assert.equal(exits, 1);
});

test('rope nodes stretch locally under the ball, keep their anchors and settle afterward',async()=>{
  const {RopeNet}=await import(pathToFileURL(path.join(root,'static/game/games/air_basketball/hoop-assembly.js')));
  const net=new RopeNet();
  for(let i=0;i<45;i++)net.advance(1/120,[{x:.1,y:.34,r:.24}]);
  const displacement=n=>Math.hypot(n.x-n.restX,n.y-n.restY);
  assert(net.nodes.some(n=>displacement(n)>.04));
  assert(net.nodes.filter(n=>n.pinned).every(n=>displacement(n)===0));
  const disturbed=net.nodes.reduce((sum,n)=>sum+displacement(n),0);
  for(let i=0;i<1200;i++)net.advance(1/120,[]);
  assert(net.nodes.reduce((sum,n)=>sum+displacement(n),0)<disturbed*.05);
  net.reset();assert(net.nodes.every(n=>displacement(n)===0));
});

test('a front-rim impact moves the net anchors first and leaves a small trailing sway', async () => {
  const { court } = await setup(480, 720);
  const assembly = court.assembly;
  assembly.markRimImpact(0, 1, 1, 0, 0);
  court.advance(1 / 60, true);
  const rearHit = assembly.rimMotion();
  assert.equal(rearHit.pitch, 0, 'a hit at the rear attachment has no forward leverage');
  assembly.reset();
  assembly.markRimImpact(0, 1, 1, 0, 1);
  court.advance(1 / 60, true);
  const first = assembly.rimMotion();
  assert(first.pitch > .01 && first.pitch <= .02, 'a hard front hit produces a bounded deflection');
  assert(assembly.net.nodes.some(node => node.pinned && Math.abs(node.y - node.restY) > .001));
  for (let frame = 0; frame < 24; frame++) court.advance(1 / 120, true);
  assert.equal(assembly.rimMotion(), null);
  assert(assembly.net.nodes.every(node => !node.pinned || node.y === node.restY));
  const sway = Math.max(...assembly.net.nodes.filter(node => !node.pinned)
    .map(node => Math.hypot(node.x - node.restX, node.y - node.restY)));
  assert(sway > 1e-5 && sway < .01, 'the free ropes continue a small movement after the rim settles');
  for (let frame = 0; frame < 240; frame++) court.advance(1 / 120, true);
  assert(assembly.net.nodes.every(node => Math.hypot(node.x - node.restX, node.y - node.restY) < 1e-5));
});

test('a clean swish tugs the rim through taut ropes and then releases the load', async () => {
  for (const fps of [30, 120]) {
    const { lanes:[player], court, scores, tick } = await setup(480, 720);
    player.enableBallPool(3);
    const ball = player.ballPool[0];
    dragTowardHoop(player, ball, 200);
    assert(player.releaseAim());
    let peak = 0;
    for (let frame = 0; frame < fps * 6; frame++) {
      tick(1 / fps);
      assert.equal(court.assembly.rimImpacts.length, 0, 'the empty basket has no direct rim hit');
      if (scores.length === 0) assert.equal(court.assembly.netPull.pitch, 0);
      peak = Math.max(peak, court.assembly.netPull.pitch);
    }
    assert.deepEqual(scores, [{ lane:'player', owner:'player', clean:true }]);
    assert(peak > .0005 && peak <= .004, 'the rope load makes a small downward deflection');
    assert.equal(court.assembly.rimMotion(), null, 'the rim settles after the ball leaves the net');
  }
});

test('both canvas halves place the ball in front of, behind, or inside the net using its flight', async () => {
  const { court, lane, ball, place } = await spatialShot();
  const pose = lane.getHoopPose();
  const { ring, mount } = court.hoopGeometry(lane);
  const netY = pose.rimY + pose.rimW * .34;
  const trace = [];
  lane.drawBall = (_ctx, x, y) => { if (x === ball.x && y === ball.y) trace.push('ball'); };
  court.assembly.drawBoard = () => trace.push('board');
  court.assembly.drawRim = (_ctx, _pose, front) => trace.push(front ? 'front-rim' : 'rear-rim');
  court.assembly.net.draw = (_ctx, _pose, front) => trace.push(front ? 'front-net' : 'rear-net');
  const board = ['board'];
  const rear = ['rear-net', 'rear-rim'];
  const front = ['front-net', 'front-rim'];

  for (const { label, depth, height, x = ring.x, entered = false, scored = false, wedgeKind, order } of [
    { label:'short miss across the face of the net', depth:.80, order:[...board, ...rear, ...front, 'ball'] },
    { label:'miss behind the net', depth:.96, order:[...board, 'ball', ...rear, ...front] },
    { label:'ball perched in front of the bracket plate', depth:.924, height:mount.height + ball.flight.radius,
      wedgeKind:'mount', order:[...board, ...rear, 'ball', ...front] },
    { label:'ball just knocked off the bracket', depth:.911, height:mount.height + ball.flight.radius - .01,
      order:[...board, ...rear, 'ball', ...front] },
    { label:'ball wedged outside the bracket', depth:.944, x:ring.x - ring.halfWidth, height:ring.height + .03,
      wedgeKind:'shoulder', order:[...board, 'ball', ...rear, ...front] },
    { label:'ball descending into the mouth', depth:.90, height:ring.height + .02,
      order:[...board, ...rear, 'ball', ...front] },
    { label:'ball centred in the net', depth:.90, entered:true, scored:true,
      order:[...board, ...rear, 'ball', ...front] },
    { label:'scored ball moves away from the net', depth:.80, scored:true,
      order:[...board, ...rear, ...front, 'ball'] }
  ]) {
    place({ x, depth, height:height ?? court.heightAt(lane, netY, depth),
      vx:0, vDepth:0, vHeight:-1, enteredHoop:entered, wedgeKind });
    ball.flying = !wedgeKind;
    ball.poolState = wedgeKind ? 'wedged' : 'flying';
    ball.scored = scored;
    for (const view of court.lanes) {
      trace.length = 0;
      view.draw();
      assert.deepEqual(trace, order, `${view.side}: ${label}`);
    }
  }
});

test('a real swish and a bank move the net, while misses leave it at rest', async () => {
  for (const { length, offset, made, bank } of [
    { length:180, offset:0, made:true, bank:false },
    { length:270, offset:0, made:true, bank:true },
    { length:130, offset:0, made:false, bank:false },
    { length:288, offset:12, made:false, bank:true }
  ]) {
    const { lanes:[lane], court, scores, tick } = await setup(480, 720);
    lane.enableBallPool(3);
    const ball = lane.ballPool[0];
    const { net } = court.assembly;
    dragTowardHoop(lane, ball, length, offset);
    assert(lane.releaseAim());
    let boardHit = false, moved = false;
    court.onBoardImpact = () => { boardHit = true; };
    for (let frame = 0; frame < 720 && ball.flying; frame++) {
      tick(1 / 120);
      const stretched = net.nodes.some(n => Math.hypot(n.x - n.restX, n.y - n.restY) > .005);
      if (stretched) assert.equal(scores.length, 1, 'net motion follows the complete pass through the rim');
      moved ||= stretched;
    }
    assert.equal(scores.length, Number(made), `${length}px drag, ${offset}°`);
    assert.equal(boardHit, bank, `${length}px drag, ${offset}°`);
    assert.equal(moved, made, `${length}px drag, ${offset}°`);
  }
});

test('interference keeps contact feedback without repainting a rectangular half-machine', async () => {
  const {lanes}=await setup();
  for (const lane of lanes) {
    const rects=[], circles=[];
    lane.ctx.fillRect=(...args)=>rects.push(args);
    lane.ctx.arc=(...args)=>circles.push(args);
    const contact={x:lane.width*.6,y:lane.height*.4};
    assert(lane.interfere(20,7,contact));
    lane.drawEffects(lane.ctx);
    assert.equal(rects.length,0,'no full-lane coloured rectangle');
    assert(circles.some(([x,y])=>x===contact.x&&y===contact.y),'contact feedback remains visible');
    lane.update(.3,false);circles.length=0;lane.drawEffects(lane.ctx);
    assert.equal(circles.length,0,'contact effect expires');
  }
});

test('default gameplay timers do not invoke browser timer APIs with the timer manager as receiver', async () => {
  const {MatchTimers}=await import(pathToFileURL(path.join(root,'static/game/games/air_basketball/match-timers.js')));
  const originalSet=global.setTimeout, originalClear=global.clearTimeout;
  try {
    global.setTimeout=function(callback,delay){assert(this===undefined||this===globalThis);return 123;};
    global.clearTimeout=function(handle){assert(this===undefined||this===globalThis);};
    const timers=new MatchTimers();const entry=timers.set(()=>{},50);
    timers.pause();timers.resume();timers.clear(entry);
    assert.equal(timers.entries.size,0);
  } finally {global.setTimeout=originalSet;global.clearTimeout=originalClear;}
});

for (const fps of [15,30,60,144]) test(`six shared balls recycle without loss at ${fps} fps`,async()=>{
  const {lanes,tick}=await setup();
  lanes.forEach(lane=>lane.enableBallPool(3));
  const original=lanes.flatMap(lane=>lane.ballPool);
  for(const lane of lanes){
    assert(lane.shoot(0,-400));assert(lane.shoot(120,-450));assert(lane.shoot(-120,-500));
    assert.equal(lane.shoot(0,-600),false);
  }
  let returningSeen=false;
  for(let frame=0;frame<fps*60;frame++){
    if(frame%(fps*2)===0)for(const lane of lanes)lane.releaseAutoShot(.9);
    tick(1/fps);
    const current=lanes.flatMap(lane=>lane.ballPool);
    assert.equal(current.length,6);assert.equal(new Set(current).size,6);
    assert(current.every(ball=>original.includes(ball)&&!ball.expired));
    assert(current.every(ball=>Number.isFinite(ball.x)&&Number.isFinite(ball.y)));
    returningSeen ||= current.some(ball=>ball.poolState==='returning');
  }
  assert(returningSeen);
  for(let frame=0;frame<fps*10;frame++)tick(1/fps);
  const wedged = lanes.flatMap(lane => lane.ballPool.filter(ball => ball.poolState === 'wedged'));
  assert(wedged.length <= 1);
  assert(lanes.every(lane => lane.ballPool.every(ball => lane.isReadyBall(ball) || wedged.includes(ball))));
});

test('a firm off-centre drag can wedge against the backboard and rear shoulder until replay', async () => {
  const { lanes:[player, neko], court, scores, tick } = await setup(480, 720);
  player.enableBallPool(3);
  neko.enableBallPool(3);
  const ball = player.ballPool[0];
  dragTowardHoop(player, ball, 257.4, -6.25);
  assert(player.releaseAim());
  for (let frame = 0; frame < 480 && ball.flying; frame++) tick(1 / 120);
  assert.equal(ball.poolState, 'wedged');
  assert.equal(ball.flying, false);
  assert(ball.flight.hoopContacts.board && ball.flight.hoopContacts.rim);
  assert.equal(scores.length, 0);
  for (let frame = 0; frame < 16; frame++) tick(1 / 60);
  const at = [ball.x, ball.y, ball.r];
  assert(ball.x < player.getHoopPose().x && ball.y < player.getHoopPose().rimY);
  for (let frame = 0; frame < 600; frame++) tick(1 / 60);
  assert.equal(ball.poolState, 'wedged');
  assert.deepEqual([ball.x, ball.y, ball.r], at);
  assert.equal(scores.length, 0);
  assert(court.assembly.net.nodes.every(n => Math.hypot(n.x - n.restX, n.y - n.restY) < 1e-6));
  assert.equal(player.isReadyBall(ball), false);
  player.hoopOffset = 12;
  court.advance(0, true);
  assert(ball.x > at[0], 'the wedged ball moves with the shared rim during interference');
  player.hoopOffset = 0;
  court.advance(0, true);
  assert.deepEqual([ball.x, ball.y, ball.r], at);

  dragTowardHoop(player, player.ballPool[1], 270);
  assert(player.releaseAim());
  for (let frame = 0; frame < 360; frame++) tick(1 / 60);
  assert.equal(scores.length, 1);
  assert.equal(ball.poolState, 'wedged');
  court.resetBallPools();
  assert.equal(player.ballPool[0], ball);
  assert(player.isReadyBall(ball));
  assert.equal(ball.flight, undefined);
});

test('a range of firm drags can settle on the rear shoulders while centered banks still score', async () => {
  for (const [length, offset] of [[277.2, -6.25], [277.2, 0], [277.2, 6.25], [284.4, -7], [295.2, -6.25]]) {
    const { lanes:[player], scores, tick } = await setup(480, 720);
    player.enableBallPool(3);
    const ball = player.ballPool[0];
    dragTowardHoop(player, ball, length, offset);
    assert(player.releaseAim());
    for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 60);
    assert.equal(ball.flight?.wedgeKind, offset === 0 ? undefined : 'shoulder', `${length}px at ${offset}°`);
    assert.equal(scores.length, offset === 0 ? 1 : 0, `${length}px at ${offset}°`);
  }
});

test('rim feedback uses the same motion on both halves and carries a held ball with its support', async () => {
  const { lanes:[player], court, scores, tick } = await setup(480, 720);
  player.enableBallPool(3);
  const ball = player.ballPool[0];
  dragTowardHoop(player, ball, 257.4, -6.25);
  assert(player.releaseAim());
  for (let frame = 0; frame < 360 && ball.flying; frame++) tick(1 / 120);
  for (let frame = 0; frame < 30; frame++) tick(1 / 120);
  assert.equal(ball.poolState, 'wedged');
  const at = [ball.x, ball.y, ball.flight.x, ball.flight.depth, ball.flight.height];
  const draws = [];
  court.assembly.net.draw = (_ctx, _pose, front, motion) => draws.push({ part:'net', front, motion });
  court.assembly.drawRim = (_ctx, _pose, front, motion) => draws.push({ part:'rim', front, motion });
  court.assembly.markRimImpact(-1, .85, 1, 0);
  tick(.02);
  for (const lane of court.lanes) lane.draw();
  assert.equal(draws.length, 8);
  assert(draws.every(draw => draw.motion && draw.motion.pitch === draws[0].motion.pitch
    && draw.motion.roll === draws[0].motion.roll));
  let shift = 0;
  for (let frame = 0; frame < 45; frame++) {
    tick(1 / 120);
    assert.equal(ball.poolState, 'wedged');
    assert.equal(ball.x, at[0]);
    assert.equal(ball.flight.depth, at[3], 'a vibrating seat stays against the board');
    const pixels = court.width / (1 + .48 * ball.flight.depth);
    assert(Math.abs((ball.y - at[1]) + (ball.flight.height - at[4]) * pixels) < 1e-8,
      'visible displacement and solid-ball displacement agree');
    shift = Math.max(shift, Math.abs(ball.y - at[1]));
  }
  assert(shift > .1 && shift < 1, 'a held sphere follows the small metal deflection');
  assert.deepEqual([ball.x, ball.y, ball.flight.x, ball.flight.depth, ball.flight.height], at);
  assert.equal(scores.length, 0);
  assert.equal(court.assembly.rimMotion(), null);
  court.assembly.markRimImpact(1, 1, 1, 0);
  court.assembly.reset();
  assert.equal(court.assembly.rimMotion(), null);
});

test('a ball can perch on the centre bracket and a second rim shot can tip it through the hoop', async () => {
  for (const fps of [30, 120]) {
    const { lanes:[player, neko], court, scores, tick } = await setup(480, 720);
    player.enableBallPool(3);
    neko.enableBallPool(3);
    const [target, incoming] = player.ballPool;
    const scoredBalls = [];
    const recordScore = player.onScore;
    player.onScore = (event, ball) => { scoredBalls.push(ball.id); recordScore(event); };
    dragTowardHoop(player, target, 252.9);
    assert(player.releaseAim());
    for (let frame = 0; frame < 2 * fps && target.flying; frame++) tick(1 / fps);
    assert.equal(target.flight?.wedgeKind, 'mount');
    for (let frame = 0; frame < fps; frame++) tick(1 / fps);
    assert.equal(target.poolState, 'wedged');
    assert(Math.abs(target.flight.height - target.flight.radius - court.hoopGeometry(player).mount.height) < 1e-8);
    assert.equal(scores.length, 0);

    dragTowardHoop(player, incoming, 187.2, -4);
    assert(player.releaseAim());
    for (let frame = 0; frame < 4 * fps && scores.length === 0; frame++) tick(1 / fps);
    assert.equal(target.scored, true);
    assert.equal(scoredBalls.filter(id => id === target.id).length, 1);
    for (let frame = 0; frame < 4 * fps; frame++) tick(1 / fps);
    assert.equal(scoredBalls.filter(id => id === target.id).length, 1);
    assert.equal(new Set(scoredBalls).size, scoredBalls.length, 'each real make counts once');
    assert(scores.every(score => score.owner === 'player' && !score.clean));
    assert([...player.ballPool, ...neko.ballPool].every(ball => ball.poolState === 'ready'));
  }
});

test('bank shots that still have a path into the hoop roll through without wedging', async () => {
  for (const [slot, length, offset] of [[1, 198, 4], [1, 266.4, -4], [1, 273.6, -4], [0, 270, -5]]) {
    const { lanes:[player], scores, tick } = await setup(480, 720);
    player.enableBallPool(3);
    const ball = player.ballPool[slot];
    dragTowardHoop(player, ball, length, offset);
    assert(player.releaseAim());
    let touchedBoth = false;
    for (let frame = 0; frame < 360 && ball.flying; frame++) {
      tick(1 / 60);
      touchedBoth ||= Boolean(ball.flight?.hoopContacts?.board && ball.flight.hoopContacts.rim);
    }
    assert(touchedBoth, `the ${length}px drag contacts both board and rim`);
    assert.notEqual(ball.poolState, 'wedged');
    assert.deepEqual(scores, [{ lane:'player', owner:'player', clean:false }]);
  }
});

async function seatedCourt(kind, fps = 120, right = false) {
  const state = await setup(480, 720);
  state.lanes.forEach(lane => lane.enableBallPool(3));
  const [player] = state.lanes, target = player.ballPool[0];
  dragTowardHoop(player, target, kind === 'mount' ? 252.9 : right ? 277.2 : 284.4,
    kind === 'mount' ? 0 : right ? 6.25 : -7);
  assert(player.releaseAim());
  for (let frame = 0; frame < 4 * fps && target.flying; frame++) state.tick(1 / fps);
  for (let frame = 0; frame < fps / 2; frame++) state.tick(1 / fps);
  assert.equal(target.flight.wedgeKind, kind);
  let directHits = 0;
  const hit = state.court.hitWedgedBall.bind(state.court);
  state.court.hitWedgedBall = (...args) => { directHits++; hit(...args); };
  return { ...state, target, directHits:() => directHits };
}

test('the same distant rim strike shakes a centre perch loose but leaves a shoulder held', async () => {
  for (const fps of [30, 120]) for (const kind of ['mount', 'shoulder']) {
    const { lanes:[player, neko], target, directHits, scores, tick } = await seatedCourt(kind, fps);
    const incoming = player.ballPool[2];
    const targetScores = [];
    const record = player.onScore;
    player.onScore = (event, ball) => { if (ball === target) targetScores.push(event); record(event); };
    dragTowardHoop(player, incoming, 170, 6);
    assert(player.releaseAim());
    let released = false, queued = false;
    for (let frame = 0; frame < 4 * fps; frame++) {
      tick(1 / fps);
      queued ||= Boolean(target.flight?.wedgeShake);
      if (target.flying && !released) {
        released = true;
        assert.equal(target.scored, false, 'release itself awards no basket');
        assert.equal(target.flight.wedgeKind, undefined);
        assert.equal(target.flight.wedgeShake, undefined);
      }
    }
    assert.equal(directHits(), 0, 'the striker never touches the seated sphere');
    assert.equal(queued, kind === 'mount');
    assert.equal(released, kind === 'mount');
    assert.equal(targetScores.length, kind === 'mount' ? 1 : 0, 'only a full fall through the opening scores');
    for (let frame = 0; frame < 5 * fps; frame++) tick(1 / fps);
    assert.equal(targetScores.length, kind === 'mount' ? 1 : 0);
    const all = [...player.ballPool, ...neko.ballPool];
    assert.equal(new Set(all).size, 6);
    assert(all.every(ball => ball.poolState === 'ready' || ball === target && ball.poolState === 'wedged'));
    assert(scores.length >= 1, 'the other shot completes through the ordinary scoring path');
  }
});

test('a firm nearby rim hit can free a shoulder without a direct ball collision', async () => {
  for (const fps of [30, 120]) for (const right of [false, true]) {
    const { lanes:[player, neko], target, court, directHits, scores, tick } = await seatedCourt('shoulder', fps, right);
    const original = [...player.ballPool, ...neko.ballPool];
    const source = right ? neko : player;
    const incoming = source.ballPool[right ? 2 : 1];
    const seat = [target.x, target.y];
    let releaseAt;
    const release = court.releaseWedgedBall.bind(court);
    court.releaseWedgedBall = (...args) => { releaseAt = [target.x, target.y]; release(...args); };
    dragTowardHoop(source, incoming, 160, right ? 6 : -6);
    assert(source.releaseAim());
    for (let frame = 0; frame < 3 * fps && target.poolState === 'wedged'; frame++) {
      tick(1 / fps);
    }
    assert(releaseAt && Math.abs(releaseAt[1] - seat[1]) > .1, 'the support deflects before release');
    assert.equal(directHits(), 0);
    assert.equal(target.poolState, 'flying');
    assert.equal(target.flight.wedgePartner, incoming.id);
    assert.equal(scores.length, 0);
    assert(Math.hypot(target.x - seat[0], target.y - seat[1]) < target.r,
      'release starts at the visible seat instead of snapping to the hoop');
    for (let frame = 0; frame < 8 * fps; frame++) tick(1 / fps);
    const all = [...player.ballPool, ...neko.ballPool];
    assert.equal(all.length, 6);
    assert(original.every(ball => all.includes(ball)));
    assert(all.every(ball => ball.poolState === 'ready'));
    assert.equal(court.assembly.rimMotion(), null);
  }
});

test('ordinary makes and gentler near-side rim hits do not empty a wedged shoulder', async () => {
  for (const [slot, length, offset] of [[2, 200, 0], [1, 270, 0], [2, 170, -4]]) {
    const { lanes:[player], target, directHits, court, tick } = await seatedCourt('shoulder');
    const at = [target.x, target.y, target.flight.depth];
    const incoming = player.ballPool[slot];
    let rimContacts = 0;
    const mark = court.assembly.markRimImpact.bind(court.assembly);
    court.assembly.markRimImpact = (...args) => { rimContacts++; mark(...args); };
    dragTowardHoop(player, incoming, length, offset);
    assert(player.releaseAim());
    for (let frame = 0; frame < 720; frame++) {
      tick(1 / 120);
      assert.equal(target.poolState, 'wedged');
      assert.equal(target.flight.wedgeShake, undefined);
    }
    assert.equal(directHits(), 0);
    if (offset) assert(rimContacts > 0, 'the gentler near-side shot really contacts the rim');
    assert.deepEqual([target.x, target.y, target.flight.depth], at);
  }
});

test('a pending transmitted shock freezes on pause and credits a real fall to the rim hitter', async () => {
  const { lanes:[player, neko], target, court, scores, directHits, tick } = await seatedCourt('mount');
  dragTowardHoop(neko, neko.ballPool[0], 170, -6);
  assert(neko.releaseAim());
  for (let frame = 0; frame < 360 && !target.flight.wedgeShake; frame++) tick(1 / 120);
  assert(target.flight.wedgeShake);
  const at = [target.x, target.y, target.flight.wedgeShake.age];
  court.advance(.6, false);
  for (const lane of court.lanes) lane.update(.6, false);
  assert.deepEqual([target.x, target.y, target.flight.wedgeShake.age], at);
  assert.equal(target.poolState, 'wedged');
  for (let frame = 0; frame < 600; frame++) tick(1 / 120);
  assert.equal(directHits(), 0);
  assert(scores.some(score => score.owner === 'neko'));
  assert(scores.every(score => score.owner === 'neko'), 'a blue-tray ball does not retain stale scoring credit');
  court.resetBallPools();
  assert.equal(target.flight, undefined);
  assert.equal(court.assembly.rimMotion(), null);
});

test('a light touch and a hard shove into the board leave a wedged ball in place', async () => {
  for (const direction of ['light side', 'into board']) {
    const { lanes:[player, neko], court, scores, tick } = await setup(480, 720);
    player.enableBallPool(3);
    neko.enableBallPool(3);
    const target = player.ballPool[0];
    dragTowardHoop(player, target, 257.4, -6.25);
    assert(player.releaseAim());
    for (let frame = 0; frame < 360 && target.flying; frame++) tick(1 / 120);
    assert.equal(target.poolState, 'wedged');
    for (let frame = 0; frame < 30; frame++) tick(1 / 120);
    const seat = [target.x, target.y], rotation = target.rotation;
    const incoming = player.launchPoolBall(0, -300, { spatial:{ vx:0, vy:-300 } });
    const { x, depth, height } = target.flight;
    const gap = incoming.flight.radius + target.flight.radius + .001;
    Object.assign(incoming.flight, direction === 'light side'
      ? { x:x - gap, depth, height, vx:.24, vDepth:0, vHeight:0 }
      : { x, depth:depth - gap, height:height + .012, vx:0, vDepth:.95, vHeight:0 });
    court.projectFlight(player, incoming);
    let maxShift = 0, impactSeen = false;
    for (let frame = 0; frame < 90; frame++) {
      tick(1 / 120);
      maxShift = Math.max(maxShift, Math.hypot(target.x - seat[0], target.y - seat[1]));
      impactSeen ||= Boolean(target.flight.wedgeWobble && player.particles.length);
      assert.equal(target.poolState, 'wedged', direction);
    }
    assert(impactSeen, `the ${direction} shot produces contact feedback`);
    assert(maxShift > 1 && maxShift < target.r * .4, `the ${direction} shot nudges the ball within its seat`);
    assert.deepEqual([target.x, target.y], seat);
    assert.equal(target.rotation, rotation);
    assert.equal(scores.length, 0);
  }
});

test('a moderate side hit knocks a wedged ball loose and both balls return without a free basket', async () => {
  const { lanes:[player, neko], court, scores, tick } = await setup(480, 720);
  player.enableBallPool(3);
  neko.enableBallPool(3);
  const [target, incoming] = player.ballPool;
  dragTowardHoop(player, target, 257.4, -6.25);
  assert(player.releaseAim());
  for (let frame = 0; frame < 360 && target.flying; frame++) tick(1 / 120);
  assert.equal(target.poolState, 'wedged');
  dragTowardHoop(player, incoming, 226.8, -8);
  assert(player.releaseAim());
  for (let frame = 0; frame < 360 && target.poolState === 'wedged'; frame++) tick(1 / 120);
  assert.equal(target.poolState, 'flying');
  assert.equal(target.flight.wedgePartner, incoming.id);
  assert.equal(scores.length, 0);
  for (let frame = 0; frame < 360; frame++) tick(1 / 60);
  const allBalls = [...player.ballPool, ...neko.ballPool];
  assert.equal(new Set(allBalls).size, 6);
  assert(allBalls.includes(target) && allBalls.includes(incoming));
  assert(allBalls.every(ball => ball.poolState === 'ready'));
  assert.equal(scores.length, 0);
  assert(court.assembly.net.nodes.every(n => Math.hypot(n.x - n.restX, n.y - n.restY) < 1e-6));
});

test('pool scoring is once per flight and collection does not retroactively change the scorer',async()=>{
  const {lanes:[player,neko],scores,tick}=await setup();
  player.enableBallPool(3);neko.enableBallPool(3);
  const original=player.ball;
  const hoop=player.getHoopPose();
  player.shoot(0,50);
  Object.assign(original,{x:hoop.x,y:hoop.rimY-1,owner:'neko'});
  for(let frame=0;frame<180;frame++)tick(1/60);
  assert.equal(scores.length,1);assert.equal(scores[0].owner,'neko');
  const collector=[player,neko].find(lane=>lane.ballPool.includes(original));
  assert(collector);assert.equal(original.owner,collector.side);
  assert.equal(original.poolState,'ready');
  player.resetBall();assert(player.ballPool.includes(original));assert.equal(player.guests.length,2);
});

test('returning balls freeze when inactive, resize with the court and keep their identities',async()=>{
  const {lanes:[player],tick}=await setup();player.enableBallPool(3);
  const original=[...player.ballPool],ball=player.ball;
  player.shoot(0,100);Object.assign(ball,{y:player.poolLandingY(ball)-1});tick(1/60);
  assert.equal(ball.poolState,'returning');
  const before=player.poolSnapshot();player.update(2,false);assert.deepEqual(player.poolSnapshot(),before);
  player.canvas.getBoundingClientRect=()=>({width:480,height:720});player.resize();
  assert.deepEqual(player.ballPool,original);
  for(let frame=0;frame<60;frame++)tick(1/60);
  assert(player.ballPool.every(ball=>player.isReadyBall(ball)&&ball.y<player.height));
});

test('a contact that knocks away the held ball cancels its aim instead of shooting a different supply ball',async()=>{
  const {lanes:[player]}=await setup();player.enableBallPool(3);
  const held=player.ball;
  assert(player.beginAim({x:held.x,y:held.y}));
  player.moveAim({x:held.x+40,y:held.y-150});
  Object.assign(held,{flying:true,poolState:'flying',vx:100,vy:-100});
  player.selectReadyBall();
  assert.notEqual(player.ball,held);assert.equal(player.aim,null);
  assert.equal(player.releaseAim(),false);
  assert.equal(player.ballPool.filter(ball=>ball.flying).length,1);
});

async function sharedPoolSetup() {
  const state=await setup();state.lanes.forEach(lane=>lane.enableBallPool(3));return state;
}
function landBall(state,lane,ball,worldX){
  Object.assign(ball,{x:worldX-state.court.origin(lane),y:lane.poolLandingY(ball)-1,
    vx:0,vy:100,flying:true,inTransit:false,poolState:'flying',poolSlot:null});
}
function sharedPhysicalBalls(state){return state.lanes.flatMap(lane=>lane.ballPool);}
function settleCollection(state){for(let step=0;step<300;step++)state.tick(1/60);}

test('an ordinary made shot rolls down the ramp, drops into the tray and can be picked up there', async () => {
  const s = await setup(480, 720);
  const [player, neko] = s.lanes;
  player.enableBallPool(3);
  neko.enableBallPool(3);
  const ball = player.ballPool[0];
  dragTowardHoop(player, ball, 180);
  assert(player.releaseAim());
  const phases = new Set();
  let previous, firstPitch, deepest = 0, hop = 0, pitchChange = 0, supportedByBall = false;
  for (let frame = 0; frame < 720 && ball.poolState !== 'ready'; frame++) {
    s.tick(1 / 120);
    if (!ball.roll) continue;
    const lane = player.ballPool.includes(ball) ? player : neko;
    const x = ball.x + s.court.origin(lane);
    if (previous) assert(Math.hypot(x - previous.x, ball.y - previous.y) < s.court.width * .04);
    previous = { x, y:ball.y };
    phases.add(ball.roll.phase);
    supportedByBall ||= Boolean(ball.roll.stacked);
    deepest = Math.max(deepest, ball.roll.depth);
    firstPitch ??= ball.rollLook.orientation[0];
    pitchChange = Math.max(pitchChange, Math.abs(ball.rollLook.orientation[0] - firstPitch));
    hop = Math.max(hop, ball.roll.drawLift);
  }
  assert.equal(s.scores.length, 1);
  assert(phases.has('ramp')&&phases.has('tray'));
  assert(phases.has('drop')||supportedByBall,'a return either falls to the floor or meets a supporting ball');
  assert(deepest > .80 && ball.roll.depth < .06);
  assert(pitchChange > .1, 'the ball turns towards the viewer on the slope');
  assert((hop > 0 || supportedByBall) && hop < s.court.width * .005, 'floor landings hop softly; sphere support does not fake a floor bounce');
  assert.equal(ball.roll.drawLift, 0, 'the stopped ball sits against the tray');
  const collector = player.ballPool.includes(ball) ? player : neko;
  assert(collector.isReadyBall(ball));
  assert(collector.beginAim({ x:ball.x, y:ball.y }));
  assert.equal(collector.heldBall, ball);
});

test('a ball can cross the centre while rolling on the ramp and is collected on its arrival side', async () => {
  const s = await sharedPoolSetup();
  const [player, neko] = s.lanes;
  const ball = neko.launchPoolBall(0, -300, { spatial:{ vx:0, vy:-300 } });
  Object.assign(ball.flight, { x:.045, depth:.74, height:ball.flight.radius + .01, vx:-.38, vDepth:0, vHeight:-.3 });
  s.court.projectFlight(neko, ball);
  let crossedOnRamp = false;
  for (let frame = 0; frame < 600 && ball.poolState !== 'ready'; frame++) {
    s.tick(1 / 120);
    if (ball.roll?.phase === 'ramp' && ball.roll.x < 0) {
      assert(neko.ballPool.includes(ball), 'ramp position does not change the collector yet');
      crossedOnRamp = true;
    }
  }
  assert(crossedOnRamp);
  assert(player.isReadyBall(ball) && player.ballPool.includes(ball));
});

test('the centre divider reflects a rolling ball already inside a tray', async () => {
  const s = await sharedPoolSetup();
  const [player, neko] = s.lanes;
  const ball = player.ballPool[0];
  player.ballPool.slice(1).forEach(other => { other.inTransit = true; });
  s.court.beginReturn(player, ball);
  Object.assign(ball.roll, { phase:'tray', side:-1, x:-.12, vx:.85 });
  let reflected = false;
  for (let frame = 0; frame < 400 && ball.poolState !== 'ready'; frame++) {
    s.tick(1 / 120);
    assert(ball.roll.x < -ball.roll.radius);
    reflected ||= ball.roll.vx < 0;
  }
  assert(reflected);
  assert(player.isReadyBall(ball));
  assert(!neko.ballPool.includes(ball));
});

test('an opponent ball landing in a collector with room stays there, regardless of who launched it',async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  player.shoot(0,-800); // Make room in the player's collector.
  const incoming=neko.ball;neko.shoot(0,-400);landBall(s,neko,incoming,50);
  const identities=[...s.court.poolBalls];s.tick(1/30);
  assert.equal(incoming.poolState,'returning');
  settleCollection(s);
  assert(player.ballPool.includes(incoming));assert(player.isReadyBall(incoming));
  assert(!neko.ballPool.includes(incoming));
  assert.equal(sharedPhysicalBalls(s).length,6);
  assert.equal(incoming.owner,'player');assert(identities.includes(incoming));
});

test('a fourth ball can settle in a collector without sending any of the other balls across',async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  const parked=[...player.ballPool];
  const incoming=neko.ball;neko.shoot(0,-400);landBall(s,neko,incoming,50);
  settleCollection(s);
  assert.deepEqual(player.ballPool,[...parked,incoming]);
  assert.equal(player.ballPool.filter(ball=>player.isReadyBall(ball)).length,4);
  assert.equal(neko.ballPool.filter(ball=>neko.isReadyBall(ball)).length,2);
  const ids=sharedPhysicalBalls(s).map(ball=>ball.id);assert.equal(new Set(ids).size,6);
});

test('six balls can settle on one side, each with its own reachable grab point',async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  const landings = [
    [.098,-.097,.748,.292], [.262,-.081,.362,.006], [.280,-.025,.474,.262],
    [.091,-.061,.472,.192], [.176,0,.435,-.074], [.101,-.112,.370,.067]
  ];
  for (const lane of s.lanes) for (let i = 0; i < 3; i++) {
    const [x, vx, depth, vDepth] = landings.shift();
    const ball = lane.launchPoolBall(0, -300, { spatial:{ vx:0, vy:-300 } });
    Object.assign(ball.flight, { x:-x, vx:-vx, depth, vDepth, height:ball.flight.radius, vHeight:-.1 });
    s.court.projectFlight(lane, ball);
    lane.beginBallReturn(ball);
  }
  settleCollection(s);
  assert.equal(player.ballPool.length,6);
  assert.equal(neko.ballPool.length,0);
  const ordered=[...player.ballPool].sort((a,b)=>a.x-b.x);
  for(const [index,ball] of ordered.entries()){
    assert(player.isReadyBall(ball),ball.id);
    assert(index===0 || ball.x-ordered[index-1].x>ball.r, `insufficient exposed space for ${ball.id}`);
    assert(player.beginAim({x:ball.x,y:ball.y}));
    assert.equal(player.heldBall,ball);
    player.cancelAim();
  }
});

test('eight returning spheres form a supported pile and fall when a lower support is removed',async()=>{
  for (const fps of [30,120]) for (const direction of [-1,1]) {
    const s=await setup(480,720);s.lanes.forEach(lane=>lane.enableBallPool(4));
    const landings=[
      [.098,-.097,.748,.292],[.262,-.081,.362,.006],[.280,-.025,.474,.262],[.091,-.061,.472,.192],
      [.176,0,.435,-.074],[.101,-.112,.370,.067],[.30,.04,.82,-.05],[.23,-.03,.56,.11]
    ];
    const original=[...s.court.poolBalls];
    for (const lane of s.lanes) for(let i=0;i<4;i++) {
      const [x,vx,depth,vDepth]=landings.shift();
      const ball=lane.launchPoolBall(0,-300,{spatial:{vx:0,vy:-300}});
      Object.assign(ball.flight,{x:direction*x,vx:direction*vx,depth,vDepth,height:ball.flight.radius,vHeight:-.1});
      s.court.projectFlight(lane,ball);lane.beginBallReturn(ball);
    }
    for(let frame=0;frame<fps*6;frame++)s.tick(1/fps);
    const collector=s.lanes[direction<0?0:1], empty=s.lanes[direction<0?1:0];
    assert.equal(collector.ballPool.length,8);
    assert.equal(empty.ballPool.length,0);
    assert(original.every(ball=>collector.ballPool.includes(ball)));
    assert(collector.ballPool.filter(ball=>!ball.roll.stacked).length<=6);
    assert(collector.ballPool.filter(ball=>ball.roll.stacked).length>=2,
      'the extra balls rest on sphere contacts above the bottom row');
    assert(collector.ballPool.every(ball=>ball.roll.phase==='tray'&&!ball.roll.queued));
    for(let a=0;a<8;a++)for(let b=0;b<a;b++){
      const first=collector.ballPool[a].roll,second=collector.ballPool[b].roll;
      assert(Math.hypot(first.x-second.x,first.depth-second.depth,first.height-second.height)
        >=first.radius+second.radius-.002,'the balls keep their physical volume');
    }
    for(const ball of collector.ballPool) {
      assert(collector.isReadyBall(ball),`${direction}: ${ball.id} is still ${ball.poolState}`);
      let reachable=false;
      for (const [dx,dy] of [[0,0],[0,-.85],[-.8,-.3],[.8,-.3],[-.55,-.65],[.55,-.65]]) {
        const point={x:ball.x+dx*ball.r,y:ball.y+dy*ball.r};
        if (point.y>=collector.height*957/1086) continue;
        if (!collector.beginAim(point)) continue;
        reachable ||= collector.heldBall===ball;
        collector.cancelAim();
      }
      assert(reachable,`${direction}: ${ball.id} has no exposed grab point`);
      assert(ball.x>0&&ball.x<collector.width&&Number.isFinite(ball.y));
    }
    const drawn=[];
    collector.drawBall=(_ctx,x,y)=>drawn.push(collector.ballPool.find(ball=>ball.x===x&&ball.y===y));
    collector.draw();
    assert.equal(drawn.length,8);
    assert(drawn.every((ball,index)=>ball&&(!index||ball.roll.depth<=drawn[index-1].roll.depth)),
      'the front row covers the rear row independently of which ball is selected');
    const upper=[...collector.ballPool].sort((a,b)=>b.roll.height-a.roll.height)[0];
    const supports=collector.ballPool.filter(ball=>ball!==upper&&ball.roll.height<upper.roll.height
      && Math.abs(Math.hypot(ball.roll.x-upper.roll.x,ball.roll.height-upper.roll.height)
        -ball.roll.radius-upper.roll.radius)<.002);
    assert(supports.length>=1,'an elevated sphere has a lower sphere touching it');
    const before=[upper.x,upper.y,upper.roll.height];
    s.court.advance(.5,false);s.lanes.forEach(lane=>lane.update(.5,false));
    assert.deepEqual([upper.x,upper.y,upper.roll.height],before,'pausing freezes the supported pile');
    assert(collector.beginAim({x:upper.x,y:upper.y}));assert.equal(collector.heldBall,upper);
    supports[0].inTransit=true;
    for(let frame=0;frame<fps/4;frame++)s.tick(1/fps);
    assert.deepEqual([upper.x,upper.y,upper.roll.height],before,'a held sphere stays in the hand');
    collector.cancelAim();
    for(let frame=0;frame<fps;frame++)s.tick(1/fps);
    assert(upper.roll.height<before[2]-upper.roll.radius*.25,'a released sphere drops after its support is removed');
    assert.equal(new Set(sharedPhysicalBalls(s)).size,8);
    s.court.resetBallPools();
    assert(s.lanes.every(lane=>lane.ballPool.length===4&&lane.ballPool.every(ball=>lane.isReadyBall(ball))));
  }
});

test('the fourth ball starts clear of its neighbours and uses the same swish and bank arcs',async()=>{
  for(const side of [0,1])for(const length of [190,288]){
    const s=await setup(480,720);s.lanes.forEach(lane=>lane.enableBallPool(4));
    const lane=s.lanes[side], original=[...lane.ballPool], ball=original[3];
    const ordered=[...original].sort((a,b)=>a.x-b.x);
    const opposite=[...s.lanes[1-side].ballPool].sort((a,b)=>a.x-b.x);
    for(let i=0;i<4;i++) {
      if(i>1)assert(Math.abs((ordered[i].x-ordered[i-1].x)-(ordered[1].x-ordered[0].x))<1e-10);
      assert(Math.abs(ordered[i].x+opposite[3-i].x-lane.width)<1e-10);
    }
    assert.equal(new Set(s.court.poolBalls).size,8);
    for(const a of original)for(const b of original)if(a!==b) {
      assert(Math.hypot(a.x-b.x,a.y-b.y)>=a.r+b.r);
    }
    dragTowardHoop(lane,ball,length);
    assert.equal(lane.heldBall,ball);
    assert(lane.releaseAim('player'));
    let board=false;s.court.onBoardImpact=()=>{board=true;};
    for(let frame=0;frame<720;frame++)s.tick(1/120);
    assert.equal(s.scores.length,1);
    assert.equal(board,length===288);
    assert(original.every(b=>s.court.poolBalls.includes(b)));
  }
});

test('an empty inventory creates no physical placeholder ball, and replay redistributes the original six',async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  const original=[...s.court.poolBalls],playerBalls=[...player.ballPool];
  for(const lane of s.lanes)for(let shot=0;shot<3;shot++)lane.shoot(0,-800);
  playerBalls.forEach((ball,index)=>landBall(s,player,ball,430+index*30));settleCollection(s);
  assert.equal(player.ballPool.length,0);assert.equal(player.shoot(0,-400),false);
  assert.equal(player.countActiveBalls(),0);assert.equal(sharedPhysicalBalls(s).length,6);
  s.court.resetBallPools();
  assert.deepEqual(sharedPhysicalBalls(s),original);
  assert(s.lanes.every(lane=>lane.ballPool.length===3&&lane.ballPool.every(ball=>lane.isReadyBall(ball))));
});

test('the right collector also keeps a fourth ball that actually arrives there',async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  const parked=[...neko.ballPool];
  const incoming=player.ball;player.shoot(0,-400);landBall(s,player,incoming,s.court.width-40);
  settleCollection(s);
  assert.deepEqual(neko.ballPool,[...parked,incoming]);
  assert.equal(neko.ballPool.filter(ball=>neko.isReadyBall(ball)).length,4);
  assert.equal(player.ballPool.filter(ball=>player.isReadyBall(ball)).length,2);
});

for(const source of ['same-inventory','opposite-inventory'])test(`an actual ${source} impact can knock a held ball loose without launching a replacement`,async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  const sourceLane=source==='same-inventory'?player:neko;
  const projectile=sourceLane.ball;sourceLane.shoot(300,0);
  const held=player.ballPool.filter(ball=>player.isReadyBall(ball)).reduce((rightmost,ball)=>ball.x>rightmost.x?ball:rightmost);
  assert(player.beginAim({x:held.x,y:held.y}));
  player.moveAim({x:held.x+30,y:held.y-150});
  Object.assign(projectile,{x:held.x+s.court.origin(player)-s.court.origin(sourceLane)-held.r-projectile.r+1,
    y:held.y,vx:500,vy:0});
  s.tick(1/240);
  assert.equal(player.heldBall,null);assert.equal(player.aim,null);assert(held.flying);
  assert.equal(held.held,false);assert.equal(player.releaseAim(),false);
  assert.equal(player.ballPool.filter(ball=>ball.flying).length,source==='same-inventory'?2:1);
  assert.equal(sharedPhysicalBalls(s).length,6);
});

test('another ball can roll into the collector without interrupting a held aim',async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  const [held,nextNearest]=[...player.ballPool].sort((a,b)=>b.x-a.x);
  assert(player.beginAim({x:held.x,y:held.y}));player.moveAim({x:held.x+30,y:held.y-150});
  const incoming=neko.ball;neko.shoot(0,-400);landBall(s,neko,incoming,50);s.tick(1/60);
  settleCollection(s);
  assert.equal(player.heldBall,held);assert.equal(player.ball,held);
  assert(player.ballPool.includes(held));assert(!neko.ballPool.includes(held));
  assert(player.ballPool.includes(nextNearest));assert(player.ballPool.includes(incoming));
  assert.equal(s.court.moveSupplyBall(held,player,neko),false);
  assert(player.releaseAim());assert(held.flying);assert.equal(held.held,false);
});

test('an overlapping projectile that is moving away cannot drop the held aim',async()=>{
  const s=await sharedPoolSetup();const [player,neko]=s.lanes;
  const held=player.ball;
  assert(player.beginAim({x:held.x,y:held.y}));player.moveAim({x:held.x+30,y:held.y-150});
  const projectile=neko.ball;neko.shoot(0,-400);
  Object.assign(projectile,{x:held.x+player.width-held.r-projectile.r+1,y:held.y,vx:-200,vy:0});
  const before={x:held.x,y:held.y,telemetry:player.getAimTelemetry()};
  s.court.collideBalls();
  assert.equal(player.heldBall,held);assert.equal(held.x,before.x);assert.equal(held.y,before.y);
  assert.deepEqual(player.getAimTelemetry(),before.telemetry);
  assert(player.releaseAim());assert(held.flying);
});
