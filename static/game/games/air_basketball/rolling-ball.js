const textureSize = 192;
const spriteSize = 96;
const lightGridSize = 16;
// Keep the baked-in blue rim light outside the skin sampled for rotation.
const sourceInset = .935;
const materialCache = new Map();
const renderCache = new WeakMap();

function turn(orientation, pitch, spin) {
  const angle = Math.hypot(pitch, spin);
  if (!angle) return orientation;
  const [x, y, z, w] = orientation;
  const scale = Math.sin(angle / 2) / angle;
  const a = pitch * scale, c = spin * scale, d = Math.cos(angle / 2);
  return [d * x + a * w - c * y, d * y + c * x - a * z,
    d * z + c * w + a * y, d * w - a * x - c * z];
}

export function beginBallLook(ball) {
  const spin = ball.rotation || 0;
  if (ball.rollLook) {
    const look = ball.rollLook;
    look.orientation = turn(look.orientation, 0, look.spin - spin);
    look.spin = spin;
  } else {
    ball.rollLook = { orientation:turn([0, 0, 0, 1], 0, -spin), spin };
  }
}

export function advanceBallLook(ball, lateral, forward, radius) {
  const look = ball.rollLook;
  if (!look) return;
  look.orientation = turn(look.orientation, forward / radius, -lateral / radius);
  look.spin = ball.rotation || 0;
}

function makeCanvas(size) {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(size, size);
  const canvas = globalThis.document?.createElement?.('canvas');
  if (canvas) canvas.width = canvas.height = size;
  return canvas;
}

function sampleRGB(pixels, width, stride, x, y, out, offset) {
  const col = Math.floor(x), row = Math.floor(y);
  const dx = x - col, dy = y - row;
  const wx = 1 - dx, wy = 1 - dy;
  const left = (row * width + col) * stride, right = left + stride;
  const bottom = left + width * stride;
  for (let channel = 0; channel < 3; channel++) {
    const upper = pixels[left + channel] * wx + pixels[right + channel] * dx;
    const lower = pixels[bottom + channel] * wx + pixels[bottom + stride + channel] * dx;
    out[offset + channel] = upper * wy + lower * dy;
  }
}

function lightGrid(pixels) {
  // Estimate the broad highlights from the orange leather samples only.
  // This keeps the pebbled surface and blue seams out of the lighting field.
  const grid = new Float32Array(lightGridSize ** 2 * 3);
  for (let row = 0; row < lightGridSize; row++) for (let col = 0; col < lightGridSize; col++) {
    let x = 2 * col / (lightGridSize - 1) - 1;
    let y = 2 * row / (lightGridSize - 1) - 1;
    const inset = Math.max(1, Math.hypot(x, y) / sourceInset);
    x = Math.round((.5 + .5 * x / inset) * (textureSize - 1));
    y = Math.round((.5 + .5 * y / inset) * (textureSize - 1));
    let red = 0, green = 0, blue = 0, count = 0;
    for (let yy = Math.max(0, y - 10); yy <= Math.min(textureSize - 1, y + 10); yy += 2) {
      for (let xx = Math.max(0, x - 10); xx <= Math.min(textureSize - 1, x + 10); xx += 2) {
        const i = (yy * textureSize + xx) * 4;
        const r = pixels[i], g = pixels[i + 1], b = pixels[i + 2];
        if (pixels[i + 3] < 250 || r < 1.16 * g || r < .96 * b) continue;
        red += r;
        green += g;
        blue += b;
        count++;
      }
    }
    const at = (row * lightGridSize + col) * 3;
    grid[at] = count ? red / count : 234;
    grid[at + 1] = count ? green / count : 132;
    grid[at + 2] = count ? blue / count : 112;
  }
  return grid;
}

function readMaterial(image, coverage) {
  const ctx = makeCanvas(textureSize)?.getContext('2d', { willReadFrequently:true });
  if (!ctx) return null;
  const { naturalWidth, naturalHeight } = image;
  ctx.drawImage(image, naturalWidth * (1 - coverage) / 2, naturalHeight * (1 - coverage) / 2,
    naturalWidth * coverage, naturalHeight * coverage, 0, 0, textureSize, textureSize);
  const { data } = ctx.getImageData(0, 0, textureSize, textureSize);
  const lighting = lightGrid(data);
  const leather = new Float32Array(textureSize ** 2 * 3);
  for (let row = 0; row < textureSize; row++) for (let col = 0; col < textureSize; col++) {
    const i = row * textureSize + col;
    const span = (lightGridSize - 1.001) / (textureSize - 1);
    sampleRGB(lighting, lightGridSize, 3, col * span, row * span, leather, i * 3);
    for (let channel = 0; channel < 3; channel++) {
      leather[i * 3 + channel] = data[i * 4 + channel] / leather[i * 3 + channel];
    }
  }
  return { leather, lighting };
}

function prepareSprite(material) {
  const ctx = makeCanvas(spriteSize)?.getContext('2d');
  if (!ctx) return null;
  const pixels = ctx.createImageData(spriteSize, spriteSize);
  const normals = [];
  const lit = new Float32Array(pixels.data.length);
  for (let row = 0; row < spriteSize; row++) for (let col = 0; col < spriteSize; col++) {
    const x = (col + .5) * 2 / spriteSize - 1;
    const y = 1 - (row + .5) * 2 / spriteSize;
    const length = Math.hypot(x, y);
    const alpha = Math.max(0, Math.min(1, (1 - length) * spriteSize / 2 + .5));
    if (!alpha) continue;
    const rim = Math.max(1, length);
    const i = (row * spriteSize + col) * 4;
    normals.push(x / rim, y / rim, Math.sqrt(Math.max(0, 1 - length ** 2)), i);
    const span = (lightGridSize - 1) * sourceInset / 2;
    const centre = (lightGridSize - 1) / 2;
    sampleRGB(material.lighting, lightGridSize, 3, centre + x * span / rim, centre - y * span / rim, lit, i);
    pixels.data[i + 3] = alpha * 255;
  }
  return { ctx, pixels, normals, lit, color:new Float32Array(3) };
}

function paintSprite(sprite, leather, look, rotation) {
  const [x, y, z, w] = turn(look.orientation, 0, look.spin - rotation);
  // Inverse rotation maps the visible hemisphere onto the source ball skin.
  // The source's front projection extends continuously around the far side.
  const xx = 1 - 2 * (y * y + z * z), xy = 2 * (x * y + z * w), xz = 2 * (x * z - y * w);
  const yx = 2 * (x * y - z * w), yy = 1 - 2 * (x * x + z * z), yz = 2 * (y * z + x * w);
  const { normals, lit, pixels, color } = sprite;
  const centre = (textureSize - 1) / 2, span = centre * sourceInset;
  for (let n = 0; n < normals.length; n += 4) {
    const sx = normals[n], sy = normals[n + 1], sz = normals[n + 2], i = normals[n + 3];
    const col = centre + span * (xx * sx + xy * sy + xz * sz);
    const row = centre - span * (yx * sx + yy * sy + yz * sz);
    sampleRGB(leather, textureSize, 3, col, row, color, 0);
    for (let channel = 0; channel < 3; channel++) {
      pixels.data[i + channel] = color[channel] * lit[i + channel];
    }
  }
  sprite.ctx.putImageData(pixels, 0, 0);
  sprite.orientation = look.orientation;
  sprite.rotation = rotation;
}

export function drawBallLook(ctx, x, y, radius, fever, rotation, look, image, coverage) {
  if (!image.complete || !image.naturalWidth) return false;
  let material = materialCache.get(image.src);
  if (material === undefined) {
    material = readMaterial(image, coverage);
    materialCache.set(image.src, material);
  }
  if (!material) return false;
  let sprite = renderCache.get(look);
  if (!sprite) {
    sprite = prepareSprite(material);
    if (!sprite) return false;
    renderCache.set(look, sprite);
  }
  if (sprite.orientation !== look.orientation || sprite.rotation !== rotation) {
    paintSprite(sprite, material.leather, look, rotation);
  }
  ctx.save();
  ctx.shadowBlur = fever ? Math.max(10, radius * .72) : 0;
  if (fever) ctx.shadowColor = 'rgba(248,188,89,.78)';
  ctx.drawImage(sprite.ctx.canvas, x - radius, y - radius, radius * 2, radius * 2);
  ctx.restore();
  return true;
}
