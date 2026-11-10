export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const assetVersion = new URL(import.meta.url).search;
const {drawBallLook} = await import(new URL(`./rolling-ball.js${assetVersion}`, import.meta.url).href);
const versionedAsset = path => new URL(`${path}${assetVersion}`, import.meta.url).href;
const BALL_SPRITE_DIAMETER = 236 / 256;
// The rim sprite's inner span is about 85% of its outer width.
const SHARED_RIM_INNER_HALF = .425;
const SPATIAL_FULL_POWER_DRAG = 360;
const SPATIAL_MAX_LAUNCH_SPEED = 900;

const STAGE_RULES = [
  { rimScale: 1, moveX: 0, moveY: 0, speed: 0 },
  { rimScale: .94, moveX: .42, moveY: 0, speed: 1.35 },
  { rimScale: .79, moveX: .35, moveY: .08, speed: 1.75 },
  { rimScale: .7, moveX: .55, moveY: .13, speed: 2.15 }
];

export class ShotLane {
  constructor({ canvas, side, onScore, onMiss, onCross, onGuestScore, onBallClash }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.side = side;
    this.onScore = onScore;
    this.onMiss = onMiss;
    this.onCross = onCross;
    this.onGuestScore = onGuestScore;
    this.onBallClash = onBallClash;
    this.showAvatarPlaceholder = side === 'neko';
    this.particles = [];
    this.guests = [];
    this.aim = null;
    this.heldBall = null;
    this.stage = 1;
    this.stageClock = Math.random() * 4;
    this.hoopOffset = 0;
    this.hoopVelocity = 0;
    this.backgroundImage = new Image();
    this.backgroundImage.decoding = 'async';
    this.backgroundImage.src = versionedAsset('./assets/neko-arcade-lane-v2.webp');
    this.sharedBackgroundImage = new Image();
    this.sharedBackgroundImage.decoding = 'async';
    this.sharedBackgroundImage.src = versionedAsset('./assets/neko-stage-four-court.png');
    this.ballImage = new Image();
    this.ballImage.decoding = 'async';
    this.ballImage.src = versionedAsset('./assets/neko-basketball.png');
    this.hoopImage = new Image();
    this.hoopImage.decoding = 'async';
    this.hoopImage.src = versionedAsset('./assets/neko-hoop.png');
    this.disruption = 0;
    this.fever = false;
    this.resize();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const previousWidth = this.width;
    const previousHeight = this.height;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.width = Math.max(240, rect.width);
    this.height = Math.max(360, rect.height);
    // Pointer positions use logical court coordinates. Strength uses the
    // corresponding screen distance, including below the minimum canvas size.
    this.inputScale = {
      x:rect.width > 0 ? rect.width / this.width : 1,
      y:rect.height > 0 ? rect.height / this.height : 1
    };
    this.canvas.width = this.width * this.dpr;
    this.canvas.height = this.height * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.hoop = {
      x: this.width * .5,
      y: this.height * .25,
      rimY: this.height * .39,
      baseRimW: clamp(this.width * .23, 70, 108)
    };
    const sizeChanged = !previousWidth || !previousHeight
      || previousWidth !== this.width || previousHeight !== this.height;
    if (previousWidth && previousHeight && sizeChanged) {
      const scaleX = this.width / previousWidth;
      const scaleY = this.height / previousHeight;
      const scaleBall = ball => {
        if (!ball || ball.expired) return;
        ball.x *= scaleX;
        ball.y *= scaleY;
        ball.vx *= scaleX;
        ball.vy *= scaleY;
        ball.r *= scaleX;
        if (ball.flight) ball.flight.projected = null;
        if (ball.returnStart) {
          ball.returnStart.x *= scaleX;
          ball.returnStart.y *= scaleY;
          if (ball.returnStart.r !== undefined) ball.returnStart.r *= scaleX;
        }
        if (ball.readyPosition) {
          ball.readyPosition.x *= scaleX;
          ball.readyPosition.y *= scaleY;
        }
      };
      if (this.ballPool) this.ballPool.forEach(scaleBall);
      else {
        if (this.ball?.flying || this.ball?.inTransit) scaleBall(this.ball);
        this.guests.forEach(scaleBall);
      }
      this.particles.forEach(particle => {
        particle.x *= scaleX;
        particle.y *= scaleY;
        particle.vx *= scaleX;
        particle.vy *= scaleY;
        particle.size *= scaleX;
      });
      if (this.aim) {
        this.aim.x *= scaleX;
        this.aim.y *= scaleY;
      }
      if (this.aimStart) {
        this.aimStart.x *= scaleX;
        this.aimStart.y *= scaleY;
      }
      this.aimMotion = null;
      this.hoopOffset *= scaleX;
      this.hoopVelocity *= scaleX;
    }
    // Re-seat the resting ball only when the court actually changed size, and keep
    // a held (already rescaled) aim: a resize mid-drag must not drop the shot.
    if (sizeChanged && this.ballPool) this.positionReadyBalls(1);
    else if (sizeChanged && !this.ball?.flying && !this.ball?.inTransit) {
      const aim = this.aim;
      this.resetBall();
      this.aim = aim;
    }
  }

  setStage(stage) {
    this.stage = clamp(Math.round(stage), 1, STAGE_RULES.length);
    this.stageClock = 0;
  }

  setFever(active) { this.fever = active; }

  getHoopPoseAt(clock = this.sharedCourt?.clock ?? this.stageClock) {
    if (this.sharedCourt) return this.sharedCourt.hoopPose(this);
    const rule = STAGE_RULES[this.stage - 1];
    const moveX = Math.sin(clock * rule.speed) * this.hoop.baseRimW * rule.moveX;
    const moveY = Math.sin(clock * rule.speed * .73 + 1.2) * this.height * rule.moveY;
    return {
      x: this.hoop.x + moveX + this.hoopOffset,
      y: this.hoop.y + moveY,
      rimY: this.hoop.rimY + moveY,
      rimW: this.hoop.baseRimW * rule.rimScale
    };
  }

  getHoopPose() { return this.getHoopPoseAt(); }

  resetBall() {
    if (this.ballPool) return this.resetBallPool();
    this.cancelAim();
    this.ball = {
      x: this.width * .5,
      y: this.height * .82,
      r: clamp(this.width * .06, 17, 25),
      vx: 0,
      vy: 0,
      owner: this.side,
      flying: false,
      inTransit: false,
      scored: false,
      hitRim: false,
      rotation: 0,
      life: 0
    };
    this.aim = null;
  }

  clearGuests() {
    if (this.ballPool) this.selectReadyBall();
    else this.guests = [];
  }

  // Inventories hold the balls currently collected on that side, independently
  // of who launched them or last owned their score.
  enableBallPool(total = 3) {
    this.poolCapacity = total;
    this.emptyBall = { x:0, y:0, r:0, vx:0, vy:0, flying:false, inTransit:true, placeholder:true };
    this.ballPool = Array.from({ length:total }, (_, index) => ({ id:`${this.side}-${index}` }));
    this.resetBallPool();
    this.sharedCourt?.captureBallPool();
  }

  resetBallPool() {
    this.cancelAim();
    for (const [slot, ball] of this.ballPool.entries()) {
      for (const key of Object.keys(ball)) if (key !== 'id') delete ball[key];
      Object.assign(ball, {
        x:this.width * .5, y:this.height * .82, r:this.sharedCourt?.ballRadius ?? clamp(this.width * .06, 17, 25),
        vx:0, vy:0, owner:this.side, flying:false, inTransit:false,
        scored:false, hitRim:false, rotation:[0, -.62, .84, .38][slot] ?? 0, life:0, poolState:'ready', poolSlot:slot
      });
    }
    this.ball = this.ballPool[0] || this.emptyBall;
    this.aim = null;
    this.selectReadyBall();
    this.positionReadyBalls(1);
  }

  isReadyBall(ball) {
    return ball && !ball.flying && !ball.inTransit && !ball.expired
      && (!this.ballPool || ball.poolState === 'ready');
  }

  isHeldBall(ball) { return Boolean(ball?.held || ball?.dragged); }

  cancelAim() {
    if (this.heldBall) this.heldBall.held = false;
    this.heldBall = null;
    this.aim = null;
    this.aimStart = null;
    this.aimMotion = null;
  }

  selectReadyBall() {
    if (this.heldBall && this.ballPool.includes(this.heldBall) && this.isReadyBall(this.heldBall)) {
      this.ball = this.heldBall;
    }
    if (!this.ballPool.includes(this.ball) || !this.isReadyBall(this.ball) || this.ball.dragged) {
      this.cancelAim();
      this.ball = this.ballPool.find(ball => this.isReadyBall(ball) && !this.isHeldBall(ball)) || this.ballPool[0] || this.emptyBall;
    }
    this.guests = this.ballPool.filter(ball => ball !== this.ball);
  }

  readyBallTarget(ball) {
    if (ball.readyPosition) return ball.readyPosition;
    if (this.sharedCourt) return this.sharedCourt.readyPosition(this, ball.poolSlot ?? 0);
    const positions = [.5, .27, .73];
    return { x:this.width * positions[ball.poolSlot ?? 0], y:this.height * .82 };
  }

  positionReadyBalls(blend) {
    for (const ball of this.ballPool) if (this.isReadyBall(ball)) {
      if (this.isHeldBall(ball) || ball.roll) continue;
      const target = this.readyBallTarget(ball);
      ball.x += (target.x - ball.x) * blend;
      ball.y += (target.y - ball.y) * blend;
    }
  }

  poolSnapshot() {
    return this.ballPool?.map(ball => ({
      id:ball.id, supply:this.side, owner:ball.owner,
      state:ball.poolState, x:ball.x, y:ball.y, vx:ball.vx, vy:ball.vy,
      r:ball.r, depth:ball.flight?.depth ?? ball.roll?.depth ?? 0,
      height:ball.flight?.height ?? ball.roll?.height ?? null
    })) || [];
  }

  readyForLaunch() {
    const available = ball => this.isReadyBall(ball) && !this.isHeldBall(ball);
    if (available(this.ball)) return this.ball;
    return this.ballPool?.find(available) || null;
  }

  launchPoolBall(vx, vy, {
    nativeShot = false, owner = this.side, fever = this.fever,
    spatial = null, ball = this.readyForLaunch()
  } = {}) {
    if (!this.ballPool?.includes(ball) || !this.isReadyBall(ball) || this.isHeldBall(ball)) return null;
    delete ball.roll;
    Object.assign(ball, {
      vx, vy, owner, fever, flying:true, inTransit:false, life:0,
      scored:false, hitRim:false, clashed:false, avatarHit:false,
      nativeShot, allowOuterExit:false, poolState:'flying', poolSlot:null, readyPosition:null
    });
    if (spatial && this.sharedCourt) this.sharedCourt.startFlight(this, ball, spatial);
    if (!this.heldBall) this.aim = null;
    this.selectReadyBall();
    return ball;
  }

  beginBallReturn(ball) {
    if (this.isHeldBall(ball)) return;
    if (!ball.scored) this.onMiss?.({ owner:ball.owner });
    if (this.sharedCourt) {
      this.sharedCourt.beginReturn(this, ball);
      this.selectReadyBall();
      return;
    }
    Object.assign(ball, {
      poolState:'returning', flying:false, inTransit:false, pageOverlay:false,
      allowOuterExit:false, vx:0, vy:0, returnElapsed:0, poolSlot:null, readyPosition:null,
      returnStart:{ x:clamp(ball.x, ball.r, this.width - ball.r), y:this.poolLandingY(ball) }
    });
    ball.x = ball.returnStart.x;
    ball.y = ball.returnStart.y;
    if (this.ball === ball) this.aim = null;
    this.selectReadyBall();
  }

  poolLandingY(ball) {
    return this.height * (this.sharedCourt ? .95 : .90) - ball.r;
  }

  liftPoolBall(ball, other) {
    delete ball.roll;
    Object.assign(ball, { flying:true, poolState:'flying', life:0, scored:false });
    if (other.flight) this.sharedCourt.startFlight(this, ball);
  }

  updateBallPool(dt) {
    for (const ball of this.ballPool) {
      if (ball.flying && !ball.inTransit) {
        ball.poolState = 'flying';
        const court = this.sharedCourt;
        const score = data => this.onScore?.(data, ball);
        if (ball.flight) {
          const segments = court.advanceFlight(this, ball, dt);
          if (ball.poolState === 'wedged') continue;
          const flight = ball.flight;
          if (!ball.scored && court.crossesHoop(this, ball, segments)) {
            this.scoreBall(ball, score);
          }
          if ((flight.vHeight <= 0 && flight.height <= flight.radius) || ball.life > 6) {
            if (flight.height < flight.radius) ball.y = court.floorY(this, flight.depth) - ball.r;
            this.beginBallReturn(ball);
          }
        } else {
          const previousY = this.advanceBall(ball, dt);
          this.containGuestEdges(ball);
          this.collideWithCourt(ball, previousY, score);
          if ((ball.vy > 0 && ball.y >= this.poolLandingY(ball)) || ball.life > 6) this.beginBallReturn(ball);
        }
      } else if (!this.sharedCourt && ball.poolState === 'returning') {
        ball.returnElapsed += dt;
        const progress = clamp(ball.returnElapsed / .75, 0, 1);
        const eased = progress * progress * (3 - 2 * progress);
        const target = this.readyBallTarget(ball);
        ball.x = ball.returnStart.x + (target.x - ball.returnStart.x) * eased;
        ball.y = ball.returnStart.y + (target.y - ball.returnStart.y) * eased;
        ball.rotation += dt * 4;
        if (progress === 1) Object.assign(ball, { poolState:'ready', owner:this.side, scored:false, life:0 });
      }
    }
    this.selectReadyBall();
    this.positionReadyBalls(1 - Math.exp(-dt * 18));
    const contacts = this.ballPool.filter(ball => ball.flying || (ball === this.ball || ball.dragged) && this.isReadyBall(ball));
    for (let i = 0; i < contacts.length; i++) for (let j = i + 1; j < contacts.length; j++) {
      const a = contacts[i], b = contacts[j];
      if (!a.flying && !b.flying) continue;
      if (this.collideBalls(a, b)) {
        if (!a.flying) this.liftPoolBall(a, b);
        if (!b.flying) this.liftPoolBall(b, a);
      }
    }
    this.selectReadyBall();
  }

  countActiveGuestBalls({ owner = null, nativeShot = null } = {}) {
    return this.guests.filter(ball => !ball.expired && (!this.ballPool || ball.flying || ball.inTransit)
      && (owner === null || ball.owner === owner)
      && (nativeShot === null || ball.nativeShot === nativeShot)).length;
  }

  countActiveBalls({ owner = null, nativeShot = null } = {}) {
    const nativeBallActive = !this.ball.placeholder && (this.ball.flying || this.ball.inTransit)
      && (owner === null || this.ball.owner === owner)
      && nativeShot !== true;
    return Number(nativeBallActive) + this.countActiveGuestBalls({ owner, nativeShot });
  }

  getGuestMotion(owner = null) {
    const candidates = owner ? this.guests.filter(ball => ball.owner === owner) : this.guests;
    const ball = candidates[candidates.length - 1];
    if (!ball || ball.expired) return null;
    return {
      xRatio: ball.x / this.width,
      yRatio: ball.y / this.height,
      vx: ball.vx,
      vy: ball.vy,
      speed: Math.hypot(ball.vx, ball.vy),
      owner: ball.owner
    };
  }

  receiveGuestBall({
    x, y, r, vx, vy, owner, fever = false, rotation = 0,
    nativeShot = false, allowOuterExit = false
  }) {
    const ball = {
      x, y, r, vx, vy, owner, fever, rotation,
      flying:true, scored:false, hitRim:false, life:0, expired:false, clashed:false,
      avatarHit:false, pageOverlay:false, nativeShot, allowOuterExit
    };
    this.guests.push(ball);
    return ball;
  }

  beginAim(point, time) {
    if (this.heldBall) return false;
    if (this.ballPool) {
      const nearby = this.ballPool.filter(ball => this.isReadyBall(ball)
        && Math.hypot(point.x - ball.x, point.y - ball.y) <= ball.r * 2.5);
      const direct = nearby.filter(ball => Math.hypot(point.x - ball.x, point.y - ball.y) <= ball.r);
      // A pile can project several spheres over the same area. Pick the
      // visible front sphere; nearby misses retain the familiar grab aid.
      const picked = (direct.length ? direct : nearby).sort((a, b) =>
        (direct.length ? (a.roll?.depth ?? 0) - (b.roll?.depth ?? 0) : 0)
        || Math.hypot(point.x - a.x, point.y - a.y) - Math.hypot(point.x - b.x, point.y - b.y))[0];
      if (!picked) return false;
      this.ball = picked;
      this.selectReadyBall();
    }
    if (this.ball.flying || this.ball.inTransit || Math.hypot(point.x - this.ball.x, point.y - this.ball.y) > this.ball.r * 2.5) return false;
    this.aim = point;
    this.aimStart = { x:point.x, y:point.y };
    this.aimMotion = Number.isFinite(time) ? { start:time, samples:[{ ...point, time }] } : null;
    this.heldBall = this.ball;
    this.ball.held = true;
    return true;
  }

  moveAim(point, time) {
    if (!this.aim) return;
    this.aim = point;
    const motion = this.aimMotion;
    if (!motion || !Number.isFinite(time)) return;
    const last = motion.samples.at(-1);
    if (time < last.time) { this.aimMotion = null; return; }
    if (time === last.time) motion.samples.pop();
    motion.samples.push({ ...point, time });
    motion.samples = motion.samples.filter(sample => time - sample.time <= 120).slice(-96);
  }

  quickAimTurn(dx, dy, length) {
    const motion = this.aimMotion;
    if (!motion || length < 40 || length > 200 || dy >= 0) return 0;
    const angle = Math.atan2(dx, -dy);
    if (Math.abs(angle) > 20 * Math.PI / 180) return 0;
    const released = motion.samples.at(-1);
    const duration = released.time - motion.start;
    if (duration > 180) return 0;
    // Mouse-up can arrive shortly after the last motion. Anchor the tail at
    // that motion, while a held/stopped gesture still loses its flick weight.
    let last = motion.samples.length - 1;
    while (last > 0 && Math.hypot((motion.samples[last - 1].x - released.x) * this.inputScale.x,
      (motion.samples[last - 1].y - released.y) * this.inputScale.y) < 1) last--;
    const end = motion.samples[last];
    const fresh = clamp((55 - (released.time - end.time)) / 20, 0, 1);
    if (!fresh) return 0;
    const samples = motion.samples.slice(0, last + 1).filter(sample => end.time - sample.time <= 65);
    if (samples.length < 3 || end.time - samples[0].time < 12) return 0;
    const sx = this.inputScale.x, sy = this.inputScale.y;
    const tx = (end.x - samples[0].x) * sx, ty = (end.y - samples[0].y) * sy;
    const travel = Math.hypot(tx, ty);
    if (ty >= -8 || Math.abs(tx) < 14 || travel <= 20) return 0;
    let path = 0, moves = 0;
    for (let index = 1; index < samples.length; index++) {
      const step = Math.hypot((samples[index].x - samples[index - 1].x) * sx,
        (samples[index].y - samples[index - 1].y) * sy);
      path += step;
      if (step >= 2) moves++;
    }
    if (moves < 2 || travel < path * .96) return 0;
    const delta = Math.atan2(tx, -ty) - angle;
    const degrees = Math.PI / 180;
    const blend = clamp((Math.abs(delta) / degrees - 12) / 12, 0, 1) * clamp((travel - 20) / 20, 0, 1);
    const quick = clamp((180 - duration) / 60, 0, 1) * clamp((length - 40) / 20, 0, 1)
      * clamp((200 - length) / 40, 0, 1) * clamp((20 - Math.abs(angle) / degrees) / 8, 0, 1);
    // Only soften a clear late sideways finish on a short, nearly-forward
    // flick. Keep the original drag length for power, and never aim at a slot
    // or the hoop. Straight drags and slow adjustments take the original path.
    return clamp(delta * .35 * blend * quick * fresh, -12 * degrees, 12 * degrees);
  }

  getAimTelemetry(point = this.aim) {
    if (!point) return { power: 0, angle: 0 };
    if (this.ballPool) {
      const shot = this.shotVector(point);
      if (!shot) return { power:0, angle:0 };
      return { power:Math.round(shot.power * 100), angle:Math.round(Math.atan2(-shot.vy, Math.abs(shot.vx)) * 180 / Math.PI) };
    }
    const dx = point.x - this.ball.x, dy = point.y - this.ball.y;
    const power = Math.round(clamp((Math.hypot(dx, dy) - 18) / 192, 0, 1) * 100);
    const angle = Math.round(clamp(Math.atan2(-dy, Math.abs(dx) || .001) * 180 / Math.PI, 0, 90));
    return { power, angle };
  }

  shotVector(point = this.aim) {
    if (!point) return null;
    const start = this.ballPool && this.aimStart || this.ball;
    let dx = point.x - start.x;
    let dy = point.y - start.y;
    const spatial = this.ballPool && this.sharedCourt;
    if (spatial) {
      dx *= this.inputScale.x;
      dy *= this.inputScale.y;
    }
    if (this.ballPool && this.sharedCourt && dy > 0) {
      // Below the grab point, only a deliberate sideways throw can launch.
      if (Math.abs(dx) < dy) return null;
      dy = 0;
    }
    const length = Math.hypot(dx, dy);
    if (length < (this.ballPool ? 4 : 18)) return null;
    const turn = spatial ? this.quickAimTurn(dx, dy, length) : 0;
    if (turn) {
      const angle = Math.atan2(dx, -dy) + turn;
      dx = Math.sin(angle) * length;
      dy = -Math.cos(angle) * length;
    }
    const fullPowerDrag = spatial ? SPATIAL_FULL_POWER_DRAG : 220;
    const drag = clamp(length, 30, fullPowerDrag);
    const speed = spatial ? drag / fullPowerDrag * SPATIAL_MAX_LAUNCH_SPEED
      : drag * (this.ballPool || this.side === 'player' ? 4.15 : 3.55);
    const vector = { vx:dx / length * speed, vy:dy / length * speed };
    return this.ballPool ? { ...vector, power:drag / fullPowerDrag } : vector;
  }

  releaseAim(owner = this.side) {
    const vector = this.shotVector();
    this.cancelAim();
    if (vector && this.ballPool && this.sharedCourt) {
      return Boolean(this.launchPoolBall(vector.vx, vector.vy, { owner, spatial:vector }));
    }
    return vector ? this.shoot(vector.vx, vector.vy, owner) : false;
  }

  shoot(vx, vy, owner = this.side) {
    if (this.ballPool) return Boolean(this.launchPoolBall(vx, vy, { owner }));
    if (this.ball.flying || this.ball.inTransit) return false;
    Object.assign(this.ball, { vx, vy, owner, flying:true, inTransit:false, life:0, scored:false, hitRim:false });
    return true;
  }

  releaseAutoShot(difficulty = .72, {
    ball = this.ballPool ? this.readyForLaunch() : this.ball,
    owner = this.side, fever = this.fever, stage = this.stage
  } = {}) {
    if (!ball) return null;
    if (this.sharedCourt && this.ballPool) {
      const error = (Math.random() - .5) * ((1 - difficulty) * 360 + (stage - 1) * 7);
      const seconds = 1.32 + Math.random() * .12;
      return this.launchPoolBall(0, 0, { ball, owner, fever, nativeShot:true, spatial:{ auto:true, seconds, error } });
    }
    let flight = .98 + Math.random() * .06;
    const clock = this.sharedCourt?.clock ?? this.stageClock;
    if (this.sharedCourt) {
      // The shared target is now diagonal from each ready ball. Retain the
      // original aim/error solver, but give its arc room to clear the near lip
      // on ascent instead of aiming straight through that physical contact.
      const clearance = 2 * (ball.r + 5);
      for (let i = 0; i < 2; i++) {
        const target = this.getHoopPoseAt(clock + flight);
        const rise = Math.max(0, ball.y - target.rimY) + clearance;
        flight = Math.max(flight, (Math.sqrt(2 * 720 * rise) + Math.sqrt(2 * 720 * clearance)) / 720);
      }
    }
    const hoop = this.getHoopPoseAt(clock + flight);
    const dx = hoop.x - ball.x;
    const dy = hoop.rimY - ball.y;
    const gravity = 720;
    const stagePenalty = (stage - 1) * 7;
    const error = (Math.random() - .5) * ((1 - difficulty) * 360 + stagePenalty);
    // Account for the court's existing horizontal damping so the automatic
    // shot reaches the hoop pose it aimed at instead of consistently landing
    // short. This does not alter the damping used by the physics simulation.
    const dampingRate = -60 * Math.log(.998);
    const horizontalTravel = (1 - Math.exp(-dampingRate * flight)) / dampingRate;
    const vx = (dx + error) / horizontalTravel;
    // Semi-implicit gravity advances velocity before position. A small visual
    // clearance makes the ball cross the rim downward at ordinary frame rates
    // instead of peaking a few pixels below the scoring sensor.
    const rimClearance = Math.min(10, ball.r * .5);
    const vy = (dy - rimClearance - .5 * gravity * flight * flight) / flight;
    if (this.ballPool) return this.launchPoolBall(vx, vy, { ball, owner, fever, nativeShot:true });
    return this.receiveGuestBall({
      x:ball.x, y:ball.y, r:ball.r, vx, vy, owner, fever, nativeShot:true
    });
  }

  interfere(dx, dy, point, selectedBall = null) {
    const strength = clamp(Math.hypot(dx, dy), 0, 34);
    if (strength < 1) return false;
    const b = selectedBall || this.ball;
    const flyingBalls = [b, ...this.guests].filter(ball => ball.flying && !ball.expired);
    let target = selectedBall?.flying ? selectedBall : null;
    if (!selectedBall) target = point && flyingBalls.length
      ? flyingBalls.reduce((nearest, ball) => Math.hypot(point.x - ball.x, point.y - ball.y) < Math.hypot(point.x - nearest.x, point.y - nearest.y) ? ball : nearest)
      : b.flying ? b : null;
    if (target) {
      const distance = point ? Math.hypot(point.x - target.x, point.y - target.y) : 0;
      const reach = target.r * 4.5;
      if (distance < reach) {
        const influence = 1 - distance / reach;
        target.vx += dx * 9 * influence;
        target.vy += dy * 7 * influence;
        target.hitRim = true;
      }
    } else if (this.isReadyBall(b) && (!this.isHeldBall(b) || b.dragged)) {
      b.x = clamp(b.x + dx * .7, b.r + 10, this.width - b.r - 10);
      b.y = clamp(b.y + dy * .35, this.height * .7, this.height * .88);
      if (selectedBall) b.readyPosition = { x:b.x, y:b.y };
    }
    if (!point || point.y < this.height * .58) this.hoopVelocity += dx * .42;
    this.disruptionPoint = { x:point?.x ?? b.x, y:point?.y ?? b.y };
    this.disruption = .28;
    return true;
  }

  collideRim(b, px, py, rimThickness = 5) {
    const dx = b.x - px;
    const dy = b.y - py;
    const distance = Math.hypot(dx, dy);
    const radius = b.r + rimThickness;
    if (distance >= radius || !distance) return;
    const nx = dx / distance;
    const ny = dy / distance;
    const dot = b.vx * nx + b.vy * ny;
    if (dot >= 0) return;
    b.x = px + nx * radius;
    b.y = py + ny * radius;
    b.vx = (b.vx - 1.72 * dot * nx) * .8;
    b.vy = (b.vy - 1.72 * dot * ny) * .8;
    b.hitRim = true;
  }

  update(dt, active) {
    this.particles.forEach(p => { p.life -= dt; p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 260 * dt; });
    this.particles = this.particles.filter(p => p.life > 0);
    if (active) this.stageClock += dt;
    if (!this.sharedCourt) this.advanceHoop(dt);
    this.disruption = Math.max(0, this.disruption - dt);
    if (!active) return;
    if (this.ballPool) return this.updateBallPool(dt);
    const b = this.ball;
    if (b.flying) this.updateNativeBall(b, dt);
    this.guests.forEach(guest => this.updateGuestBall(guest, dt));
    this.guests.forEach(guest => {
      // An auto-shot is spawned from the ready ball's exact position. Treating
      // that pair as a collision made one Neko release launch two basketballs.
      // Cross-court and prank balls still collide with the ready ball normally.
      if (guest.expired || this.ball.inTransit || (guest.nativeShot && !this.ball.flying)) return;
      const nativeWasFlying = this.ball.flying;
      if (this.collideBalls(guest, this.ball) && !nativeWasFlying) {
        Object.assign(this.ball, { flying:true, life:0, scored:false });
      }
    });
    for (let i = 0; i < this.guests.length; i++) {
      for (let j = i + 1; j < this.guests.length; j++) this.collideBalls(this.guests[i], this.guests[j]);
    }
    this.guests = this.guests.filter(guest => !guest.expired);
  }

  advanceHoop(dt) {
    this.hoopVelocity += (-this.hoopOffset * 34 - this.hoopVelocity * 8) * dt;
    this.hoopOffset += this.hoopVelocity * dt;
    this.hoopOffset = clamp(this.hoopOffset, -this.hoop.baseRimW * .34, this.hoop.baseRimW * .34);
  }

  advanceBall(b, dt) {
    const previousY = b.y;
    b.life += dt;
    b.vy += 720 * dt;
    b.x += b.vx * dt;
    b.y += b.vy * dt;
    b.rotation = (b.rotation || 0) + b.vx * dt / Math.max(10, b.r) * .55;
    b.vx *= Math.pow(.998, dt * 60);
    return previousY;
  }

  containTopEdge(b) {
    if (b.y - b.r >= 0) return;
    b.y = b.r;
    if (b.vy < 0) b.vy = Math.abs(b.vy) * .74;
    b.hitRim = true;
  }

  containOuterEdge(b) {
    if (this.sharedCourt) {
      this.containSharedEdges(b, b.allowOuterExit);
      return;
    }
    if (this.side === 'player' && b.x - b.r < 0) {
      b.x = b.r;
      if (b.vx < 0) b.vx = Math.abs(b.vx) * .76;
      b.hitRim = true;
    } else if (this.side === 'neko' && b.x + b.r > this.width) {
      b.x = this.width - b.r;
      if (b.vx > 0) b.vx = -Math.abs(b.vx) * .76;
      b.hitRim = true;
    }
  }

  containGuestEdges(b) {
    this.containTopEdge(b);
    if (this.sharedCourt) {
      this.containSharedEdges(b, b.allowOuterExit);
      return;
    }
    if (b.x - b.r < 0) {
      b.x = b.r;
      if (b.vx < 0) b.vx = Math.abs(b.vx) * .76;
      b.hitRim = true;
    } else if (!b.allowOuterExit && b.x + b.r > this.width) {
      b.x = this.width - b.r;
      if (b.vx > 0) b.vx = -Math.abs(b.vx) * .76;
      b.hitRim = true;
    }
  }

  containSharedEdges(b, allowOuterExit = false) {
    const { left, right } = this.sharedCourt.bounds(this);
    if (b.x - b.r < left) {
      b.x = left + b.r;
      if (b.vx < 0) b.vx = Math.abs(b.vx) * .76;
      b.hitRim = true;
    } else if (!allowOuterExit && b.x + b.r > right) {
      if (this.sharedCourt.canExitRight(b)) {
        b.allowOuterExit = true;
        this.sharedCourt.onExit?.(b, this);
      } else {
        b.x = right - b.r;
        if (b.vx > 0) b.vx = -Math.abs(b.vx) * .76;
        b.hitRim = true;
      }
    }
  }

  scoreBall(b, onScore) {
    b.scored = true;
    if (b.flight) b.flight.vDepth *= .16;
    this.burst(b.x, b.y, b.fever || this.fever ? 34 : 18);
    onScore({ clean:!b.hitRim, owner:b.owner });
  }

  collideWithCourt(b, previousY, onScore, allowScore = true) {
    const hoop = this.getHoopPose();
    const rimThickness = this.sharedCourt ? hoop.rimW * (.5 - SHARED_RIM_INNER_HALF) : 5;
    this.collideRim(b, hoop.x - hoop.rimW / 2, hoop.rimY, rimThickness);
    this.collideRim(b, hoop.x + hoop.rimW / 2, hoop.rimY, rimThickness);
    const boardRimW = hoop.boardRimW ?? hoop.rimW;
    const boardX = hoop.x + boardRimW / 2 + 13;
    const boardTop = hoop.y - boardRimW * .62;
    if (b.x + b.r > boardX && b.x - b.r < boardX + 7 && b.y > boardTop && b.y < hoop.rimY + 15 && b.vx > 0) {
      b.x = boardX - b.r;
      b.vx *= -.7;
      b.hitRim = true;
    }
    const scoringHalfWidth = this.sharedCourt ? hoop.rimW * SHARED_RIM_INNER_HALF - b.r : hoop.rimW * .38;
    if (allowScore && !b.scored && b.vy > 0 && previousY < hoop.rimY && b.y >= hoop.rimY && Math.abs(b.x - hoop.x) < scoringHalfWidth) {
      this.scoreBall(b, onScore);
    }
  }

  updateNativeBall(b, dt) {
    const nextX = b.x + b.vx * dt;
    const bounds = this.sharedCourt?.bounds(this);
    const crossingEdge = bounds
      ? null
      : this.side === 'player' && nextX > this.width ? 'right'
        : this.side === 'neko' && nextX < 0 ? 'left' : null;
    const boundaryX = crossingEdge === 'right' ? (bounds?.right ?? this.width) : 0;
    const crossingRatio = crossingEdge
      ? clamp((boundaryX - b.x) / (nextX - b.x), 0, 1)
      : 1;
    const simulatedDt = dt * crossingRatio;
    const previousY = this.advanceBall(b, simulatedDt);
    if (crossingEdge) b.x = boundaryX;
    this.containTopEdge(b);
    this.containOuterEdge(b);
    // Hand the ball to the page-level overlay as soon as its centre reaches the
    // cabinet boundary. Waiting for the whole sprite to leave made it vanish
    // behind the cabinet before reappearing in the gap.
    if (crossingEdge && this.onCross?.({
      x:b.x, y:b.y, r:b.r, vx:b.vx, vy:b.vy,
      owner:b.owner, fever:this.fever, rotation:b.rotation || 0, edge:crossingEdge,
      sourceWidth:this.width, sourceHeight:this.height,
      stepRemainder:Math.max(0, dt - simulatedDt)
    })) {
      Object.assign(b, { flying:false, inTransit:true });
      this.aim = null;
      return;
    }
    this.collideWithCourt(b, previousY, data => this.onScore(data));
    if (b.y - b.r > this.height || b.life > 5) {
      if (!b.scored) this.onMiss?.({ owner:b.owner });
      this.resetBall();
    }
  }

  updateGuestBall(b, dt) {
    if (b.expired) return;
    const previousY = this.advanceBall(b, dt);
    this.containGuestEdges(b);
    this.collideWithCourt(b, previousY, data => this.onGuestScore?.(data));
    if (b.y - b.r > this.height || b.life > 5.5) {
      b.expired = true;
      if (b.nativeShot && !b.scored) this.onMiss?.({ owner:b.owner });
    }
  }

  collideBalls(a, b) {
    if (a.expired || b.expired) return false;
    // The spatial wedge impact already resolved this pair. Let the balls
    // separate in depth before using the usual projected-ball collision.
    if ((a.flight?.wedgePartner && a.flight.wedgePartner === b.id && a.life < .18)
        || (b.flight?.wedgePartner && b.flight.wedgePartner === a.id && b.life < .18)) return false;
    if (Math.abs((a.flight?.depth ?? a.roll?.depth ?? 0) - (b.flight?.depth ?? b.roll?.depth ?? 0)) > .075) return false;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const distance = Math.hypot(dx, dy);
    const minDistance = a.r + b.r;
    if (!distance || distance >= minDistance) return false;
    const nx = dx / distance;
    const ny = dy / distance;
    const aSpeed = Math.hypot(a.vx, a.vy);
    const bSpeed = Math.hypot(b.vx, b.vy);
    const relativeSpeed = (b.vx - a.vx) * nx + (b.vy - a.vy) * ny;
    // Collectors cannot take a held ball. An actual incoming impact can still
    // knock it loose; an overlap without an approaching contact cannot.
    if (relativeSpeed >= 0 && (this.isHeldBall(a) || this.isHeldBall(b))) return false;
    const overlap = minDistance - distance;
    a.x -= nx * overlap * .5; a.y -= ny * overlap * .5;
    b.x += nx * overlap * .5; b.y += ny * overlap * .5;
    if (relativeSpeed < 0) {
      for (const ball of [a, b]) if (this.isHeldBall(ball)) {
        const holder = (this.sharedCourt?.lanes || [this]).find(lane => lane.heldBall === ball);
        holder?.cancelAim();
        ball.dragged = false;
      }
      const impulse = -(1 + .86) * relativeSpeed / 2;
      a.vx -= impulse * nx; a.vy -= impulse * ny;
      b.vx += impulse * nx; b.vy += impulse * ny;
      let newOwner = null;
      if (aSpeed > bSpeed * 1.08 && a.owner) {
        b.owner = a.owner;
        newOwner = a.owner;
      } else if (bSpeed > aSpeed * 1.08 && b.owner) {
        a.owner = b.owner;
        newOwner = b.owner;
      }
      [a,b].forEach(ball => {
        const speed = Math.hypot(ball.vx, ball.vy);
        const boosted = Math.min(960, speed * 1.06);
        if (speed > 0) { ball.vx *= boosted / speed; ball.vy *= boosted / speed; }
      });
      a.hitRim = b.hitRim = true;
      this.burst((a.x + b.x) / 2, (a.y + b.y) / 2, 8);
      if (!a.clashed) {
        a.clashed = true;
        this.onBallClash?.({ owner:newOwner || a.owner, ownershipChanged:Boolean(newOwner) });
      }
    }
    return true;
  }

  burst(x, y, amount = 18) {
    for (let i = 0; i < amount; i++) {
      const a = Math.random() * Math.PI * 2;
      const s = 35 + Math.random() * 150;
      this.particles.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s - 50, life: .6 + Math.random() * .55, size: 2 + Math.random() * 4 });
    }
  }

  draw(aimAllowed = true) {
    if (this.sharedCourt) return this.sharedCourt.draw(this, aimAllowed);
    this.drawCourt();
    this.drawBalls(this.ctx);
    this.drawEffects(this.ctx);
  }

  drawCourt(worldWidth = this.width, worldOrigin = 0) {
    const c = this.ctx;
    const w = this.width;
    const h = this.height;
    const hoop = this.getHoopPose();
    const playerSide = this.sharedCourt || this.side === 'player';
    const accent = playerSide ? '#67bce2' : '#e596bb';
    const deepAccent = playerSide ? '#397b9b' : '#c481a2';
    c.fillStyle = playerSide ? '#eaf5f8' : '#f8eef3';
    c.fillRect(0, 0, w, h);
    const image = this.sharedCourt ? this.sharedBackgroundImage : this.backgroundImage;
    if (image.complete && image.naturalWidth) {
      const scale = Math.max(worldWidth / image.naturalWidth, h / image.naturalHeight);
      const sourceW = this.sharedCourt ? image.naturalWidth : worldWidth / scale;
      const sourceH = this.sharedCourt ? image.naturalHeight : h / scale;
      const sourceX = (image.naturalWidth - sourceW) / 2;
      const sourceY = Math.max(0, (image.naturalHeight - sourceH) * .42);
      c.save();
      c.globalAlpha = this.sharedCourt ? 1 : playerSide ? .94 : .82;
      c.drawImage(image, sourceX, sourceY, sourceW, sourceH, -worldOrigin, 0, worldWidth, h);
      c.fillStyle = this.sharedCourt ? 'rgba(0,0,0,0)' : playerSide ? 'rgba(218,242,250,.06)' : 'rgba(255,226,240,.28)';
      c.fillRect(0, 0, w, h);
      c.restore();
    }

    // A single quiet lane shadow keeps the ball readable without redrawing the art.
    const laneShade = c.createLinearGradient(0, h * .46, 0, h);
    laneShade.addColorStop(0, 'rgba(255,255,255,0)');
    laneShade.addColorStop(1, playerSide ? 'rgba(38,102,131,.13)' : 'rgba(225,151,187,.11)');
    c.fillStyle = laneShade;
    if (!this.sharedCourt) c.fillRect(0, h * .46, w, h * .54);

    if (this.showAvatarPlaceholder && !this.sharedCourt) this.drawNeko(c, w * .23, h * .72, Math.min(w * .11, 34));

    if (this.sharedCourt) {
      for (const layer of this.sharedCourt.boxLayers) this.sharedCourt.drawLayer(this, layer);
      return;
    }

    if (this.hoopImage.complete && this.hoopImage.naturalWidth) {
      const assemblySize = hoop.rimW * 1.84;
      c.save();
      c.shadowColor = (this.sharedCourt ? this.sharedCourt.lanes.some(lane => lane.fever) : this.fever)
        ? 'rgba(248,188,89,.72)'
        : playerSide ? 'rgba(64,126,158,.28)' : 'rgba(229,157,192,.28)';
      c.shadowBlur = this.fever ? 17 : 9;
      c.drawImage(
        this.hoopImage,
        hoop.x - assemblySize / 2,
        hoop.rimY - assemblySize * .595,
        assemblySize,
        assemblySize
      );
      c.restore();
    } else {
      const boardW = hoop.rimW * 1.45;
      const boardH = hoop.rimW * .88;
      const boardTop = hoop.y - boardH * .48;
      c.save();
      c.fillStyle = 'rgba(250,253,253,.72)';
      c.strokeStyle = deepAccent;
      c.lineWidth = 3;
      this.roundRect(c, hoop.x - boardW / 2, boardTop, boardW, boardH, 5);
      c.fill(); c.stroke();
      c.strokeStyle = '#e66f63';
      c.lineWidth = 6;
      c.beginPath(); c.ellipse(hoop.x, hoop.rimY, hoop.rimW / 2, 7, 0, 0, Math.PI * 2); c.stroke();
      c.restore();
    }
  }

  drawBalls(c, include = () => true, aimAllowed = true) {
    const b = this.ball;
    if (!b.inTransit && include(b)) {
      this.drawMotionBlur(c, b);
      this.drawAimFeedback(c, b, aimAllowed);
      if (b.pageOverlay) {
        // A page-level sprite draws this same ball while it is outside the
        // illustrated court, so it can remain visible beside the character.
      } else {
        this.drawBall(c, b.x, b.y - (b.roll?.drawLift || 0), b.r, this.fever, b.rotation || 0, b.rollLook);
      }
      if (b.flying && b.owner !== this.side) this.drawOwnershipMarker(c, b);
    }
    this.guests.forEach(guest => {
      if (guest.expired || guest.inTransit || guest.pageOverlay || !include(guest)) return;
      c.save();
      c.shadowColor = guest.owner === 'player' ? '#5db9e6' : '#ef9fc3';
      c.shadowBlur = 14;
      this.drawMotionBlur(c, guest);
      this.drawBall(c, guest.x, guest.y - (guest.roll?.drawLift || 0), guest.r, guest.fever, guest.rotation || 0, guest.rollLook);
      if (guest.flying) this.drawOwnershipMarker(c, guest);
      c.restore();
    });

  }

  drawEffects(c) {
    if (this.disruption > 0) {
      if (this.sharedCourt) {
        // The old full-lane tint revealed a rectangular pink half-machine
        // behind the shared art. Keep feedback local to the actual contact.
        const point = this.disruptionPoint || this.ball;
        const progress = 1 - clamp(this.disruption / .28, 0, 1);
        c.save();
        c.strokeStyle = `rgba(89,166,201,${(1 - progress) * .65})`;
        c.lineWidth = 2;
        c.beginPath();
        c.arc(point.x, point.y, 16 + progress * 32, 0, Math.PI * 2);
        c.stroke();
        c.restore();
      } else {
        c.fillStyle = this.side === 'player' ? `rgba(88,190,229,${this.disruption * .55})` : `rgba(235,137,181,${this.disruption * .55})`;
        c.fillRect(0, 0, this.width, this.height);
      }
    }

    this.particles.forEach((p, index) => {
      c.globalAlpha = clamp(p.life * 1.5, 0, 1);
      this.drawSparkle(c, p.x, p.y, p.size * 1.45, index % 3 === 0 ? '#f18479' : index % 2 ? '#f2b6d1' : '#fff1a8');
    });
    c.globalAlpha = 1;
  }

  drawAimFeedback(c, ball, aimAllowed) {
    if (!this.aim || ball.flying) return;
    const shot = aimAllowed ? this.shotVector() : null;
    const radius = ball.r + 8;
    c.save();
    c.lineCap = 'round';
    c.lineWidth = 4;
    c.strokeStyle = 'rgba(255,255,255,.60)';
    c.beginPath(); c.arc(ball.x, ball.y, radius, 0, Math.PI * 2); c.stroke();
    if (shot) {
      const power = shot.power ?? this.getAimTelemetry().power / 100;
      const accent = this.side === 'player' ? '#287ca8' : '#a64b7c';
      c.strokeStyle = accent;
      c.beginPath(); c.arc(ball.x, ball.y, radius, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * power); c.stroke();
      c.translate(ball.x, ball.y);
      c.rotate(Math.atan2(shot.vy, shot.vx));
      c.beginPath();
      c.moveTo(radius + 14, 0);
      c.lineTo(radius + 3, -5);
      c.lineTo(radius + 3, 5);
      c.closePath();
      c.fillStyle = accent;
      c.strokeStyle = '#fff';
      c.lineWidth = 2;
      c.stroke(); c.fill();
    }
    c.restore();
  }

  drawMotionBlur(c, ball) {
    if (!ball.flying) return;
    const speed = Math.hypot(ball.vx, ball.vy);
    if (speed < 80) return;
    const nx = ball.vx / speed;
    const ny = ball.vy / speed;
    c.save();
    c.fillStyle = ball.fever || this.fever ? 'rgba(244,196,95,.2)' : 'rgba(198,99,75,.14)';
    for (let i = 2; i >= 1; i--) {
      c.globalAlpha = .18 / i;
      c.beginPath();
      c.arc(ball.x - nx * ball.r * i * .72, ball.y - ny * ball.r * i * .72, ball.r * (1 - i * .16), 0, Math.PI * 2);
      c.fill();
    }
    c.restore();
  }

  drawOwnershipMarker(c, ball) {
    c.save();
    c.strokeStyle = ball.owner === 'player' ? '#4eaedb' : '#e78fb7';
    c.lineWidth = 2.5;
    c.globalAlpha = .78;
    c.setLineDash([4,3]);
    c.beginPath(); c.arc(ball.x, ball.y, ball.r + 5, 0, Math.PI * 2); c.stroke();
    c.restore();
  }

  ballSpriteSize(radius) {
    return this.sharedCourt ? radius * 2 / BALL_SPRITE_DIAMETER : radius * 2.46;
  }

  drawBall(c, x, y, r, fever = this.fever, rotation = 0, look = null) {
    if (this.sharedCourt && look
        && drawBallLook(c, x, y, r, fever, rotation, look, this.ballImage, BALL_SPRITE_DIAMETER)) return;
    if (this.ballImage.complete && this.ballImage.naturalWidth) {
      c.save();
      c.translate(x, y);
      c.rotate(rotation);
      if (fever) {
        c.shadowColor = 'rgba(248,188,89,.78)';
        c.shadowBlur = Math.max(10, r * .72);
      } else {
        c.shadowColor = 'rgba(45,77,99,.24)';
        c.shadowBlur = Math.max(4, r * .32);
      }
      const size = this.ballSpriteSize(r);
      c.drawImage(this.ballImage, -size / 2, -size / 2, size, size);
      c.restore();
      return;
    }
    const g = c.createRadialGradient(x - r * .35, y - r * .38, 2, x, y, r);
    g.addColorStop(0, fever ? '#fff3b8' : '#ffd49a');
    g.addColorStop(.5, fever ? '#f5ae4e' : '#f1945f');
    g.addColorStop(1, '#c45f4b');
    c.save();
    c.shadowColor = 'rgba(130,74,63,.2)';
    c.shadowBlur = Math.max(4, r * .35);
    c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill();
    c.shadowBlur = 0;
    c.strokeStyle = '#824c47'; c.lineWidth = Math.max(2, r * .08);
    c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.moveTo(x - r, y); c.quadraticCurveTo(x, y - r * .44, x + r, y); c.moveTo(x - r, y); c.quadraticCurveTo(x, y + r * .44, x + r, y); c.moveTo(x, y - r); c.lineTo(x, y + r); c.stroke();
    c.restore();
  }

  drawNeko(c, x, y, r) {
    c.save();
    c.translate(x, y);
    c.shadowColor = 'rgba(146,78,114,.18)';
    c.shadowBlur = 12;
    c.fillStyle = 'rgba(255,240,247,.92)';
    c.beginPath(); c.moveTo(-r * .72, -r * .55); c.lineTo(-r * .5, -r * 1.35); c.lineTo(-r * .08, -r * .78); c.lineTo(r * .45, -r * 1.35); c.lineTo(r * .72, -r * .45); c.arc(0, 0, r, 0, Math.PI * 2); c.fill();
    c.shadowBlur = 0;
    c.fillStyle = '#597188';
    c.beginPath(); c.arc(-r * .32, -r * .05, r * .09, 0, Math.PI * 2); c.arc(r * .32, -r * .05, r * .09, 0, Math.PI * 2); c.fill();
    c.strokeStyle = '#e693b8'; c.lineWidth = 2;
    c.beginPath(); c.arc(0, r * .22, r * .26, .15 * Math.PI, .85 * Math.PI); c.stroke();
    c.restore();
  }

  roundRect(c, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + width, y, x + width, y + height, r);
    c.arcTo(x + width, y + height, x, y + height, r);
    c.arcTo(x, y + height, x, y, r);
    c.arcTo(x, y, x + width, y, r);
    c.closePath();
  }

  drawSparkle(c, x, y, size, color) {
    c.save();
    c.translate(x, y);
    c.fillStyle = color;
    c.beginPath();
    for (let i = 0; i < 8; i++) {
      const angle = -Math.PI / 2 + i * Math.PI / 4;
      const radius = i % 2 ? size * .42 : size;
      const px = Math.cos(angle) * radius;
      const py = Math.sin(angle) * radius;
      if (i === 0) c.moveTo(px, py); else c.lineTo(px, py);
    }
    c.closePath();
    c.fill();
    c.restore();
  }
}
