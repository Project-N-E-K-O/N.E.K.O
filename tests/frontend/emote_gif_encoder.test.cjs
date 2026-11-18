const test = require('node:test');
const assert = require('node:assert/strict');

const encoder = require('../../static/js/emote_gif_encoder.js');

function readU16(bytes, offset) {
    return bytes[offset] | (bytes[offset + 1] << 8);
}

function skipSubBlocks(bytes, offset) {
    let cursor = offset;
    while (cursor < bytes.length) {
        const length = bytes[cursor++];
        if (length === 0) return cursor;
        cursor += length;
    }
    throw new Error('unterminated GIF sub-blocks');
}

function inspectGif(bytes) {
    assert.equal(String.fromCharCode(...bytes.slice(0, 6)), 'GIF89a');
    const width = readU16(bytes, 6);
    const height = readU16(bytes, 8);
    const packed = bytes[10];
    let cursor = 13;
    const colorTableEntries = packed & 0x80 ? 1 << ((packed & 0x07) + 1) : 0;
    if (packed & 0x80) cursor += 3 * colorTableEntries;
    let frames = 0;
    const delays = [];
    const minimumCodeSizes = [];
    let hasLoopExtension = false;
    while (cursor < bytes.length) {
        const marker = bytes[cursor++];
        if (marker === 0x3b) break;
        if (marker === 0x21) {
            const label = bytes[cursor++];
            if (label === 0xf9) {
                assert.equal(bytes[cursor++], 4);
                const flags = bytes[cursor++];
                const delay = readU16(bytes, cursor);
                cursor += 2;
                cursor += 1; // transparent colour index
                assert.equal(bytes[cursor++], 0);
                delays.push({ delay, transparent: Boolean(flags & 1), disposal: (flags >> 2) & 0x07 });
            } else if (label === 0xff) {
                const length = bytes[cursor++];
                const application = String.fromCharCode(...bytes.slice(cursor, cursor + length));
                if (application === 'NETSCAPE2.0') hasLoopExtension = true;
                cursor += length;
                cursor = skipSubBlocks(bytes, cursor);
            } else {
                cursor = skipSubBlocks(bytes, cursor);
            }
            continue;
        }
        if (marker !== 0x2c) throw new Error(`unexpected GIF marker ${marker.toString(16)}`);
        cursor += 8; // left, top, width, height
        const imagePacked = bytes[cursor++];
        if (imagePacked & 0x80) cursor += 3 * (1 << ((imagePacked & 0x07) + 1));
        assert.ok(bytes[cursor] >= 2 && bytes[cursor] <= 8);
        minimumCodeSizes.push(bytes[cursor]);
        cursor += 1; // LZW minimum code size
        cursor = skipSubBlocks(bytes, cursor);
        frames += 1;
    }
    return {
        width,
        height,
        frames,
        delays,
        minimumCodeSizes,
        colorTableEntries,
        hasLoopExtension,
        trailer: bytes[bytes.length - 1],
    };
}

function rgbaFrame(width, height, phase = 0) {
    const data = new Uint8ClampedArray(width * height * 4);
    for (let index = 0; index < width * height; index += 1) {
        data[index * 4] = (index + phase * 17) & 0xff;
        data[index * 4 + 1] = (index * 3 + phase * 11) & 0xff;
        data[index * 4 + 2] = 200;
        data[index * 4 + 3] = index % 7 === 0 ? 0 : 255;
    }
    return { width, height, data, delayMs: 80 };
}

test('encodes transparent animated GIF frames with bounded dimensions', () => {
    const bytes = encoder.encode(
        Array.from({ length: 12 }, (_, index) => rgbaFrame(64, 48, index)),
        { maxDimension: 32, fps: 12 },
    );
    const result = inspectGif(bytes);
    assert.deepEqual({ width: result.width, height: result.height }, { width: 32, height: 24 });
    assert.equal(result.colorTableEntries, 128);
    assert.equal(result.frames, 12);
    assert.equal(result.delays.length, 12);
    assert.ok(result.minimumCodeSizes.every((size) => size === 7));
    assert.ok(result.delays.every((item) => item.delay === 8 && item.transparent && item.disposal === 2));
    assert.equal(result.hasLoopExtension, true);
    assert.equal(result.trailer, 0x3b);
});

test('supports a one-shot GIF without the Netscape loop extension', () => {
    const result = inspectGif(encoder.encode([rgbaFrame(2, 2)], { loop: false }));
    assert.equal(result.frames, 1);
    assert.equal(result.hasLoopExtension, false);
});

test('distributes centisecond rounding across the whole loop', () => {
    const frames = Array.from({ length: 30 }, (_, index) => ({
        ...rgbaFrame(2, 2, index), delayMs: 1000 / 30,
    }));
    const result = inspectGif(encoder.encode(frames));
    const delays = result.delays.map(({ delay }) => delay);
    assert.deepEqual(delays.slice(0, 6), [3, 4, 3, 3, 4, 3]);
    assert.equal(delays.reduce((sum, delay) => sum + delay, 0), 100);
});

test('retains an opt-in 256-colour quality mode', () => {
    const result = inspectGif(encoder.encode([rgbaFrame(8, 8)], { colors: 256 }));
    assert.equal(result.colorTableEntries, 256);
    assert.deepEqual(result.minimumCodeSizes, [8]);
});

test('rejects empty input and malformed frames', () => {
    assert.throws(() => encoder.encode([]), { code: 'no_frames' });
    assert.throws(() => encoder.encode([{ width: 2, height: 2, data: new Uint8Array(3) }]), {
        code: 'invalid_frame',
    });
});

// ── adaptive (median-cut) palette ────────────────────────────────────────────
// The fixed 4x4x8 lattice gave red and green four levels against blue's eight,
// which lifted mid-tones by ~40/255 and pulled dark neutrals toward blue. These
// pin the replacement: the palette must follow the artwork's own colours.

const FLAT_PALETTE = [
    [255, 255, 255], [250, 225, 210], [128, 60, 160], [20, 18, 30],
    [240, 200, 120], [220, 40, 60], [40, 90, 220], [128, 128, 128],
];

// Cel animation: overwhelmingly flat colours with a thin antialias tail.
function flatFrame(width, height, phase) {
    const data = new Uint8ClampedArray(width * height * 4);
    for (let index = 0; index < width * height; index += 1) {
        const pick = (index * 7 + phase * 3) % FLAT_PALETTE.length;
        const base = FLAT_PALETTE[pick];
        const blended = index % 40 === 0;
        const other = FLAT_PALETTE[(pick + 1) % FLAT_PALETTE.length];
        const mix = blended ? 0.25 : 0;
        data[index * 4] = base[0] * (1 - mix) + other[0] * mix;
        data[index * 4 + 1] = base[1] * (1 - mix) + other[1] * mix;
        data[index * 4 + 2] = base[2] * (1 - mix) + other[2] * mix;
        data[index * 4 + 3] = index % 13 === 0 ? 0 : 255;
    }
    return { width, height, data, delayMs: 50 };
}

function readGlobalColorTable(bytes) {
    const entries = 1 << ((bytes[10] & 0x07) + 1);
    const table = [];
    for (let index = 0; index < entries; index += 1) {
        table.push([bytes[13 + index * 3], bytes[13 + index * 3 + 1], bytes[13 + index * 3 + 2]]);
    }
    return table;
}

function meanWeightedError(table, frames) {
    let total = 0;
    let weighted = 0;
    const histogram = new Map();
    for (const frame of frames) {
        for (let index = 0; index < frame.width * frame.height; index += 1) {
            if (frame.data[index * 4 + 3] < 128) continue;
            const key = (frame.data[index * 4] << 16)
                | (frame.data[index * 4 + 1] << 8)
                | frame.data[index * 4 + 2];
            histogram.set(key, (histogram.get(key) || 0) + 1);
        }
    }
    for (const [key, count] of histogram) {
        const red = (key >> 16) & 0xff;
        const green = (key >> 8) & 0xff;
        const blue = key & 0xff;
        let best = Infinity;
        // Entry 0 is the transparent slot, so it never competes for opaque pixels.
        for (let index = 1; index < table.length; index += 1) {
            const dr = red - table[index][0];
            const dg = green - table[index][1];
            const db = blue - table[index][2];
            const distance = dr * dr * 0.299 + dg * dg * 0.587 + db * db * 0.114;
            if (distance < best) best = distance;
        }
        weighted += Math.sqrt(best) * count;
        total += count;
    }
    return weighted / total;
}

test('adaptive palette keeps the artwork\'s own flat colours', () => {
    const frames = [flatFrame(64, 64, 0), flatFrame(64, 64, 1)];
    const table = readGlobalColorTable(encoder.encode(frames, { width: 64, height: 64 }));
    // Pure white used to be unreachable: 4x4x8 needs 128 slots for a 127-slot
    // table, so the brightest cell was clamped away and white collapsed to a
    // yellowish entry shared with skin tones.
    assert.ok(
        table.some(([r, g, b]) => r === 255 && g === 255 && b === 255),
        'palette must contain the source pure white',
    );
    assert.ok(
        table.some(([r, g, b]) => r === 250 && g === 225 && b === 210),
        'palette must contain the source skin tone',
    );
});

test('adaptive palette beats the lattice on cel-shaded frames', () => {
    const frames = [
        flatFrame(64, 64, 0), flatFrame(64, 64, 1), flatFrame(64, 64, 2), flatFrame(64, 64, 3),
    ];
    const lattice = meanWeightedError(
        readGlobalColorTable(encoder.encode(frames, { width: 64, height: 64, adaptive: false })),
        frames,
    );
    const adaptive = meanWeightedError(
        readGlobalColorTable(encoder.encode(frames, { width: 64, height: 64 })),
        frames,
    );
    assert.ok(adaptive < lattice / 1.5, `adaptive ${adaptive.toFixed(2)} should beat lattice ${lattice.toFixed(2)}`);
});

test('adaptive palette is padded to the declared table without changing the code width', () => {
    const bytes = encoder.encode([flatFrame(32, 32, 0)], { width: 32, height: 32 });
    const result = inspectGif(bytes);
    // The packed colour-table field can only state a power of two, so a
    // content-derived palette still has to fill the full 128 entries.
    assert.equal(result.colorTableEntries, 128);
    assert.deepEqual(result.minimumCodeSizes, [7]);
});

test('a prebuilt palette is reused verbatim so budget retries cannot shift colour', () => {
    const frames = [flatFrame(48, 48, 0), flatFrame(48, 48, 5)];
    const sampled = encoder.buildPaletteFromFrames(frames, { colors: 128 });
    assert.ok(sampled && sampled.palette, 'buildPaletteFromFrames must return a palette');
    const withPalette = readGlobalColorTable(
        encoder.encode(frames, { width: 48, height: 48, palette: sampled }),
    );
    const auto = readGlobalColorTable(encoder.encode(frames, { width: 48, height: 48 }));
    assert.deepEqual(withPalette, auto);
});

test('an over-long supplied table is trimmed instead of desyncing the stream', () => {
    const frames = [flatFrame(16, 16, 0)];
    const sampled = encoder.buildPaletteFromFrames(frames, { colors: 128 });
    const oversized = new Uint8Array(sampled.palette.length + 900);
    oversized.set(sampled.palette);
    const result = inspectGif(encoder.encode(frames, {
        width: 16, height: 16, palette: { palette: oversized, colors: sampled.colors },
    }));
    assert.equal(result.frames, 1);
    assert.equal(result.trailer, 0x3b);
});

test('single-colour frames decline an adaptive palette and stay encodable', () => {
    const data = new Uint8ClampedArray(8 * 8 * 4);
    for (let index = 0; index < 64; index += 1) {
        data[index * 4] = 10;
        data[index * 4 + 1] = 200;
        data[index * 4 + 2] = 90;
        data[index * 4 + 3] = 255;
    }
    const frames = [{ width: 8, height: 8, data }];
    // One distinct colour carries no information to partition; median-cut would
    // emit a degenerate table, so sampling reports "not usable" instead.
    assert.equal(encoder.buildPaletteFromFrames(frames, { colors: 128 }), null);
    // encode must still fall back to the lattice and produce a well-formed file
    // whose chosen entry sits near the source colour.
    const result = inspectGif(encoder.encode(frames, { width: 8, height: 8 }));
    assert.equal(result.frames, 1);
    assert.equal(result.colorTableEntries, 128);
    const nearest = readGlobalColorTable(encoder.encode(frames, { width: 8, height: 8 }))
        .slice(1)
        .reduce((best, [r, g, b]) => {
            const distance = (r - 10) ** 2 + (g - 200) ** 2 + (b - 90) ** 2;
            return distance < best.distance ? { distance, color: [r, g, b] } : best;
        }, { distance: Infinity, color: null });
    assert.ok(nearest.color, 'the lattice must offer a colour');
    assert.ok(
        Math.abs(nearest.color[1] - 200) <= 40 && Math.abs(nearest.color[2] - 90) <= 40,
        `nearest lattice colour ${JSON.stringify(nearest.color)} is too far from rgb(10,200,90)`,
    );
});
