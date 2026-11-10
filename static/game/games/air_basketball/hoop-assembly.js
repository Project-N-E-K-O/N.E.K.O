const assetVersion = new URL(import.meta.url).search;
const boardPulseSeconds = .24;
const rimShakeSeconds = .16;
const rimRear = -.09;
const rimDepth = .18;
const netLength = .64;
const netTopRadius = .43;
const netBottomRadius = .17;
const netOffset = .025;
const netEllipse = .075;

function rimOffset(x, y, motion) {
  return motion ? motion.pitch * (y - rimRear) / rimDepth + motion.roll * x : 0;
}

// A separate rope lattice, rather than a textured net moving as one rigid image.
export class RopeNet {
  constructor() {
    this.columns = 12;
    this.rows = 6;
    this.nodes = [];
    this.links = [];
    for (let row = 0; row <= this.rows; row++) {
      const t = row / this.rows;
      for (let column = 0; column < this.columns; column++) {
        const angle = (column + (row % 2) * .5) / this.columns * Math.PI * 2;
        const x = Math.cos(angle) * (netTopRadius + (netBottomRadius - netTopRadius) * t);
        const y = netOffset + t * netLength + Math.sin(angle) * netEllipse * (1 - .4 * t);
        const node = {x, y, px:x, py:y, restX:x, restY:y, pinned:row === 0, front:Math.sin(angle) >= 0};
        this.nodes.push(node);
        if (row) {
          for (const previous of [column, (column + (row % 2 ? 1 : -1) + this.columns) % this.columns]) {
            const a = this.nodes[(row - 1) * this.columns + previous];
            this.links.push({a, b:node, length:Math.hypot(a.x - x, a.y - y), front:node.front});
          }
        }
      }
    }
  }

  reset() {
    for (const n of this.nodes) Object.assign(n, {x:n.restX, y:n.restY, px:n.restX, py:n.restY});
  }

  overlaps({ x, depth, height, radius }, ring, rimWidth) {
    // Test the highest part of the ball against the narrowing net. A ball
    // knocked out to either side stops pushing the ropes as it moves away.
    const drop = (ring.height - height - radius) / rimWidth;
    if (drop > netOffset + netLength + netEllipse * .6 || height - radius > ring.height) return false;
    const t = Math.max(0, Math.min(1, (drop - netOffset) / netLength));
    const taper = 1 + (netBottomRadius / netTopRadius - 1) * t;
    return Math.hypot(
      (x - ring.x) / (ring.halfWidth * taper + radius),
      (depth - ring.depth) / (ring.halfDepth * (1 - .4 * t) + radius)
    ) < 1;
  }

  advance(dt, balls, rimMotion) {
    if (!(dt > 0)) return;
    const steps = Math.max(1, Math.ceil(dt / (1 / 120)));
    const step = dt / steps;
    let pitch = 0, roll = 0;
    for (let i = 0; i < steps; i++) {
      const motion = rimMotion?.((i + 1) * step - dt);
      for (const n of this.nodes) {
        if (n.pinned) {
          n.x = n.restX;
          n.y = n.restY + rimOffset(n.restX, n.restY, motion);
          n.px = n.x; n.py = n.y;
          continue;
        }
        const vx = (n.x - n.px) * Math.exp(-8 * step);
        const vy = (n.y - n.py) * Math.exp(-8 * step);
        n.px = n.x; n.py = n.y;
        n.x += vx + (n.restX - n.x) * 45 * step * step;
        n.y += vy + (n.restY - n.y) * 45 * step * step;
      }
      for (let iteration = 0; iteration < 4; iteration++) {
        for (const {a, b, length} of this.links) {
          const dx = b.x - a.x, dy = b.y - a.y;
          const distance = Math.hypot(dx, dy) || .0001;
          const error = (distance - length) / distance * .75;
          const aWeight = a.pinned ? 0 : b.pinned ? 1 : .5;
          const bWeight = b.pinned ? 0 : a.pinned ? 1 : .5;
          a.x += dx * error * aWeight; a.y += dy * error * aWeight;
          b.x -= dx * error * bWeight; b.y -= dy * error * bWeight;
        }
        for (const ball of balls) for (const n of this.nodes) {
          if (n.pinned) continue;
          const dx = n.x - ball.x, dy = n.y - ball.y;
          const distance = Math.hypot(dx, dy);
          // The front ropes wrap across the visible face of the ball.
          const contactRadius = ball.r * (n.front ? .78 : 1);
          if (distance >= contactRadius || distance < .0001) continue;
          const push = (contactRadius - distance) * .55 / distance;
          n.x += dx * push; n.y += dy * push;
        }
      }
      // A projected rope can fold into an alternate distance-constraint
      // equilibrium. Shape recovery untangles it after the ball has left.
      if (!balls.length) {
        const recover = 1 - Math.exp(-2 * step);
        for (const n of this.nodes) if (!n.pinned) {
          n.x += (n.restX - n.x) * recover;
          n.y += (n.restY - n.y) * recover;
        }
      }
      // A taut hanger pulls back on its fixed attachment. Slack rope carries
      // no load, and the rear hangers have less leverage on the bracket.
      for (const { a, b, length } of this.links) {
        if (!a.pinned) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const distance = Math.hypot(dx, dy);
        if (dy <= 0 || distance <= length) continue;
        const pull = (distance - length) * dy / distance;
        pitch += pull * Math.max(0, Math.min(1, (a.restY - rimRear) / rimDepth));
        roll += pull * a.restX;
      }
    }
    return { pitch:pitch / steps, roll:roll / steps };
  }

  draw(ctx, pose, front, motion) {
    const ropePoint = node => {
      const y = node.pinned ? node.restY + rimOffset(node.restX, node.restY, motion) : node.y;
      return [pose.x + node.x * pose.rimW, pose.rimY + y * pose.rimW];
    };
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    for (const link of this.links) {
      if (link.front !== front) continue;
      ctx.moveTo(...ropePoint(link.a));
      ctx.lineTo(...ropePoint(link.b));
    }
    ctx.strokeStyle = front ? 'rgba(81,119,140,.75)' : 'rgba(91,130,151,.48)';
    ctx.lineWidth = Math.max(2, pose.rimW * .024);
    ctx.stroke();
    ctx.strokeStyle = front ? '#f6fbff' : 'rgba(227,240,249,.9)';
    ctx.lineWidth = Math.max(1, pose.rimW * .013);
    ctx.stroke();
    ctx.restore();
  }
}

export class HoopAssembly {
  constructor() {
    this.net = new RopeNet();
    this.boardImpacts = [];
    this.rimImpacts = [];
    this.netPull = { pitch:0, roll:0 };
    this.boardImage = new Image();
    this.rimImage = new Image();
    for (const [image, file] of [[this.boardImage, 'neko-backboard.png'], [this.rimImage, 'neko-rim.png']]) {
      image.decoding = 'async';
      image.src = new URL(`./assets/${file}${assetVersion}`, import.meta.url).href;
    }
  }

  reset() {
    this.net.reset();
    this.boardImpacts.length = 0;
    this.rimImpacts.length = 0;
    this.netPull.pitch = this.netPull.roll = 0;
  }

  markBoardImpact(x, y) {
    this.boardImpacts.push({ x, y, age:0 });
  }

  markRimImpact(side, closing, vertical, age, front = .5) {
    const strength = Math.min(1, (closing - .055) / .70);
    if (strength > 0) this.rimImpacts.push({ side, front, strength:vertical < -.2 ? -strength : strength, age });
  }

  advanceImpacts(dt) {
    if (this.boardImpacts.length) {
      for (const impact of this.boardImpacts) impact.age += dt;
      this.boardImpacts = this.boardImpacts.filter(impact => impact.age < boardPulseSeconds);
    }
    if (this.rimImpacts.length) {
      for (const impact of this.rimImpacts) impact.age += dt;
      this.rimImpacts = this.rimImpacts.filter(impact => impact.age < rimShakeSeconds);
    }
  }

  advanceNet(dt, balls) {
    if (!(dt > 0)) return;
    const load = this.net.advance(dt, balls, offset => this.rimMotion(offset));
    const pitch = Math.min(.004, Math.max(0, load.pitch - .005) * .035);
    const roll = Math.sign(load.roll) * Math.min(.0015, Math.max(0, Math.abs(load.roll) - .004) * .05);
    const follow = 1 - Math.exp(-60 * dt);
    this.netPull.pitch += (pitch - this.netPull.pitch) * follow;
    this.netPull.roll += (roll - this.netPull.roll) * follow;
    if (this.netPull.pitch < .00015 && Math.abs(this.netPull.roll) < .00015) {
      this.netPull.pitch = this.netPull.roll = 0;
    }
  }

  rimMotion(offset = 0) {
    if (!this.rimImpacts.length && !this.netPull.pitch && !this.netPull.roll) return null;
    let { pitch, roll } = this.netPull;
    for (const { side, front, strength, age } of this.rimImpacts) {
      const time = age + offset;
      if (time < 0) continue;
      // Spread the impulse over the brief ball contact so its first movement
      // remains visible when the display misses the exact impact frame.
      const tailTime = Math.max(0, time - .015);
      const wave = strength * .5 * (Math.exp(-38 * time) * Math.sin(30 * time)
        + Math.exp(-38 * tailTime) * Math.sin(30 * tailTime));
      pitch += wave * .075 * front;
      roll += wave * .035 * side * (.3 + .7 * front);
    }
    return { pitch:Math.max(-.020, Math.min(.020, pitch)), roll:Math.max(-.014, Math.min(.014, roll)) };
  }

  supportOffset(side, front) {
    return rimOffset(side / 2, rimRear + rimDepth * front, this.rimMotion());
  }

  boardRect(pose) {
    // Original 512px sprite: board bounds x15..497, y13..321. Its size and
    // placement stay fixed while the new rim/net use their independent scale.
    const assembly = pose.boardRimW * 1.84;
    return {x:pose.x - assembly * 241 / 512, y:pose.rimY - assembly * (.595 - 13 / 512),
      width:assembly * 482 / 512, height:assembly * 308 / 512};
  }

  drawBoard(ctx, pose) {
    const r = this.boardRect(pose);
    if (this.boardImage.complete && this.boardImage.naturalWidth) {
      ctx.drawImage(this.boardImage, 83, 39, 1411, 902, r.x, r.y, r.width, r.height);
    } else {
      ctx.save(); ctx.fillStyle = '#e8f0f8'; ctx.strokeStyle = '#82aaca'; ctx.lineWidth = 3;
      ctx.fillRect(r.x, r.y, r.width, r.height); ctx.strokeRect(r.x, r.y, r.width, r.height); ctx.restore();
    }
    if (this.boardImpacts.length) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(r.x, r.y, r.width, r.height);
      ctx.clip();
      for (const impact of this.boardImpacts) {
        const progress = impact.age / boardPulseSeconds;
        const opacity = 1 - progress;
        ctx.beginPath();
        ctx.arc(r.x + impact.x * r.width, r.y + impact.y * r.height,
          r.width * (.11 + .09 * progress), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(107,174,220,${.23 * opacity})`;
        ctx.strokeStyle = `rgba(50,114,169,${.84 * opacity})`;
        ctx.lineWidth = Math.max(1.5, r.width * .013);
        ctx.fill();
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  drawRim(ctx, pose, front, motion) {
    const width = pose.rimW;
    const height = width * 410 / 1212;
    const top = pose.rimY - height * .665;
    const mountingPath = () => {
      // The pins, red back plate and centre bracket stay bolted to the board.
      ctx.moveTo(pose.x - width * .216, top - 2);
      ctx.lineTo(pose.x + width * .216, top - 2);
      ctx.lineTo(pose.x + width * .216, pose.rimY - width * .112);
      ctx.lineTo(pose.x + width * .139, pose.rimY - width * .112);
      ctx.lineTo(pose.x + width * .105, pose.rimY + width * .031);
      ctx.lineTo(pose.x - width * .105, pose.rimY + width * .031);
      ctx.lineTo(pose.x - width * .139, pose.rimY - width * .112);
      ctx.lineTo(pose.x - width * .216, pose.rimY - width * .112);
      ctx.closePath();
    };
    const drawImage = () => ctx.drawImage(this.rimImage, 281, 236, 1212, 410,
      pose.x - width / 2, top, width, height);
    ctx.save();
    if (motion && this.rimImage.complete && this.rimImage.naturalWidth) {
      ctx.beginPath();
      ctx.rect(pose.x - width / 2 - 3, top - 3, width + 6, height + 6);
      mountingPath();
      ctx.clip('evenodd');
      // Project a small tilt about the rear attachment. Each narrow section
      // keeps the artwork's tube thickness while its centreline changes depth.
      const slices = 32;
      const sourceTop = front ? 410 * .665 : 0;
      const sourceHeight = front ? 410 * (1 - .665) : 410 * .665;
      for (let index = 0; index < slices; index++) {
        const x = (index + .5) / slices - .5;
        const y = (front ? 1 : -1) * .09 * Math.sqrt(Math.max(0, 1 - (x * 2) ** 2));
        const shift = rimOffset(x, y, motion) * width;
        const shiftedTop = top + sourceTop * width / 1212 + shift;
        ctx.save();
        ctx.beginPath();
        ctx.rect(pose.x - width / 2 + index * width / slices, shiftedTop,
          width / slices, sourceHeight * width / 1212);
        ctx.clip();
        ctx.drawImage(this.rimImage, 281, 236 + sourceTop, 1212, sourceHeight,
          pose.x - width / 2, shiftedTop, width, sourceHeight * width / 1212);
        ctx.restore();
      }
    } else {
      ctx.beginPath();
      ctx.rect(pose.x - width / 2 - 2, front ? pose.rimY : top - 2, width + 4,
        front ? height + 4 : pose.rimY - top + 2);
      ctx.clip();
      if (this.rimImage.complete && this.rimImage.naturalWidth) drawImage();
      else {
        ctx.strokeStyle = '#e98582'; ctx.lineWidth = Math.max(3, width * .045);
        ctx.beginPath();
        ctx.ellipse(pose.x, pose.rimY + rimOffset(0, 0, motion) * width,
          width / 2, width * .09 + (motion?.pitch ?? 0) * width / 2, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
    ctx.restore();
    if (motion && this.rimImage.complete && this.rimImage.naturalWidth) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(pose.x - width / 2 - 2, front ? pose.rimY : top - 2, width + 4,
        front ? height + 4 : pose.rimY - top + 2);
      ctx.clip();
      ctx.beginPath();
      mountingPath();
      ctx.clip();
      drawImage();
      ctx.restore();
    }
  }

  drawBack(ctx, pose) {
    const motion = this.rimMotion();
    this.net.draw(ctx, pose, false, motion);
    this.drawRim(ctx, pose, false, motion);
  }

  drawFront(ctx, pose) {
    const motion = this.rimMotion();
    this.net.draw(ctx, pose, true, motion);
    this.drawRim(ctx, pose, true, motion);
  }
}
