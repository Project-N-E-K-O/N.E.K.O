export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const assetVersion = new URL(import.meta.url).search;
const versionedAsset = path => new URL(`${path}${assetVersion}`, import.meta.url).href;

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
    this.stage = 1;
    this.stageClock = Math.random() * 4;
    this.hoopOffset = 0;
    this.hoopVelocity = 0;
    this.backgroundImage = new Image();
    this.backgroundImage.decoding = 'async';
    this.backgroundImage.src = versionedAsset('./assets/neko-arcade-lane-v2.webp');
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
    this.canvas.width = this.width * this.dpr;
    this.canvas.height = this.height * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.hoop = {
      x: this.width * .5,
      y: this.height * .25,
      rimY: this.height * .39,
      baseRimW: clamp(this.width * .23, 70, 108)
    };
    if (previousWidth && previousHeight
        && (previousWidth !== this.width || previousHeight !== this.height)) {
      const scaleX = this.width / previousWidth;
      const scaleY = this.height / previousHeight;
      const scaleBall = ball => {
        if (!ball || ball.expired) return;
        ball.x *= scaleX;
        ball.y *= scaleY;
        ball.vx *= scaleX;
        ball.vy *= scaleY;
        ball.r *= scaleX;
      };
      if (this.ball?.flying || this.ball?.inTransit) scaleBall(this.ball);
      this.guests.forEach(scaleBall);
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
      this.hoopOffset *= scaleX;
      this.hoopVelocity *= scaleX;
    }
    if (!this.ball?.flying && !this.ball?.inTransit) this.resetBall();
  }

  setStage(stage) {
    this.stage = clamp(Math.round(stage), 1, STAGE_RULES.length);
    this.stageClock = 0;
  }

  setFever(active) { this.fever = active; }

  getHoopPoseAt(clock = this.stageClock) {
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

  clearGuests() { this.guests = []; }

  countActiveGuestBalls({ owner = null, nativeShot = null } = {}) {
    return this.guests.filter(ball => !ball.expired
      && (owner === null || ball.owner === owner)
      && (nativeShot === null || ball.nativeShot === nativeShot)).length;
  }

  countActiveBalls({ owner = null, nativeShot = null } = {}) {
    const nativeBallActive = (this.ball.flying || this.ball.inTransit)
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

  beginAim(point) {
    if (this.ball.flying || this.ball.inTransit || Math.hypot(point.x - this.ball.x, point.y - this.ball.y) > this.ball.r * 2.5) return false;
    this.aim = point;
    return true;
  }

  moveAim(point) { if (this.aim) this.aim = point; }

  getAimTelemetry(point = this.aim) {
    if (!point) return { power: 0, angle: 0 };
    const dx = point.x - this.ball.x;
    const dy = point.y - this.ball.y;
    const length = Math.hypot(dx, dy);
    const power = Math.round(clamp((length - 18) / 192, 0, 1) * 100);
    const angle = Math.round(clamp(Math.atan2(-dy, Math.abs(dx) || .001) * 180 / Math.PI, 0, 90));
    return { power, angle };
  }

  shotVector(point = this.aim) {
    if (!point) return null;
    const dx = point.x - this.ball.x;
    const dy = point.y - this.ball.y;
    const length = Math.hypot(dx, dy);
    if (length < 18) return null;
    // Neko auto-shots use their own solver. This boost applies only to the
    // player's drag-and-release throw so it can cross the second cabinet and
    // still carry enough momentum to reach the character standing outside it.
    const speed = clamp(length, 30, 220) * (this.side === 'player' ? 4.15 : 3.55);
    return { vx: dx / length * speed, vy: dy / length * speed };
  }

  releaseAim() {
    const vector = this.shotVector();
    this.aim = null;
    return vector ? this.shoot(vector.vx, vector.vy) : false;
  }

  shoot(vx, vy) {
    if (this.ball.flying || this.ball.inTransit) return false;
    Object.assign(this.ball, { vx, vy, owner:this.side, flying:true, inTransit:false, life:0, scored:false, hitRim:false });
    return true;
  }

  autoShoot(difficulty = .72) {
    if (this.ball.flying) return false;
    const hoop = this.getHoopPose();
    const dx = hoop.x - this.ball.x;
    const dy = hoop.rimY - this.ball.y;
    // Cross the rim on the descending half of the arc. The old 0.74s lower
    // bound put the rim near the apex, so visually good Neko shots often never
    // satisfied the downward-through-the-rim scoring rule.
    const flight = .98 + Math.random() * .06;
    const gravity = 720;
    const stagePenalty = (this.stage - 1) * 7;
    const error = (Math.random() - .5) * ((1 - difficulty) * 360 + stagePenalty);
    const vx = (dx + error) / flight;
    const vy = (dy - .5 * gravity * flight * flight) / flight;
    return this.shoot(vx, vy);
  }

  releaseAutoShot(difficulty = .72) {
    const flight = .98 + Math.random() * .06;
    const hoop = this.getHoopPoseAt(this.stageClock + flight);
    const dx = hoop.x - this.ball.x;
    const dy = hoop.rimY - this.ball.y;
    const gravity = 720;
    const stagePenalty = (this.stage - 1) * 7;
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
    const rimClearance = Math.min(10, this.ball.r * .5);
    const vy = (dy - rimClearance - .5 * gravity * flight * flight) / flight;
    return this.receiveGuestBall({
      x:this.ball.x, y:this.ball.y, r:this.ball.r, vx, vy,
      owner:this.side, fever:this.fever, nativeShot:true
    });
  }

  interfere(dx, dy, point) {
    const strength = clamp(Math.hypot(dx, dy), 0, 34);
    if (strength < 1) return false;
    const b = this.ball;
    const flyingBalls = [b, ...this.guests].filter(ball => ball.flying && !ball.expired);
    const target = point && flyingBalls.length
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
    } else if (!b.inTransit) {
      b.x = clamp(b.x + dx * .7, b.r + 10, this.width - b.r - 10);
      b.y = clamp(b.y + dy * .35, this.height * .7, this.height * .88);
    }
    if (!point || point.y < this.height * .58) this.hoopVelocity += dx * .42;
    this.disruption = .28;
    return true;
  }

  collideRim(b, px, py) {
    const dx = b.x - px;
    const dy = b.y - py;
    const distance = Math.hypot(dx, dy);
    const radius = b.r + 5;
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
    this.hoopVelocity += (-this.hoopOffset * 34 - this.hoopVelocity * 8) * dt;
    this.hoopOffset += this.hoopVelocity * dt;
    this.hoopOffset = clamp(this.hoopOffset, -this.hoop.baseRimW * .34, this.hoop.baseRimW * .34);
    this.disruption = Math.max(0, this.disruption - dt);
    if (!active) return;
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

  collideWithCourt(b, previousY, onScore) {
    const hoop = this.getHoopPose();
    this.collideRim(b, hoop.x - hoop.rimW / 2, hoop.rimY);
    this.collideRim(b, hoop.x + hoop.rimW / 2, hoop.rimY);
    const boardX = hoop.x + hoop.rimW / 2 + 13;
    const boardTop = hoop.y - hoop.rimW * .62;
    if (b.x + b.r > boardX && b.x - b.r < boardX + 7 && b.y > boardTop && b.y < hoop.rimY + 15 && b.vx > 0) {
      b.x = boardX - b.r;
      b.vx *= -.7;
      b.hitRim = true;
    }
    if (!b.scored && b.vy > 0 && previousY < hoop.rimY && b.y >= hoop.rimY && Math.abs(b.x - hoop.x) < hoop.rimW * .38) {
      b.scored = true;
      this.burst(b.x, b.y, b.fever || this.fever ? 34 : 18);
      onScore({ clean:!b.hitRim, owner:b.owner });
    }
  }

  updateNativeBall(b, dt) {
    const nextX = b.x + b.vx * dt;
    const crossingEdge = this.side === 'player' && nextX > this.width ? 'right'
      : this.side === 'neko' && nextX < 0 ? 'left' : null;
    const boundaryX = crossingEdge === 'right' ? this.width : 0;
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
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const distance = Math.hypot(dx, dy);
    const minDistance = a.r + b.r;
    if (!distance || distance >= minDistance) return false;
    const nx = dx / distance;
    const ny = dy / distance;
    const aSpeed = Math.hypot(a.vx, a.vy);
    const bSpeed = Math.hypot(b.vx, b.vy);
    const overlap = minDistance - distance;
    a.x -= nx * overlap * .5; a.y -= ny * overlap * .5;
    b.x += nx * overlap * .5; b.y += ny * overlap * .5;
    const relativeSpeed = (b.vx - a.vx) * nx + (b.vy - a.vy) * ny;
    if (relativeSpeed < 0) {
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

  draw() {
    const c = this.ctx;
    const w = this.width;
    const h = this.height;
    const b = this.ball;
    const hoop = this.getHoopPose();
    const playerSide = this.side === 'player';
    const accent = playerSide ? '#67bce2' : '#e596bb';
    const deepAccent = playerSide ? '#397b9b' : '#c481a2';
    c.fillStyle = playerSide ? '#eaf5f8' : '#f8eef3';
    c.fillRect(0, 0, w, h);
    if (this.backgroundImage.complete && this.backgroundImage.naturalWidth) {
      const image = this.backgroundImage;
      const scale = Math.max(w / image.naturalWidth, h / image.naturalHeight);
      const sourceW = w / scale;
      const sourceH = h / scale;
      const sourceX = (image.naturalWidth - sourceW) / 2;
      const sourceY = Math.max(0, (image.naturalHeight - sourceH) * .42);
      c.save();
      c.globalAlpha = playerSide ? .94 : .82;
      c.drawImage(image, sourceX, sourceY, sourceW, sourceH, 0, 0, w, h);
      c.fillStyle = playerSide ? 'rgba(218,242,250,.06)' : 'rgba(255,226,240,.28)';
      c.fillRect(0, 0, w, h);
      c.restore();
    }

    // A single quiet lane shadow keeps the ball readable without redrawing the art.
    const laneShade = c.createLinearGradient(0, h * .46, 0, h);
    laneShade.addColorStop(0, 'rgba(255,255,255,0)');
    laneShade.addColorStop(1, playerSide ? 'rgba(38,102,131,.13)' : 'rgba(225,151,187,.11)');
    c.fillStyle = laneShade;
    c.fillRect(0, h * .46, w, h * .54);

    if (this.showAvatarPlaceholder) this.drawNeko(c, w * .23, h * .72, Math.min(w * .11, 34));

    if (this.hoopImage.complete && this.hoopImage.naturalWidth) {
      const assemblySize = hoop.rimW * 1.84;
      c.save();
      c.shadowColor = this.fever
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
    if (!b.inTransit) {
      this.drawMotionBlur(c, b);
      this.drawAimFeedback(c, b);
      if (b.pageOverlay) {
        // Player-owned balls are mirrored by a page-level overlay so they stay
        // above both cabinets, HUDs and the character for their whole life.
      } else if (this.aim && !b.flying) {
        const power = this.getAimTelemetry().power / 100;
        const angle = Math.atan2(this.aim.y - b.y, this.aim.x - b.x);
        c.save();
        c.translate(b.x, b.y);
        c.rotate(angle);
        c.scale(1 + power * .06, 1 - power * .045);
        this.drawBall(c, 0, 0, b.r);
        c.restore();
      } else {
        this.drawBall(c, b.x, b.y, b.r, this.fever, b.rotation || 0);
      }
      if (b.flying && b.owner !== this.side) this.drawOwnershipMarker(c, b);
    }
    this.guests.forEach(guest => {
      if (guest.pageOverlay) return;
      c.save();
      c.shadowColor = guest.owner === 'player' ? '#5db9e6' : '#ef9fc3';
      c.shadowBlur = 14;
      this.drawMotionBlur(c, guest);
      this.drawBall(c, guest.x, guest.y, guest.r, guest.fever, guest.rotation || 0);
      this.drawOwnershipMarker(c, guest);
      c.restore();
    });

    if (this.disruption > 0) {
      c.fillStyle = this.side === 'player' ? `rgba(88,190,229,${this.disruption * .55})` : `rgba(235,137,181,${this.disruption * .55})`;
      c.fillRect(0, 0, w, h);
    }

    this.particles.forEach((p, index) => {
      c.globalAlpha = clamp(p.life * 1.5, 0, 1);
      this.drawSparkle(c, p.x, p.y, p.size * 1.45, index % 3 === 0 ? '#f18479' : index % 2 ? '#f2b6d1' : '#fff1a8');
    });
    c.globalAlpha = 1;
  }

  drawAimFeedback(c, ball) {
    if (!this.aim || ball.flying) return;
    const power = this.getAimTelemetry().power / 100;
    const radius = ball.r + 10;
    c.save();
    c.lineCap = 'round';
    c.lineWidth = 4;
    c.strokeStyle = 'rgba(255,255,255,.42)';
    c.beginPath(); c.arc(ball.x, ball.y, radius, 0, Math.PI * 2); c.stroke();
    c.strokeStyle = power > .88 ? '#ef807b' : this.side === 'player' ? '#5db9e6' : '#ef9fc3';
    c.shadowColor = c.strokeStyle;
    c.shadowBlur = 10;
    c.beginPath(); c.arc(ball.x, ball.y, radius, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * power); c.stroke();
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

  drawBall(c, x, y, r, fever = this.fever, rotation = 0) {
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
      const size = r * 2.46;
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
