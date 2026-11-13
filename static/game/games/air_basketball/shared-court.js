// Keep the 1.0 controls, but give both inventories one world.
// Lane coordinates differ only by an x translation; there is no wall or
// teleport at the centre, and every ball uses the same hoop and outer bounds.
const assetVersion = new URL(import.meta.url).search;
const {HoopAssembly} = await import(new URL(`./hoop-assembly.js${assetVersion}`, import.meta.url).href);
const {beginBallLook, advanceBallLook} = await import(new URL(`./rolling-ball.js${assetVersion}`, import.meta.url).href);

const artworkSize = { width:1448, height:1086 };
const boxSlots = { player:[.575, .32, .80], neko:[.415, .195, .68] };
const fourBallSlots = { player:[.64, .40, .88, .16], neko:[.36, .12, .60, .84] };
const perspective = .48;
const frontFloor = .937;
const rearFloor = .539;
const rampLipY = 887 / artworkSize.height;
const trayBackY = 958 / artworkSize.height;
const dividerFrontY = 957 / artworkSize.height;
const rampLip = (frontFloor - rampLipY)
  / ((frontFloor - rearFloor) * (1 + perspective) - (frontFloor - rampLipY) * perspective);
const dividerHalfWidth = 12 / artworkSize.width;
const gravity = 1.10;
const lateralDrag = -60 * Math.log(.998);
const hoopDepth = .90;
// The board face is just beyond the rear lip and in front of the cage wall.
const boardDepth = .978;
const depthScale = depth => 1 / (1 + perspective * depth);
const lateralTravel = seconds => -Math.expm1(-lateralDrag * seconds) / lateralDrag;
const contactGap = 1e-6;
const wedgeWobbleSeconds = .26;
const wedgeSettleSeconds = .08;
const rimRollSeconds = 4;
const depthPerForward = .89;
const risePerForward = 1.19;
// Forward strength, descending range at rim height, arrival time. Interpolate
// range and time together so a longer drag cannot accidentally shorten a shot.
// Ordinary throws stay low while gaining depth through the hoop opening.
// Firmer throws reach the board over a short, smooth transition.
const manualArc = [
  [0, 0, 0],
  [.15, .0433, .3245],
  [.30, .480, 1.00],
  [.40, .860, 1.38],
  [.46, .898, 1.44],
  [.55, .907, 1.47],
  [.65, .917, 1.50],
  [.73, .994, 1.55],
  [.85, 1.045, 1.58],
  [1.02, 1.110, 1.68]
];
// Match the response slope at each point. The weighted harmonic mean keeps
// each segment within its endpoints while smoothing the transitions.
const manualArcSlopes = manualArc.map((row, index) => {
  const left = manualArc[index - 1], right = manualArc[index + 1];
  return [1, 2].map(column => {
    if (!left) return (right[column] - row[column]) / (right[0] - row[0]);
    if (!right) return (row[column] - left[column]) / (row[0] - left[0]);
    const before = (row[column] - left[column]) / (row[0] - left[0]);
    const after = (right[column] - row[column]) / (right[0] - row[0]);
    if (before * after <= 0) return 0;
    const w1 = 2 * (right[0] - row[0]) + row[0] - left[0];
    const w2 = right[0] - row[0] + 2 * (row[0] - left[0]);
    return (w1 + w2) / (w1 / before + w2 / after);
  });
});
const ringPoints = Array.from({length:48}, (_, index) => {
  const angle = index * Math.PI * 2 / 48;
  return { x:Math.cos(angle), depth:Math.sin(angle) };
});
const clamp = (n, min, max) => Math.max(min, Math.min(max, n));

function manualForwardVelocity(forward, heightDelta) {
  const upper = manualArc.findIndex(([power]) => forward <= power);
  if (upper < 1) return { depth:forward * depthPerForward, rise:forward * risePerForward };
  const [low, nearRange, nearTime] = manualArc[upper - 1];
  const [high, farRange, farTime] = manualArc[upper];
  const fraction = (forward - low) / (high - low);
  const f2 = fraction ** 2, f3 = fraction ** 3;
  const interpolate = (a, b, column) =>
    (2 * f3 - 3 * f2 + 1) * a + (f3 - 2 * f2 + fraction) * (high - low) * manualArcSlopes[upper - 1][column]
    + (-2 * f3 + 3 * f2) * b + (f3 - f2) * (high - low) * manualArcSlopes[upper][column];
  const range = interpolate(nearRange, farRange, 0);
  const seconds = interpolate(nearTime, farTime, 1);
  // Tiny tosses must still fall short instead of becoming high, slow lobs.
  const lift = clamp((forward - .15) / .15, 0, 1);
  const height = heightDelta * lift * lift * (3 - 2 * lift);
  return { depth:range / seconds, rise:height / seconds + gravity * seconds / 2 };
}

function manualLateralVelocity(flight, hoopHeight, screenAim, screenHoop, sideEase) {
  const discriminant = flight.vHeight ** 2 - 2 * gravity * (hoopHeight - flight.height);
  if (discriminant <= 0 || sideEase === 1) return flight.vx;
  const seconds = (flight.vHeight + Math.sqrt(discriminant)) / gravity;
  const range = flight.vDepth * seconds;
  const reach = clamp((range - .50) / .20, 0, 1);
  if (!reach) return flight.vx;
  // Use the screen direction at hoop height, accounting for how perspective
  // shifts a moving ball toward the centre as it travels into the court.
  const diameter = 2 * flight.radius * depthScale(hoopDepth);
  const error = screenAim - screenHoop;
  const near = 1 - clamp((Math.abs(error) / diameter - .45) / .90, 0, 1);
  const correction = clamp(error * .25, -.20 * diameter, .20 * diameter) * near;
  const targetX = (screenAim - correction) / depthScale(range);
  const mappedVx = (targetX - flight.x) / lateralTravel(seconds);
  const blend = reach ** 2 * (3 - 2 * reach) * (1 - sideEase);
  return flight.vx + (mappedVx - flight.vx) * blend;
}

// Inside edges of the painted cage, in source-image pixels. The side wall
// widens toward the roof at the back, and is vertical at the front opening.
const frontHalfWidth = .5 - 52 / artworkSize.width;
const rearHalfWidth = (.5 - 390 / artworkSize.width) / depthScale(1);
const rearRoofHalfWidth = (.5 - 290 / artworkSize.width) / depthScale(1);
const rearRoofY = 64 / artworkSize.height;

function sideWall(direction) {
  return ({x, depth, height}, {rearRoofHeight}) => {
    const flare = (rearRoofHalfWidth - rearHalfWidth) / rearRoofHeight;
    const taper = rearHalfWidth - frontHalfWidth + flare * height;
    const halfWidth = frontHalfWidth + taper * depth;
    const length = Math.hypot(1, taper, flare * depth);
    return { distance:(halfWidth - direction * x) / length,
      nx:-direction / length, nd:taper / length, nh:flare * depth / length };
  };
}

const rightWall = { surface:sideWall(1), restitution:.76 };
const backWall = { surface:({depth}) => ({ distance:1 - depth, nx:0, nd:-1, nh:0 }), restitution:.45 };
const floor = { surface:({height}) => ({ distance:height, nx:0, nd:0, nh:1 }), restitution:0 };
const cage = [
  { surface:sideWall(-1), restitution:.76 },
  rightWall,
  { surface:({height, depth}, {frontRoofHeight, roofSlope, roofNormalLength}) => ({
    distance:(frontRoofHeight + roofSlope * depth - height) / roofNormalLength,
    nx:0, nd:roofSlope / roofNormalLength, nh:-1 / roofNormalLength
  }), restitution:.74 },
  backWall,
  { surface:({depth}) => ({ distance:depth, nx:0, nd:1, nh:0 }), restitution:0, centreOnly:true },
  floor
];

function flightPosition({x, depth, height, vx, vDepth, vHeight}, seconds) {
  return { x:x + vx * lateralTravel(seconds), depth:depth + vDepth * seconds,
    height:height + vHeight * seconds - gravity * seconds ** 2 / 2 };
}

function nearestRingOffset(x, depth, ring) {
  let nearestX, nearestDepth, nearestSquared = Infinity;
  // The short segments give the ring a continuous tube, including the
  // front and rear lips that a pair of screen-space circles cannot cover.
  for (let index = 0; index < ringPoints.length; index++) {
    const a = ringPoints[index], b = ringPoints[(index + 1) % ringPoints.length];
    const dx = x - a.x * ring.halfWidth, dd = depth - a.depth * ring.halfDepth;
    const sx = (b.x - a.x) * ring.halfWidth, sd = (b.depth - a.depth) * ring.halfDepth;
    const t = clamp((dx * sx + dd * sd) / (sx ** 2 + sd ** 2), 0, 1);
    const offX = dx - sx * t, offDepth = dd - sd * t;
    const squared = offX ** 2 + offDepth ** 2;
    if (squared < nearestSquared) {
      nearestSquared = squared;
      nearestX = offX;
      nearestDepth = offDepth;
    }
  }
  return { dx:nearestX, dd:nearestDepth };
}

function fitsHoopOpening(x, depth, ring, clearance) {
  x -= ring.x;
  depth -= ring.depth;
  if ((x / ring.halfWidth) ** 2 + (depth / ring.halfDepth) ** 2 >= 1) return false;
  const { dx, dd } = nearestRingOffset(x, depth, ring);
  return Math.hypot(dx, dd) >= clearance + ring.tube - contactGap;
}

function contactSurface(dx, dd, dh, thickness = 0) {
  const length = Math.hypot(dx, dd, dh);
  return length > 0
    ? { distance:length - thickness, nx:dx / length, nd:dd / length, nh:dh / length }
    : { distance:-thickness, nx:0, nd:-1, nh:0 };
}

const rim = {
  surface({x, depth, height}, ring) {
    const { dx, dd } = nearestRingOffset(x - ring.x, depth - hoopDepth, ring);
    return contactSurface(dx, dd, height - ring.height, ring.tube);
  },
  restitution:.45
};

const backboard = {
  surface({x, depth, height}, board) {
    const dx = x - board.x, dh = height - board.height;
    const ax = Math.abs(dx), ah = Math.abs(dh);
    let px = Math.min(ax, board.halfWidth), ph = Math.min(ah, board.halfHeight);
    const bevelX = board.halfWidth * .13, bevelH = board.halfHeight * .20;
    const startX = board.halfWidth - bevelX;
    if ((px - startX) / bevelX + (ph - board.halfHeight) / bevelH > 0) {
      const t = clamp(((ax - startX) * bevelX - (ah - board.halfHeight) * bevelH)
        / (bevelX ** 2 + bevelH ** 2), 0, 1);
      px = startX + t * bevelX;
      ph = board.halfHeight - t * bevelH;
    }
    return contactSurface(Math.sign(dx) * (ax - px), depth - boardDepth, Math.sign(dh) * (ah - ph));
  },
  // Let a bank shot fall toward the rim before it rebounds too far forward.
  restitution:.35
};

const wedgedBallSurface = {
  surface({ x, depth, height }, target) {
    return contactSurface(x - target.x, depth - target.depth, height - target.height, target.radius);
  },
  restitution:.32
};

function resolveHoopContact(flight, wall, normal, closing, time, onImpact) {
  const { nx, nd, nh } = normal;
  const kind = wall === backboard ? 'board' : 'rim';
  const previous = flight.hoopContacts?.[kind];
  const elapsed = previous ? Math.max(0, time - previous.time) : Infinity;
  const nearby = previous && Math.hypot(flight.x - previous.x,
    flight.depth - previous.depth, flight.height - previous.height) < flight.radius * 2.5;
  const continuous = nearby && elapsed < .075;
  if (!continuous) onImpact?.();
  const speed = clamp((closing - .15) / .65, 0, 1);
  const weight = speed * speed * (3 - 2 * speed);
  let restitution = wall === backboard ? wall.restitution : .15 + (wall.restitution - .15) * weight;
  if (wall === rim && closing < .10) restitution = 0;
  // A resting/sliding contact should not restart a fresh spring every frame.
  if (continuous) restitution = closing < .35 ? 0 : Math.min(restitution, .15);
  const retention = continuous ? Math.exp(-(wall === backboard ? 4 : 6) * elapsed)
    : wall === backboard ? .85 : .80 + .10 * weight;
  // Lose energy both across and along the surface. Applying friction by elapsed
  // contact time keeps a run of small contacts comparable across frame rates.
  flight.vx = (flight.vx + closing * nx) * retention + closing * restitution * nx;
  flight.vDepth = (flight.vDepth + closing * nd) * retention + closing * restitution * nd;
  flight.vHeight = (flight.vHeight + closing * nh) * retention + closing * restitution * nh;
  (flight.hoopContacts ||= {})[kind] = { time, x:flight.x, depth:flight.depth, height:flight.height };
  return wall === rim && restitution === 0 && nh > 0
    && Math.hypot(flight.vx, flight.vDepth, flight.vHeight) < .35;
}

function slideRimContact(flight, ring, seconds, time) {
  const from = { x:flight.x, depth:flight.depth, height:flight.height };
  Object.assign(flight, flightPosition(flight, seconds));
  flight.vx *= Math.exp(-lateralDrag * seconds);
  flight.vHeight -= gravity * seconds;
  const { distance, nx, nd, nh } = rim.surface(flight, ring);
  if (distance < flight.radius + contactGap) {
    const correction = flight.radius + 2 * contactGap - distance;
    flight.x += nx * correction;
    flight.depth += nd * correction;
    flight.height += nh * correction;
    // Support cancels motion into the tube; gravity along it still slides the
    // ball off naturally. Spend real time instead of bouncing at zero time.
    const incoming = Math.min(0, flight.vx * nx + flight.vDepth * nd + flight.vHeight * nh);
    flight.vx -= incoming * nx;
    flight.vDepth -= incoming * nd;
    flight.vHeight -= incoming * nh;
    const retention = Math.exp(-6 * seconds);
    flight.vx *= retention;
    flight.vDepth *= retention;
    flight.vHeight *= retention;
  }
  flight.hoopContacts.rim = { time, x:flight.x, depth:flight.depth, height:flight.height };
  return { from, to:{ x:flight.x, depth:flight.depth, height:flight.height } };
}

function firstHoopImpact(flight, seconds, hoop, wedged) {
  // Distance to a solid can shrink by at most the ball's travel. Use that
  // bound to look for contacts even when both ends of a step miss the rim.
  const speed = Math.hypot(flight.vx, flight.vDepth,
    Math.max(Math.abs(flight.vHeight), Math.abs(flight.vHeight - gravity * seconds)));
  if (flight.depth + speed * seconds + flight.radius < hoopDepth - hoop.ring.halfDepth - hoop.ring.tube) return null;
  let first = null;
  const solids = [[backboard, hoop.board], [rim, hoop.ring]];
  if (wedged?.poolState === 'wedged') solids.push([wedgedBallSurface, wedged.flight]);
  for (const [wall, shape] of solids) {
    let time = 0;
    for (let sample = 0; sample < 64 && time <= (first?.seconds ?? seconds); sample++) {
      const position = flightPosition(flight, time);
      const surface = wall.surface(position, shape);
      const gap = surface.distance - flight.radius;
      if (gap <= contactGap) {
        first = { wall, radius:flight.radius, seconds:time, surface };
        break;
      }
      time += gap / speed;
    }
  }
  return first;
}

function firstCageImpact(flight, seconds, geometry, allowOuterExit) {
  const end = flightPosition(flight, seconds);
  let first = null;
  for (const wall of cage) {
    if (wall === rightWall && allowOuterExit) continue;
    const radius = wall.centreOnly ? 0 : flight.radius;
    if (wall.surface(end, geometry).distance >= radius) continue;
    let before = 0;
    let after = seconds;
    if (wall.surface(flight, geometry).distance >= radius) {
      // Find contact along the moving ball's path before spending the remainder
      // of the step on its reflected path. This also orders corner impacts.
      for (let i = 0; i < 12; i++) {
        const middle = (before + after) / 2;
        if (wall.surface(flightPosition(flight, middle), geometry).distance < radius) after = middle;
        else before = middle;
      }
    } else {
      after = 0;
    }
    if (!first || after < first.seconds) first = { wall, radius, seconds:after };
  }
  return first;
}

function loadLayer(name, x, y) {
  const image = new Image();
  image.decoding = 'async';
  image.src = new URL(`./assets/neko-stage-four-${name}.webp${assetVersion}`, import.meta.url).href;
  return { image, x, y };
}

export class SharedCourt {
  constructor(player, neko) {
    this.lanes = [player, neko];
    this.clock = 0;
    // Stage one intentionally keeps the shared target stationary and full-size.
    this.stage = 1;
    this.fixedHoop = true;
    this.rimScale = .9;
    this.rightOpening = null;
    this.assembly = new HoopAssembly();
    this.boxLayers = [loadLayer('player-box', 36, 887), loadLayer('neko-box', 736, 887), loadLayer('divider', 710, 887)];
    this.frontLayer = loadLayer('front', 0, 957);
    for (const lane of this.lanes) {
      lane.sharedCourt = this;
      lane.stageClock = 0;
    }
  }

  get width() { return this.lanes.reduce((sum, lane) => sum + lane.width, 0); }
  get ballRadius() { return this.width * .034; }
  get hoopDepth() { return hoopDepth; }
  get offset() { return this.lanes.reduce((sum, lane) => sum + lane.hoopOffset, 0); }
  origin(lane) { return lane === this.lanes[0] ? 0 : this.lanes[0].width; }
  bounds(lane) { return { left:-this.origin(lane), right:this.width - this.origin(lane) }; }

  readyPosition(lane, slot = 0) {
    const slots = lane.poolCapacity === 4 ? fourBallSlots : boxSlots;
    return { x:lane.width * slots[lane.side][slot], y:lane.height * .892 };
  }

  floorY(lane, depth) {
    return lane.height * (frontFloor - (frontFloor - rearFloor) * (1 + perspective) * depth * depthScale(depth));
  }

  hoopHeight(lane) {
    return this.heightAt(lane, this.hoopPose(lane).rimY, hoopDepth);
  }

  heightAt(lane, y, depth) {
    return (this.floorY(lane, depth) - y) / (this.width * depthScale(depth));
  }

  hoopGeometry(lane) {
    const pose = this.hoopPose(lane);
    const height = this.hoopHeight(lane);
    const rimPixels = this.width * depthScale(hoopDepth);
    const boardPixels = this.width * depthScale(boardDepth);
    const rect = this.assembly.boardRect(pose);
    const floorPerspectiveSlope = lane.height * (frontFloor - rearFloor) * (1 + perspective);
    const rimScreenSlope = (floorPerspectiveSlope - this.width * height * perspective) * depthScale(hoopDepth) ** 2;
    const halfDepth = pose.rimW * .089 / rimScreenSlope;
    const mountDepth = hoopDepth + halfDepth * .54;
    const mountPixels = this.width * depthScale(mountDepth);
    // Fit the centreline and tube to the existing art, including the short
    // axis visible in the ellipse, then use the same opening for scoring.
    return {
      ring: { x:this.offset / rimPixels, depth:hoopDepth, height, halfWidth:pose.rimW * .475 / rimPixels,
        halfDepth, tube:pose.rimW * .027 / rimPixels },
      // The protruding red ledge on the existing centre bracket.
      mount: { front:hoopDepth + halfDepth * .24, halfWidth:pose.rimW * .13 / mountPixels,
        height:this.heightAt(lane, pose.rimY - pose.rimW * .132, mountDepth) },
      board: { x:this.offset / boardPixels, height:this.heightAt(lane, rect.y + rect.height / 2, boardDepth),
        halfWidth:rect.width / (2 * boardPixels), halfHeight:rect.height / (2 * boardPixels) }
    };
  }

  startFlight(lane, ball, aim) {
    const radius = ball.r / this.width;
    const flight = ball.flight = {
      x:(ball.x + this.origin(lane)) / this.width - .5,
      depth:0,
      height:Math.max(radius, (this.floorY(lane, 0) - ball.y) / this.width),
      radius
    };
    if (!aim) {
      // A resting ball knocked loose by a spatial shot starts at the mouth.
      flight.vx = ball.vx / this.width;
      flight.vDepth = 0;
      flight.vHeight = -ball.vy / this.width;
    } else if (aim.target) {
      const { x, y, depth } = aim.target;
      const pixels = this.width * depthScale(depth);
      const targetX = (x + this.origin(lane) - this.width / 2) / pixels;
      const targetHeight = Math.max(flight.radius, (this.floorY(lane, depth) - y) / pixels);
      flight.vx = (targetX - flight.x) / lateralTravel(aim.seconds);
      flight.vDepth = depth / aim.seconds;
      flight.vHeight = (targetHeight - flight.height) / aim.seconds + gravity * aim.seconds / 2;
    } else if (aim.auto) {
      const seconds = aim.seconds;
      const targetX = this.offset / (this.width * depthScale(hoopDepth));
      flight.vx = (targetX + aim.error / (this.width * depthScale(hoopDepth)) - flight.x) / lateralTravel(seconds);
      flight.vDepth = hoopDepth / seconds;
      flight.vHeight = (this.hoopHeight(lane) - flight.height) / seconds + gravity * seconds / 2;
    } else {
      const forward = Math.max(0, -aim.vy / 900);
      const sideways = aim.vx / 900;
      const pose = this.hoopPose(lane);
      const hoopHeight = this.heightAt(lane, pose.rimY, hoopDepth);
      const screenHoop = this.offset / this.width;
      const hoopX = screenHoop / depthScale(hoopDepth);
      const sideRatio = forward > 0 ? Math.abs(sideways) / forward : Infinity;
      const sideBlend = clamp((sideRatio - 1.2) / .8, 0, 1);
      const sideEase = sideBlend ** 2 * (3 - 2 * sideBlend);
      const power = Math.min(1, Math.hypot(forward, sideways));
      const towardHoop = sideways * (hoopX - flight.x) > 0;
      // Aiming inward from an outer pickup needs a steeper diagonal on screen.
      // Use the power shown by the ring for these throws at every strength.
      const strength = towardHoop ? forward + Math.max(0, power - forward) * (1 - sideEase) : forward;
      const { depth, rise } = manualForwardVelocity(strength, hoopHeight - flight.height);
      flight.vx = sideways * 1.08;
      flight.vDepth = depth;
      flight.vHeight = rise + .50 * Math.max(0, Math.abs(sideways) - forward) * sideEase;
      if (forward > 0 && sideEase < 1) {
        const { x, y } = lane.inputScale;
        const dragSlope = (sideways * y) / (forward * x);
        const screenAim = flight.x + dragSlope * (ball.y - pose.rimY) / this.width;
        flight.vx = manualLateralVelocity(flight, hoopHeight, screenAim, screenHoop, sideEase);
      }
    }
    this.projectFlight(lane, ball);
  }

  projectFlight(lane, ball, flight = ball.flight) {
    const scale = depthScale(flight.depth);
    const scaleSpeed = -perspective * scale * scale * flight.vDepth;
    const floorSpeed = -lane.height * (frontFloor - rearFloor) * (1 + perspective) * scale * scale * flight.vDepth;
    const projected = {
      x:this.width * (.5 + flight.x * scale) - this.origin(lane),
      y:this.floorY(lane, flight.depth) - this.width * flight.height * scale,
      vx:this.width * (flight.vx * scale + flight.x * scaleSpeed),
      vy:floorSpeed - this.width * (flight.vHeight * scale + flight.height * scaleSpeed)
    };
    Object.assign(ball, projected, { r:flight.radius * this.width * scale });
    if (flight === ball.flight) flight.projected = projected;
    else flight.drawLift = (flight.lift || 0) * this.width * scale;
  }

  advanceFlight(lane, ball, dt) {
    return this.#advanceSpatialFlight(lane, ball, dt, false);
  }

  #advanceSpatialFlight(lane, ball, dt, preview) {
    const flight = ball.flight;
    // Avatar hits and ball clashes act on the projected ball. Bring their
    // changes back into world coordinates before finding the next contact.
    const projected = flight.projected;
    if (projected && !preview) {
      const pixels = this.width * depthScale(flight.depth);
      flight.x += (ball.x - projected.x) / pixels;
      flight.height -= (ball.y - projected.y) / pixels;
      flight.vx += (ball.vx - projected.vx) / pixels;
      flight.vHeight -= (ball.vy - projected.vy) / pixels;
    }
    const frontRoofHeight = lane.height * frontFloor / this.width;
    const rearRoofHeight = lane.height * (rearFloor - rearRoofY) / (this.width * depthScale(1));
    const roofSlope = rearRoofHeight - frontRoofHeight;
    const geometry = { frontRoofHeight, rearRoofHeight, roofSlope, roofNormalLength:Math.hypot(1, roofSlope) };
    const hoop = this.hoopGeometry(lane);
    const wedgeLane = preview ? null : this.lanes.find(source => source.ballPool?.some(candidate => candidate.poolState === 'wedged'));
    const wedged = wedgeLane?.ballPool.find(candidate => candidate.poolState === 'wedged');
    if (ball.allowOuterExit && rightWall.surface(flight, geometry).distance > flight.radius + contactGap) {
      ball.allowOuterExit = false;
    }
    const segments = [];
    let remaining = dt;
    // Bound repeated contact at a corner, with room for short supported steps
    // when a slow ball slides around the curved rim.
    for (let contacts = 0; remaining > 0 && contacts < 24; contacts++) {
      const cageHit = firstCageImpact(flight, remaining, geometry, ball.allowOuterExit);
      const hit = firstHoopImpact(flight, cageHit?.seconds ?? remaining, hoop, wedged) || cageHit;
      const seconds = hit ? hit.seconds : remaining;
      const from = { x:flight.x, depth:flight.depth, height:flight.height };
      const to = flightPosition(flight, seconds);
      const time = ball.life + dt - remaining;
      if (!preview && !wedged && this.tryMount(lane, ball, hoop, from, to, { time, dt, remaining })) {
        segments.push({ from, to:{ x:flight.x, depth:flight.depth, height:flight.height } });
        break;
      }
      if (seconds > 0) segments.push({ from, to });
      Object.assign(flight, to);
      flight.vx *= Math.exp(-lateralDrag * seconds);
      flight.vHeight -= gravity * seconds;
      ball.rotation = (ball.rotation || 0) + (to.x - from.x) / flight.radius * .55;
      remaining -= seconds;
      if (!hit) break;
      const { wall, radius } = hit;
      const { distance, nx, nd, nh } = hit.surface ?? wall.surface(flight, geometry);
      const normalSpeed = flight.vx * nx + flight.vDepth * nd + flight.vHeight * nh;
      if (wall === rightWall && normalSpeed < 0) {
        const pixels = this.width * depthScale(flight.depth);
        const y = this.floorY(lane, flight.depth) - flight.height * pixels;
        if (this.canExitRight(ball, flight.depth, y, flight.radius * pixels)) {
          ball.allowOuterExit = true;
          this.onExit?.(ball, lane);
          continue;
        }
      }
      if (wall === floor && normalSpeed <= 0) {
        flight.height = radius;
        break;
      }
      const correction = radius - distance + 2 * contactGap;
      flight.x += nx * correction;
      flight.depth += nd * correction;
      flight.height += nh * correction;
      if (normalSpeed < 0) {
        let sliding = false;
        if (wall === backboard || wall === rim) {
          const impactTime = time + seconds;
          if (!preview && wall === backboard && normalSpeed < -.12
              && impactTime - (flight.hoopContacts?.board?.time ?? -Infinity) > .10) {
            const { x, height, halfWidth, halfHeight } = hoop.board;
            this.assembly.markBoardImpact(
              clamp(.5 + (flight.x - nx * radius - x) / (2 * halfWidth), 0, 1),
              clamp(.5 - (flight.height - nh * radius - height) / (2 * halfHeight), 0, 1)
            );
            this.onBoardImpact?.();
          }
          const onImpact = !preview && wall === rim ? () => {
            const { x, depth, halfWidth, halfDepth } = hoop.ring;
            const side = clamp((flight.x - nx * radius - x) / halfWidth, -1, 1);
            const front = clamp((depth + halfDepth - (flight.depth - nd * radius)) / (2 * halfDepth), 0, 1);
            this.assembly.markRimImpact(side, -normalSpeed, nh, remaining, front);
            if (wedged?.poolState === 'wedged') {
              this.shakeWedgedBall(lane, ball, wedged, hoop.ring,
                { side, front, closing:-normalSpeed, nx, nh, age:remaining });
            }
          } : undefined;
          sliding = resolveHoopContact(flight, wall, { nx, nd, nh }, -normalSpeed, impactTime, onImpact);
          if (!preview && !wedged && !sliding && (wall === backboard || nh > 0)
              && this.tryShoulderSeat(lane, ball, hoop.ring, { time:impactTime, dt, remaining })) break;
        } else if (wall === wedgedBallSurface) {
          this.hitWedgedBall(lane, ball, wedgeLane, wedged, hoop.ring, { nx, nd, nh }, -normalSpeed);
        } else {
          const impulse = -(1 + wall.restitution) * normalSpeed;
          flight.vx += nx * impulse;
          flight.vDepth += nd * impulse;
          flight.vHeight += nh * impulse;
        }
        if (wall.restitution && wall !== backWall) ball.hitRim = true;
        if (sliding && remaining > 0) {
          // Short constrained steps handle curved support without skipping a
          // nearby surface or spending the frame's contact budget on micro-bounces.
          const step = Math.min(remaining, 1 / 240);
          const segment = slideRimContact(flight, hoop.ring, step, ball.life + dt - remaining + step);
          segments.push(segment);
          ball.rotation += (segment.to.x - segment.from.x) / flight.radius * .55;
          remaining -= step;
          if (!preview && !wedged
              && this.tryShoulderSeat(lane, ball, hoop.ring, { time:ball.life + dt - remaining, dt, remaining })) break;
        }
      }
    }
    ball.life += dt;
    if (!preview) this.projectFlight(lane, ball);
    return segments;
  }

  canSettle(ball, time) {
    const { flight } = ball;
    return !ball.scored && !flight.enteredHoop && !flight.wedgePartner && time >= (flight.rollPriorityUntil ?? -Infinity);
  }

  tryShoulderSeat(lane, ball, ring, timing) {
    const flight = ball.flight;
    const { time } = timing;
    const contacts = flight.hoopContacts;
    if (!contacts?.board || !contacts.rim || time - contacts.board.time > .16 || time - contacts.rim.time > .12
        || !this.canSettle(ball, time) || Math.hypot(flight.vx, flight.vDepth, flight.vHeight) > .49) return false;

    const { x, radius } = flight;
    const depth = boardDepth - radius - 2 * contactGap;
    const maxSettleDistance = radius * .70;
    if (Math.abs(flight.depth - depth) > maxSettleDistance) return false;
    const { dx, dd } = nearestRingOffset(x - ring.x, depth - ring.depth, ring);
    const contactRadius = radius + ring.tube;
    const liftSquared = contactRadius ** 2 - dx ** 2 - dd ** 2;
    // Only the rear shoulders can push a ball back against the board.
    if (dd < contactRadius * .08 || liftSquared < (radius * .42) ** 2) return false;
    const height = ring.height + Math.sqrt(liftSquared);
    if (Math.hypot(flight.depth - depth, flight.height - height) > maxSettleDistance) return false;

    return this.settleWedge(lane, ball, ring, flight, timing, 'shoulder', depth, height);
  }

  tryMount(lane, ball, { mount, ring }, from, to, { time, dt, remaining }) {
    const flight = ball.flight;
    const height = mount.height + flight.radius;
    if (from.height < height || to.height >= height || !this.canSettle(ball, time)) return false;
    const seconds = (flight.vHeight + Math.sqrt(flight.vHeight ** 2 + 2 * gravity * (from.height - height))) / gravity;
    const contact = flightPosition(flight, seconds);
    if (Math.abs(contact.x - ring.x) > mount.halfWidth || contact.depth < mount.front
        || contact.depth > boardDepth - flight.radius + contactGap) return false;
    const landing = { ...flight, ...contact, vHeight:flight.vHeight - gravity * seconds,
      vx:flight.vx * Math.exp(-lateralDrag * seconds) };
    if (Math.abs(landing.vx) > .24 || Math.abs(landing.vDepth) > .76 || landing.vHeight < -.85) return false;
    return this.settleWedge(lane, ball, ring, landing, { time:time + seconds, dt, remaining:remaining - seconds },
      'mount', contact.depth, height);
  }

  settleWedge(lane, ball, ring, arrival, { time, dt, remaining }, kind, depth, height) {
    // Continue the same unassisted flight in a scratch ball before allowing
    // either seat to interrupt it. The probe shares the actual scoring test.
    const probe = { flight:{ ...arrival, hoopContacts:{ ...arrival.hoopContacts } }, life:time };
    for (let elapsed = 0, step = remaining || dt; elapsed < rimRollSeconds; elapsed += step, step = dt) {
      const segments = this.#advanceSpatialFlight(lane, probe, step, true);
      if (this.crossesHoop(lane, probe, segments)) {
        ball.flight.rollPriorityUntil = time + rimRollSeconds;
        return false;
      }
      if (probe.flight.height < ring.height - probe.flight.radius - ring.tube && probe.flight.vHeight <= 0) break;
    }
    Object.assign(ball.flight, { x:arrival.x, depth:arrival.depth, height:arrival.height, vx:0, vDepth:0, vHeight:0,
      enteredHoop:false, wedgeKind:kind, wedgeDepth:depth, wedgeOffset:arrival.x - ring.x, wedgeLift:height - ring.height,
      wedgeSettle:{ depth:arrival.depth - depth, height:arrival.height - height, age:0 } });
    Object.assign(ball, { flying:false, poolState:'wedged', hitRim:true });
    return true;
  }

  hitWedgedBall(lane, striker, targetLane, target, ring, { nx, nd, nh }, closing) {
    const flight = target.flight;
    const transfer = closing * .58;
    let vx = -nx * transfer;
    let vDepth = Math.min(0, -nd * transfer);
    let vHeight = -nh * transfer;
    // The board absorbs the inward shove. Project any remaining force that
    // presses into the rim along its surface before judging whether it frees the ball.
    let freed;
    if (flight.wedgeKind === 'mount') {
      freed = closing >= .14 && (Math.abs(nx) >= .26 || nh >= .26 || nd > .16);
      // Once tipped over the ledge, the bracket slopes down toward the hoop.
      vDepth = Math.min(vDepth, -.10);
    } else {
      const support = rim.surface(flight, ring);
      const intoRim = Math.min(0, vx * support.nx + vDepth * support.nd + vHeight * support.nh);
      const tangent = intoRim / (support.nx ** 2 + support.nh ** 2);
      vx -= tangent * support.nx;
      vHeight -= tangent * support.nh;
      const sideKnock = Math.abs(nx) >= .50 && closing >= .30 && Math.abs(vx) >= .105;
      freed = sideKnock || (closing >= .40 && Math.hypot(vx, vDepth, vHeight) >= .19);
    }
    const rebound = freed ? transfer : closing * (1 + wedgedBallSurface.restitution);
    striker.flight.vx += rebound * nx;
    striker.flight.vDepth += rebound * nd;
    striker.flight.vHeight += rebound * nh;
    if (!freed) {
      if (!flight.wedgeWobble && closing >= .16) {
        const depth = flight.depth + nd * flight.radius;
        const pixels = this.width * depthScale(depth);
        const x = this.width / 2 + pixels * (flight.x + nx * flight.radius) - this.origin(lane);
        const y = this.floorY(lane, depth) - pixels * (flight.height + nh * flight.radius);
        lane.burst(x, y, 4);
      }
      const rotation = flight.wedgeWobble?.rotation ?? target.rotation ?? 0;
      const maxShift = flight.radius * .36;
      flight.wedgeWobble = { rotation, angle:(nx >= 0 ? 1 : -1) * Math.min(.13, closing * .20),
        shift:clamp(vx * .22, -maxShift, maxShift), age:0 };
      return;
    }

    this.releaseWedgedBall(lane, striker, targetLane, target, { vx, vDepth, vHeight });
  }

  shakeWedgedBall(lane, striker, target, ring, { side, front, closing, nx, nh, age }) {
    const flight = target.flight;
    const onMount = flight.wedgeKind === 'mount';
    const targetSide = clamp(flight.wedgeOffset / ring.halfWidth, -1, 1);
    // One real impact carries a shock through the support. There is no hit
    // counter: distant glances cannot accumulate into an inevitable release.
    const proximity = onMount ? 1 - .35 * Math.abs(side)
      : 1 - .85 * clamp(Math.abs(side - targetSide) / 1.3, 0, 1);
    const strength = closing * (.45 + .55 * front) * proximity * (.65 + .35 * Math.abs(nh));
    if (strength < (onMount ? .24 : .42) || strength <= (flight.wedgeShake?.strength ?? 0)) return;
    flight.wedgeShake = { strength, age, source:{ id:striker.id, owner:striker.owner, fever:striker.fever },
      sourceSide:lane.side, vx:clamp(-nx * closing * .12, -.10, .10),
      vDepth:onMount ? -.10 : -.07 - strength * .10, vHeight:-strength * .08 };
  }

  releaseWedgedBall(lane, striker, targetLane, target, { vx, vDepth, vHeight }) {
    const flight = target.flight;
    const changedOwner = target.owner !== striker.owner;
    Object.assign(target, { owner:striker.owner, fever:striker.fever, nativeShot:false,
      flying:true, poolState:'flying', life:0, hitRim:true, clashed:true });
    Object.assign(flight, { vx, vDepth, vHeight, enteredHoop:false, wedgePartner:striker.id });
    delete flight.wedgeWobble;
    delete flight.wedgeShake;
    delete flight.wedgeSettle;
    delete flight.wedgeKind;
    delete flight.wedgeDepth;
    delete flight.wedgeOffset;
    delete flight.wedgeLift;
    delete flight.hoopContacts;
    this.projectFlight(targetLane, target);
    lane.burst(target.x + this.origin(targetLane) - this.origin(lane), target.y, 8);
    lane.onBallClash?.({ owner:target.owner, ownershipChanged:changedOwner });
  }

  crossesHoop(lane, ball, segments) {
    const { ring } = this.hoopGeometry(lane);
    const flight = ball.flight;
    const exitHeight = ring.height - flight.radius - ring.tube;
    const fitsAt = (from, to, height, clearance) => {
      const t = (from.height - height) / (from.height - to.height);
      return fitsHoopOpening(from.x + (to.x - from.x) * t,
        from.depth + (to.depth - from.depth) * t, ring, clearance);
    };
    for (const { from, to } of segments) {
      if (to.height > ring.height) flight.enteredHoop = false;
      if (to.height >= from.height) continue;
      if (from.height >= ring.height && to.height <= ring.height) {
        flight.enteredHoop = fitsAt(from, to, ring.height, flight.radius);
      }
      if (flight.enteredHoop && from.height >= exitHeight && to.height <= exitHeight) {
        flight.enteredHoop = false;
        if (fitsAt(from, to, exitHeight, 0)) return true;
      }
    }
    return false;
  }

  drawLayer(lane, { image, x, y }) {
    if (!image.complete || !image.naturalWidth) return;
    const scaleX = this.width / artworkSize.width;
    const scaleY = lane.height / artworkSize.height;
    lane.ctx.drawImage(image, x * scaleX - this.origin(lane), y * scaleY,
      image.naturalWidth * scaleX, image.naturalHeight * scaleY);
  }

  captureBallPool() {
    if (!this.poolBalls && this.lanes.every(lane => lane.ballPool)) {
      this.poolBalls = this.lanes.flatMap(lane => lane.ballPool);
    }
  }

  resetBallPools() {
    if (!this.poolBalls) return;
    let offset = 0;
    for (const lane of this.lanes) {
      lane.ballPool = this.poolBalls.slice(offset, offset + lane.poolCapacity);
      offset += lane.poolCapacity;
      lane.resetBallPool();
    }
  }

  moveSupplyBall(ball, source, target) {
    if (source.isHeldBall(ball)) return false;
    if (source === target) return;
    const translation = this.origin(source) - this.origin(target);
    source.ballPool.splice(source.ballPool.indexOf(ball), 1);
    target.ballPool.push(ball);
    ball.x += translation;
    ball.poolSlot = null;
    ball.readyPosition = null;
    source.selectReadyBall();
    target.selectReadyBall();
  }

  beginReturn(lane, ball) {
    const flight = ball.flight;
    const radius = flight?.radius ?? ball.r / this.width;
    const depth = flight?.depth ?? 0;
    ball.roll = {
      x:flight?.x ?? (ball.x + this.origin(lane)) / this.width - .5,
      depth, height:flight?.height ?? radius, radius,
      vx:(flight?.vx ?? ball.vx / this.width) * .42,
      vDepth:(flight?.vDepth ?? 0) * .42,
      vHeight:depth < rampLip ? Math.min(0, flight?.vHeight ?? 0) : 0,
      phase:depth >= rampLip ? 'ramp' : 'drop',
      bounceSpeed:depth >= rampLip ? clamp(-(flight?.vHeight ?? 0) * .095, 0, .095) : 0,
      side:0, quiet:0
    };
    beginBallLook(ball);
    delete ball.flight;
    ball.poolState = 'returning';
    ball.flying = false;
    ball.inTransit = false;
    this.projectFlight(lane, ball, ball.roll);
  }

  returnBounds(lane, ball, roll) {
    const { radius } = roll;
    const edge = frontHalfWidth + (rearHalfWidth - frontHalfWidth) * roll.depth - radius;
    if (ball.allowOuterExit && roll.x <= edge) ball.allowOuterExit = false;
    const left = -edge, right = ball.allowOuterExit ? Infinity : edge;
    if (roll.x < left || roll.x > right) {
      roll.x = clamp(roll.x, left, right);
      if (roll.vx * roll.x > 0) roll.vx *= -.30;
    }
    if (roll.depth < 0 || roll.depth > 1 - radius) {
      roll.depth = clamp(roll.depth, 0, 1 - radius);
      if (roll.depth === 0 && roll.vDepth < 0) roll.vDepth *= roll.vDepth < -.12 ? -.13 : 0;
      if (roll.depth > 0 && roll.vDepth > 0) roll.vDepth *= -.24;
    }
    if (roll.phase === 'ramp' || ball.allowOuterExit) return;
    if (roll.depth > rampLip) {
      roll.depth = rampLip;
      if (roll.vDepth > 0) roll.vDepth *= -.24;
    }
    const dividerY = lane.height * (dividerFrontY + (rampLipY - dividerFrontY) * roll.depth / rampLip);
    const clearance = Math.max(0, roll.height - this.heightAt(lane, dividerY, roll.depth));
    if (roll.phase === 'tray') {
      const support = radius + this.heightAt(lane, trayBackY * lane.height, rampLip) * clamp(roll.depth / rampLip, 0, 1);
      if (roll.height < support) {
        roll.height = support;
        if (roll.vHeight < 0) roll.vHeight = 0;
      }
      roll.stacked = roll.height - support > .0001;
    }
    if (clearance >= radius) {
      roll.side = Math.sign(roll.x) || roll.side;
      return;
    }
    roll.side ||= Math.sign(roll.x) || Math.sign(roll.vx) || -1;
    const gap = dividerHalfWidth + Math.sqrt(radius ** 2 - clearance ** 2);
    if (roll.side * roll.x < gap) {
      roll.x = roll.side * gap;
      if (roll.side * roll.vx < 0) roll.vx *= -.25;
    }
  }

  advanceRoll(lane, ball, roll, dt, trayDrop) {
    if (roll.bounceSpeed || roll.lift) {
      roll.lift = Math.max(0, (roll.lift || 0) + roll.bounceSpeed * dt - gravity * dt ** 2 / 2);
      roll.bounceSpeed = roll.lift ? roll.bounceSpeed - gravity * dt : 0;
    }
    if (ball.allowOuterExit) roll.vx = Math.max(-.9, Math.min(-.30, roll.vx - 1.4 * dt));
    else roll.vx *= Math.exp(-(roll.phase === 'tray' ? 5 : 1.2) * dt);
    const previousX = roll.x, previousDepth = roll.depth;
    roll.x += roll.vx * dt;
    if (roll.phase === 'ramp') {
      roll.vDepth = Math.max(-.85, roll.vDepth - 1.15 * dt);
      roll.depth += roll.vDepth * dt;
      roll.height = roll.radius;
      if (roll.depth <= rampLip) {
        roll.phase = 'drop';
        roll.vHeight = -.10;
        roll.vDepth *= .72;
      }
    } else {
      roll.vDepth = (roll.vDepth - (roll.phase === 'tray' ? 1.05 : .35) * dt)
        * Math.exp(-(roll.phase === 'tray' ? 5.5 : .4) * dt);
      roll.depth += roll.vDepth * dt;
      if (roll.phase === 'drop' || roll.phase === 'tray') {
        roll.vHeight -= gravity * dt;
        roll.height += roll.vHeight * dt;
      }
      const support = roll.radius - trayDrop * clamp(roll.depth / rampLip, 0, 1);
      if (roll.height <= support) {
        if (roll.phase === 'drop') {
          roll.lift = 0;
          roll.bounceSpeed = clamp(-roll.vHeight * .10, 0, .041);
        }
        roll.phase = 'tray';
        roll.height = support;
        roll.vHeight = 0;
        roll.side ||= Math.sign(roll.x) || Math.sign(roll.vx) || -1;
      }
    }
    this.returnBounds(lane, ball, roll);
    const lateral = roll.x - previousX;
    const forward = (previousDepth - roll.depth) * (roll.phase === 'drop' ? .45 : 1);
    ball.rotation = (ball.rotation || 0) + lateral / roll.radius;
    advanceBallLook(ball, lateral, forward, roll.radius);
  }

  collideReturningPair(a, b) {
    const first = a.roll, second = b.roll;
    if (first.side && second.side && first.side !== second.side && !first.stacked && !second.stacked) return;
    // The shallow collector has one row in depth. Its physical cross-section
    // is x/height: a descending ball can be supported by the spheres below.
    const channel = first.phase !== 'ramp' && second.phase !== 'ramp'
      && (first.phase === 'tray' || second.phase === 'tray');
    const trayDrop = -this.heightAt(a.lane, trayBackY * a.lane.height, rampLip);
    const elevation = roll => roll.height + trayDrop * clamp(roll.depth / rampLip, 0, 1);
    const dx = second.x - first.x;
    const dz = channel ? 0 : second.depth - first.depth;
    const dh = channel ? elevation(second) - elevation(first) : second.height - first.height;
    const distance = Math.hypot(dx, dz, dh);
    const diameter = first.radius + second.radius;
    if (distance >= diameter - contactGap) return;
    // A floor hit and a ball hit can occur in the same substep. Retain the
    // descending approach so floor clamping cannot turn it into a purely
    // sideways squeeze between already-supported balls.
    let approach = dh;
    if (channel && a.landed !== b.landed && !(a.landed ? second.stacked : first.stacked)) {
      approach = (a.landed ? -1 : 1) * Math.max(Math.abs(dh),
        diameter * .05, (a.landed ? a.downward : b.downward) / 120);
    }
    const normalLength = Math.hypot(dx, dz, approach);
    const nx = normalLength ? dx / normalLength : 1;
    const nz = normalLength ? dz / normalLength : 0;
    const nh = normalLength ? approach / normalLength : 0;
    const movableA = !a.lane.isHeldBall(a.ball), movableB = !b.lane.isHeldBall(b.ball);
    const movers = Number(movableA) + Number(movableB);
    if (!movers) return;
    const overlap = (diameter - distance) / movers;
    const closing = (first.vx - second.vx) * nx + (first.vDepth - second.vDepth) * nz
      + (first.vHeight - second.vHeight) * nh;
    const restitution = Math.abs(nh) > .25 ? (closing < .18 ? 0 : .08) : .18;
    const impulse = Math.max(0, closing) * (1 + restitution) / movers;
    for (const [entry, sign] of [[a, -1], [b, 1]]) {
      if (entry.lane.isHeldBall(entry.ball)) continue;
      const { roll, ball } = entry;
      roll.x += sign * nx * overlap;
      roll.depth += sign * nz * overlap;
      roll.height += sign * nh * overlap;
      roll.vx += sign * nx * impulse;
      roll.vDepth += sign * nz * impulse;
      roll.vHeight += sign * nh * impulse;
      if (channel && roll.phase === 'drop' && sign * nh > .35) {
        roll.phase = 'tray';
        roll.side = Math.sign(roll.x) || -1;
      }
      if (ball.poolState === 'ready') {
        ball.roll = roll;
        if (impulse > .025 || overlap > .003) {
          beginBallLook(ball);
          roll.quiet = 0;
          ball.poolState = 'returning';
        } else if (!roll.stacked) {
          roll.vx = roll.vDepth = roll.vHeight = 0;
        }
      }
    }
  }

  advanceReturns(dt) {
    if (!this.lanes.some(lane => lane.ballPool?.some(ball => ball.roll
      && (ball.poolState === 'returning' || ball.roll.stacked)))) return;
    const entries = this.lanes.flatMap((lane, index) => (lane.ballPool || [])
      .filter(ball => !ball.inTransit && (lane.isReadyBall(ball) || ball.poolState === 'returning'))
      .map(ball => {
        const radius = ball.r / this.width;
        const roll = ball.roll || {
          x:(ball.x + this.origin(lane)) / this.width - .5, depth:0, height:radius, radius,
          vx:0, vDepth:0, vHeight:0, phase:'tray', side:index === 0 ? -1 : 1, quiet:0
        };
        return { lane, ball, roll, wasStacked:roll.stacked,
          previousX:roll.x, previousDepth:roll.depth, previousHeight:roll.height };
      }));
    const [player] = this.lanes;
    const trayDrop = -this.heightAt(player, trayBackY * player.height, rampLip);
    const steps = Math.ceil(dt * 120);
    const step = dt / steps;
    for (let i = 0; i < steps; i++) {
      for (const entry of entries) {
        const { lane, ball, roll } = entry;
        entry.landed = false;
        entry.downward = Math.max(0, -roll.vHeight);
        if (lane.isHeldBall(ball)) continue;
        if (ball.poolState === 'returning' || roll.stacked) {
          const falling = roll.phase === 'drop';
          this.advanceRoll(lane, ball, roll, step, trayDrop);
          entry.landed = falling && roll.phase === 'tray';
        }
      }
      // Resolve the returning balls against one another and the balls already
      // in the tray. A held ball stays anchored unless an airborne hit dislodges it.
      for (let pass = 0; pass < 20; pass++) {
        for (let a = 0; a < entries.length; a++) for (let b = a + 1; b < entries.length; b++) {
          this.collideReturningPair(entries[a], entries[b]);
        }
        for (const { lane, ball, roll } of entries) {
          if (ball.roll && !lane.isHeldBall(ball)) this.returnBounds(lane, ball, roll);
        }
      }
    }
    for (const { lane, ball, roll, wasStacked, previousX, previousDepth, previousHeight } of entries) {
      if (!ball.roll || lane.isHeldBall(ball)) continue;
      const movementSpeed = Math.hypot(roll.x - previousX, roll.depth - previousDepth,
        roll.height - previousHeight) / dt;
      if (ball.poolState === 'ready' && wasStacked && movementSpeed > .045) {
        ball.poolState = 'returning';
        roll.quiet = 0;
        beginBallLook(ball);
      }
      let collector = lane;
      if (ball.poolState === 'returning' && roll.phase === 'tray' && !ball.allowOuterExit) {
        const destination = this.lanes[(roll.side || Math.sign(roll.x)) < 0 ? 0 : 1];
        if (destination.ballPool) collector = destination;
        if (lane !== collector) this.moveSupplyBall(ball, lane, collector);
        const speed = Math.min(movementSpeed, Math.hypot(roll.vx, roll.vDepth, roll.vHeight));
        roll.quiet = roll.depth < .06 && speed < .045 ? roll.quiet + dt : 0;
        if (roll.quiet >= .12) {
          roll.vx = roll.vDepth = roll.vHeight = 0;
          roll.lift = roll.bounceSpeed = 0;
          Object.assign(ball, { poolState:'ready', owner:collector.side, scored:false, life:0 });
        }
      }
      this.projectFlight(collector, ball, roll);
    }
    for (const lane of this.lanes) if (lane.ballPool) lane.selectReadyBall();
  }

  reprojectNonFlyingBalls() {
    for (const lane of this.lanes) for (const ball of lane.ballPool || []) {
      if (ball.roll) this.projectFlight(lane, ball, ball.roll);
      else if (ball.poolState === 'wedged') this.projectWedge(lane, ball, this.hoopGeometry(lane).ring);
    }
  }

  projectWedge(lane, ball, ring) {
    const flight = ball.flight;
    flight.x = ring.x + flight.wedgeOffset;
    flight.depth = flight.wedgeDepth;
    flight.height = ring.height + flight.wedgeLift;
    const settle = flight.wedgeSettle;
    if (settle) {
      const left = (1 - settle.age / wedgeSettleSeconds) ** 2;
      flight.depth += settle.depth * left;
      flight.height += settle.height * left;
    }
    const wobble = flight.wedgeWobble;
    if (wobble?.shift && wobble.age > 0) {
      const progress = wobble.age / wedgeWobbleSeconds;
      // A blocked hit can slide the ball along the seat. Keep its depth at
      // the board and lift it over the rim tube as its centre moves sideways.
      flight.x += wobble.shift * Math.sin(progress * Math.PI) * (1 - progress);
      if (flight.wedgeKind === 'shoulder') {
        const { dx, dd } = nearestRingOffset(flight.x - ring.x, flight.depth - ring.depth, ring);
        const liftSquared = (flight.radius + ring.tube) ** 2 - dx ** 2 - dd ** 2;
        flight.height = ring.height + Math.sqrt(Math.max((flight.radius * .42) ** 2, liftSquared));
      }
    }
    // The collider follows the same tiny support movement as the visible
    // ball. The fixed centre ledge transmits less motion than the ring tube.
    const { dx, dd } = nearestRingOffset(flight.x - ring.x, flight.depth - ring.depth, ring);
    const side = clamp((flight.x - ring.x - dx) / ring.halfWidth, -1, 1);
    const front = clamp((ring.depth + ring.halfDepth - flight.depth + dd) / (2 * ring.halfDepth), 0, 1);
    const onMount = flight.wedgeKind === 'mount';
    const support = this.assembly.supportOffset(side, onMount ? Math.max(.25, front) : front);
    const rimWidth = this.hoopPose(lane).rimW / (this.width * depthScale(flight.depth));
    flight.height -= support * rimWidth * (onMount ? .35 : .75);
    this.projectFlight(lane, ball);
  }

  hoopPose(lane) {
    // Match the approved 4:3 scene. Both inventories use these same world
    // dimensions for drawing, rim contacts, scoring and the automatic solver.
    return {
      x:this.width / 2 - this.origin(lane) + this.offset,
      y:lane.height * .20,
      rimY:lane.height * .34,
      boardRimW:this.width * .126,
      rimW:this.width * .126 * this.rimScale
    };
  }

  canExitRight(ball, depth = ball.flight?.depth ?? 0, y = ball.y, radius = ball.r) {
    const opening = this.rightOpening;
    return ball.owner === 'player' && opening && depth < .32
      && y - radius >= opening.top && y + radius <= opening.bottom;
  }

  advance(dt, active) {
    if (active) this.clock += dt;
    for (const lane of this.lanes) lane.advanceHoop(dt);
    if (active) {
      this.assembly.advanceImpacts(dt);
      const [player] = this.lanes;
      const pose = this.hoopPose(player);
      const { ring } = this.hoopGeometry(player);
      const rimWidth = pose.rimW / (this.width * depthScale(ring.depth));
      const balls = [];
      for (const lane of this.lanes) for (const b of [lane.ball, ...lane.guests]) {
        if (b.poolState === 'wedged') {
          const settle = b.flight.wedgeSettle;
          if (settle && (settle.age += dt) >= wedgeSettleSeconds) delete b.flight.wedgeSettle;
          const wobble = b.flight.wedgeWobble;
          if (wobble) {
            wobble.age = Math.min(wedgeWobbleSeconds, wobble.age + dt);
            const progress = wobble.age / wedgeWobbleSeconds;
            b.rotation = wobble.rotation + wobble.angle * (1 - progress) * Math.sin(progress * Math.PI * 3);
            if (progress === 1) delete b.flight.wedgeWobble;
          }
          this.projectWedge(lane, b, ring);
          const shake = b.flight.wedgeShake;
          if (shake && (shake.age += dt) >= .025) {
            // Release near the first support deflection, then let the normal
            // flight, rim collisions and whole-ball scoring finish the fall.
            const sourceLane = this.lanes.find(source => source.side === shake.sourceSide);
            this.releaseWedgedBall(sourceLane, shake.source, lane, b, shake);
          }
          continue;
        }
        if (!b.scored || !b.flying || !b.flight || b.expired || b.inTransit
            || !this.assembly.net.overlaps(b.flight, ring, rimWidth)) continue;
        const x = (b.x + this.origin(lane) - pose.x) / pose.rimW;
        const y = (b.y - pose.rimY) / pose.rimW;
        balls.push({x, y, r:b.r / pose.rimW});
      }
      this.assembly.advanceNet(dt, balls);
      if (dt > 0) this.advanceReturns(dt);
    }
  }

  #collideFlightWithReady(lane, projectile, trayLane, ready) {
    const flight = projectile.flight;
    const radius = ready.r / this.width;
    const roll = ready.roll || {
      x:(ready.x + this.origin(trayLane)) / this.width - .5,
      depth:0, height:Math.max(radius, this.heightAt(trayLane, ready.y, 0)), radius,
      vx:0, vDepth:0, vHeight:0, phase:'tray', side:trayLane.side === 'player' ? -1 : 1
    };
    const dx = roll.x - flight.x, dz = roll.depth - flight.depth, dh = roll.height - flight.height;
    const distance = Math.hypot(dx, dz, dh);
    const diameter = flight.radius + roll.radius;
    if (distance >= diameter) return;
    const vx = flight.vx - roll.vx, vz = flight.vDepth - roll.vDepth, vh = flight.vHeight - roll.vHeight;
    const normalLength = distance || Math.hypot(vx, vz, vh);
    if (!normalLength) return;
    const nx = (distance ? dx : vx) / normalLength;
    const nz = (distance ? dz : vz) / normalLength;
    const nh = (distance ? dh : vh) / normalLength;
    const closing = vx * nx + vz * nz + vh * nh;
    if (closing <= 0) return;

    if (trayLane.heldBall === ready) trayLane.cancelAim();
    ready.dragged = false;
    const ownershipChanged = Boolean(projectile.owner && ready.owner !== projectile.owner);
    ready.owner = projectile.owner || ready.owner;
    const impulse = closing * .93;
    const separation = (diameter - distance) / 2 + contactGap;
    flight.x -= nx * separation;
    flight.depth -= nz * separation;
    flight.height -= nh * separation;
    flight.vx -= nx * impulse;
    flight.vDepth -= nz * impulse;
    flight.vHeight -= nh * impulse;
    roll.x += nx * separation;
    roll.depth += nz * separation;
    roll.height += nh * separation;
    roll.vx += nx * impulse;
    roll.vDepth += nz * impulse;
    roll.vHeight += nh * impulse;

    // The tray solver now owns this ball, including support and re-collection.
    Object.assign(ready, { roll, poolState:'returning', shotBy:null });
    roll.quiet = 0;
    beginBallLook(ready);
    this.returnBounds(trayLane, ready, roll);
    this.projectFlight(trayLane, ready, roll);
    this.projectFlight(lane, projectile);
    projectile.hitRim = true;
    lane.burst((projectile.x + ready.x + this.origin(trayLane) - this.origin(lane)) / 2,
      (projectile.y + ready.y) / 2, 8);
    if (!projectile.clashed) {
      projectile.clashed = true;
      lane.onBallClash?.({ owner:projectile.owner, ownershipChanged });
    }
  }

  collidePair(lane, a, otherLane, b) {
    // A ball may have entered the return solver earlier in this pair pass.
    if ((!a.flying && !lane.isReadyBall(a)) || (!b.flying && !otherLane.isReadyBall(b))) return;
    if (a.flying && a.flight && otherLane.ballPool && !b.flying) {
      this.#collideFlightWithReady(lane, a, otherLane, b);
      return;
    }
    if (b.flying && b.flight && lane.ballPool && !a.flying) {
      this.#collideFlightWithReady(otherLane, b, lane, a);
      return;
    }
    // The older projected path cannot resolve depth or stacked tray balls.
    if ((!a.flying && a !== lane.ball && !a.dragged)
        || (!b.flying && b !== otherLane.ball && !b.dragged)) return;
    const offset = this.origin(otherLane) - this.origin(lane);
    b.x += offset;
    let collided;
    try {
      collided = lane.collideBalls(a, b);
    } finally {
      b.x -= offset;
    }
    if (!collided) return;
    if (!a.flying) lane.liftPoolBall(a, b);
    if (!b.flying) otherLane.liftPoolBall(b, a);
  }

  // Same-inventory pairs are handled in ShotLane.updateBallPool.
  collideBalls() {
    const [player, neko] = this.lanes;
    const available = lane => (lane.ballPool || [lane.ball, ...lane.guests]).filter(ball =>
      !ball.expired && !ball.inTransit && (!lane.ballPool || ball.flying || lane.isReadyBall(ball)));
    for (const a of available(player)) for (const b of available(neko)) {
      if (a.flying || b.flying) this.collidePair(player, a, neko, b);
    }
    for (const lane of this.lanes) if (lane.ballPool) lane.selectReadyBall();
  }

  draw(lane, aimAllowed = true) {
    const ctx = lane.ctx;
    lane.drawCourt(this.width, this.origin(lane));
    const trayDrop = -this.heightAt(lane, trayBackY * lane.height, rampLip);
    for (const source of this.lanes) for (const ball of source.ballPool || []) {
      const roll = ball.roll;
      const flight = ball.flight || roll;
      if (!flight || ball.inTransit || ball.pageOverlay || flight.depth > 1) continue;
      const x = ball.x + this.origin(source) - this.origin(lane);
      const drop = roll && roll.phase !== 'ramp' ? trayDrop * clamp(roll.depth / rampLip, 0, 1) : 0;
      const clearance = Math.max(0, flight.height - flight.radius + drop + (roll?.lift || 0));
      const y = this.floorY(lane, flight.depth) + drop * this.width * depthScale(flight.depth);
      const opacity = .22 / (1 + clearance * 7);
      if (roll) {
        const shadowRadius = ball.r * (.95 + clearance * 2);
        ctx.save();
        ctx.translate(x, y);
        ctx.scale(1, .34);
        const contact = ctx.createRadialGradient(0, 0, 0, 0, 0, shadowRadius);
        contact.addColorStop(0, `rgba(54,43,40,${opacity * 1.6})`);
        contact.addColorStop(.55, `rgba(54,43,40,${opacity * .8})`);
        contact.addColorStop(1, 'rgba(54,43,40,0)');
        ctx.fillStyle = contact;
        ctx.fillRect(-shadowRadius, -shadowRadius, shadowRadius * 2, shadowRadius * 2);
        ctx.restore();
        continue;
      }
      ctx.fillStyle = `rgba(54,43,40,${opacity})`;
      ctx.beginPath();
      ctx.ellipse(x, y, ball.r * .86, ball.r * .29, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    const drawBalls = (include, byDepth = false) => {
      if (byDepth) {
        const balls = this.lanes.flatMap(source => [source.ball, ...source.guests]
          .filter(include).map(ball => ({ source, ball })));
        balls.sort((a, b) => (b.ball.roll?.depth ?? 0) - (a.ball.roll?.depth ?? 0));
        for (const { source, ball } of balls) {
          ctx.save();
          ctx.translate(this.origin(source) - this.origin(lane), 0);
          source.drawBalls(ctx, candidate => candidate === ball, aimAllowed);
          ctx.restore();
        }
        return;
      }
      for (const source of this.lanes) {
        ctx.save();
        ctx.translate(this.origin(source) - this.origin(lane), 0);
        source.drawBalls(ctx, include, aimAllowed);
        ctx.restore();
      }
    };
    const pose = lane.getHoopPose();
    const { ring, mount } = this.hoopGeometry(lane);
    const rimWidth = pose.rimW / (this.width * depthScale(ring.depth));
    const layers = new Map();
    for (const source of this.lanes) for (const ball of [source.ball, ...source.guests]) {
      if (ball.poolState === 'wedged') {
        layers.set(ball, ball.flight.wedgeKind === 'mount' ? 'inside-hoop' : 'behind-hoop');
        continue;
      }
      if (!ball.flying) continue;
      const flight = ball.flight;
      let layer = 'front';
      if (flight) {
        // A descending ball can meet the front lip visually before its
        // centre reaches the same opening used by scoring.
        const entering = flight.vHeight < 0 && flight.height > ring.height
          && flight.height - flight.radius <= ring.height
          && fitsHoopOpening(flight.x, flight.depth, ring, flight.radius);
        // Keep a ball above the bracket's ledge in front of its back plate,
        // including the first part of its fall after it gets knocked loose.
        const aboveMount = flight.depth >= ring.depth && flight.height >= mount.height
          && Math.abs(flight.x - ring.x) < mount.halfWidth + flight.radius;
        if (flight.depth > boardDepth) layer = 'behind-board';
        else if (aboveMount || entering || ((flight.enteredHoop || ball.scored)
            && this.assembly.net.overlaps(flight, ring, rimWidth))) layer = 'inside-hoop';
        else if (flight.depth >= ring.depth) layer = 'behind-hoop';
      }
      layers.set(ball, layer);
    }
    drawBalls(ball => layers.get(ball) === 'behind-board');
    this.assembly.drawBoard(ctx, pose);
    drawBalls(ball => layers.get(ball) === 'behind-hoop');
    this.assembly.drawBack(ctx, pose);
    drawBalls(ball => layers.get(ball) === 'inside-hoop');
    this.assembly.drawFront(ctx, pose);
    drawBalls(ball => !ball.flying && ball.poolState !== 'wedged', true);
    // The lip is in front of the resting balls, but a released ball can fly
    // above it even when its two-dimensional projection overlaps the rail.
    this.drawLayer(lane, this.frontLayer);
    drawBalls(ball => layers.get(ball) === 'front');
    for (const source of this.lanes) {
      ctx.save();
      ctx.translate(this.origin(source) - this.origin(lane), 0);
      source.drawEffects(ctx);
      ctx.restore();
    }
  }
}
