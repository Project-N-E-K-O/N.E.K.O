/*
 * Small dependency-free GIF89a encoder used by the Emote maker.
 *
 * The editor already has a WebGL renderer, but browsers do not expose a
 * portable "record canvas as GIF" API.  Keeping the encoder here avoids
 * adding a runtime dependency to the desktop bundle.  Frames are accepted as
 * ImageData-like objects ({ width, height, data }) or canvases.
 *
 * Colour selection is adaptive by default: a median-cut palette is built from
 * the colours that actually appear in the animation.  A fixed 4x4x8 lattice is
 * retained as an opt-out fallback, but it is a poor fit for hand-drawn art --
 * four levels for red and green against eight for blue systematically lifts
 * mid-tones by ~40/255 and pulls dark neutral colours toward blue, which reads
 * as a purple cast in exported emotes.
 *
 * The declared colour table must stay a power of two (the GIF packed field only
 * encodes 2^(n+1) entries), so an adaptive palette is always padded out to the
 * 128- or 256-entry table and pixel indices stay below the LZW clear code.
 * A 256-colour mode remains available for callers that prefer smoother
 * gradients.  Both modes keep output bounded and make transparent emotes
 * usable in chat clients that only understand GIF89a.
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }

    root.NekoEmoteGifEncoder = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const MAX_GIF_DIMENSION = 0xffff;
    const DEFAULT_COLOR_COUNT = 128;
    const TRANSPARENT_INDEX = 0;

    class GifEncoderError extends Error {
        constructor(message, code = 'gif_encoder_error') {
            super(message);
            this.name = 'GifEncoderError';
            this.code = code;
        }
    }

    function finitePositive(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) && number > 0 ? number : fallback;
    }

    function clampByte(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return 0;
        return Math.max(0, Math.min(255, Math.round(number)));
    }

    function writeAscii(target, value) {
        for (let index = 0; index < value.length; index += 1) {
            target.push(value.charCodeAt(index) & 0xff);
        }
    }

    function writeU16(target, value) {
        const number = Math.max(0, Math.min(MAX_GIF_DIMENSION, Math.round(Number(value) || 0)));
        target.push(number & 0xff, (number >>> 8) & 0xff);
    }

    function normalizeColorCount(value) {
        const requested = Math.round(Number(value));
        if (requested === 256) return 256;
        // The default palette has 128 colours. Keep unsupported values on the
        // nearest safe default instead of emitting a malformed table size.
        return DEFAULT_COLOR_COUNT;
    }

    function buildPalette(colorCount = DEFAULT_COLOR_COUNT) {
        const count = normalizeColorCount(colorCount);
        const palette = new Uint8Array(count * 3);
        // Entry zero is reserved for GIF transparency.  In 128-colour mode
        // the remaining entries are a 4x4x8 RGB lattice.  In 256-colour mode
        // they form the higher fidelity 6x6x7 lattice used by the fallback
        // quality mode.
        if (count === 128) {
            // Keep an opaque black entry beside the transparent entry. This
            // avoids turning black outlines into a dark-blue substitute.
            palette.set([0, 0, 0], 1 * 3);
            for (let index = 2; index < count; index += 1) {
                const raw = index - 1;
                const red = Math.floor(raw / 32);
                const green = Math.floor((raw % 32) / 8);
                const blue = raw % 8;
                palette[index * 3] = Math.round(red * 255 / 3);
                palette[index * 3 + 1] = Math.round(green * 255 / 3);
                palette[index * 3 + 2] = Math.round(blue * 255 / 7);
            }
            return palette;
        }
        let index = 1;
        for (let red = 0; red < 6; red += 1) {
            for (let green = 0; green < 6; green += 1) {
                for (let blue = 0; blue < 7; blue += 1) {
                    palette[index * 3] = Math.round(red * 255 / 5);
                    palette[index * 3 + 1] = Math.round(green * 255 / 5);
                    palette[index * 3 + 2] = Math.round(blue * 255 / 6);
                    index += 1;
                }
            }
        }
        palette.set([0, 0, 0], 253 * 3);
        palette.set([128, 128, 128], 254 * 3);
        palette.set([255, 255, 255], 255 * 3);
        return palette;
    }

    const PALETTE = buildPalette(DEFAULT_COLOR_COUNT);

    function colorIndex(red, green, blue, colorCount = DEFAULT_COLOR_COUNT) {
        if (normalizeColorCount(colorCount) === 128) {
            const redLevel = Math.max(0, Math.min(3, Math.round(clampByte(red) * 3 / 255)));
            const greenLevel = Math.max(0, Math.min(3, Math.round(clampByte(green) * 3 / 255)));
            const blueLevel = Math.max(0, Math.min(7, Math.round(clampByte(blue) * 7 / 255)));
            const index = redLevel * 32 + greenLevel * 8 + blueLevel;
            // Index zero is reserved for transparency; index one is the
            // nearest opaque colour for RGB(0,0,0).
            return index === TRANSPARENT_INDEX ? 1 : Math.min(127, index + 1);
        }
        const redLevel = Math.max(0, Math.min(5, Math.round(clampByte(red) * 5 / 255)));
        const greenLevel = Math.max(0, Math.min(5, Math.round(clampByte(green) * 5 / 255)));
        const blueLevel = Math.max(0, Math.min(6, Math.round(clampByte(blue) * 6 / 255)));
        return 1 + redLevel * 42 + greenLevel * 7 + blueLevel;
    }

    const MAX_INDEX_CACHE = 1 << 16;
    const DEFAULT_SAMPLE_LIMIT = 120_000;

    function channelValue(entry, channel) {
        return channel === 0 ? entry.red : (channel === 1 ? entry.green : entry.blue);
    }

    function makeBox(entries) {
        let minRed = 255;
        let maxRed = 0;
        let minGreen = 255;
        let maxGreen = 0;
        let minBlue = 255;
        let maxBlue = 0;
        let count = 0;
        for (const entry of entries) {
            count += entry.count;
            if (entry.red < minRed) minRed = entry.red;
            if (entry.red > maxRed) maxRed = entry.red;
            if (entry.green < minGreen) minGreen = entry.green;
            if (entry.green > maxGreen) maxGreen = entry.green;
            if (entry.blue < minBlue) minBlue = entry.blue;
            if (entry.blue > maxBlue) maxBlue = entry.blue;
        }
        const redSpan = maxRed - minRed;
        const greenSpan = maxGreen - minGreen;
        const blueSpan = maxBlue - minBlue;
        const channel = (redSpan >= greenSpan && redSpan >= blueSpan) ? 0
            : ((greenSpan >= blueSpan) ? 1 : 2);
        const span = channel === 0 ? redSpan : (channel === 1 ? greenSpan : blueSpan);
        return { entries, channel, span, count };
    }

    // Split on the weighted median of the widest channel, so a handful of
    // high-frequency colours (skin, hair, a flat background) are never crowded
    // out by long tails of anti-aliased pixels.
    function splitBox(box) {
        const sorted = box.entries.slice()
            .sort((a, b) => (channelValue(a, box.channel) - channelValue(b, box.channel)) || (a.count - b.count));
        const half = box.count / 2;
        let running = 0;
        let cut = sorted.length;
        for (let index = 0; index < sorted.length; index += 1) {
            running += sorted[index].count;
            if (running >= half) {
                cut = index + 1;
                break;
            }
        }
        if (cut <= 0) cut = 1;
        if (cut >= sorted.length) cut = sorted.length - 1;
        if (cut < 1) return null;
        return [makeBox(sorted.slice(0, cut)), makeBox(sorted.slice(cut))];
    }

    function boxAverage(box) {
        let red = 0;
        let green = 0;
        let blue = 0;
        let weight = 0;
        for (const entry of box.entries) {
            weight += entry.count;
            red += entry.red * entry.count;
            green += entry.green * entry.count;
            blue += entry.blue * entry.count;
        }
        if (!weight) return [0, 0, 0];
        return [clampByte(red / weight), clampByte(green / weight), clampByte(blue / weight)];
    }

    function medianCut(entries, maxColors) {
        const limit = Math.max(1, Math.min(maxColors, entries.length));
        const boxes = [makeBox(entries)];
        while (boxes.length < limit) {
            let target = -1;
            let bestScore = 0;
            for (let index = 0; index < boxes.length; index += 1) {
                const box = boxes[index];
                if (box.entries.length < 2 || !box.span) continue;
                // Split the box that is both wide in colour space and heavy in pixels.
                const score = box.span * box.count;
                if (score > bestScore) {
                    bestScore = score;
                    target = index;
                }
            }
            if (target < 0) break;
            const parts = splitBox(boxes[target]);
            if (!parts) break;
            boxes.splice(target, 1, parts[0], parts[1]);
        }
        return boxes.map(boxAverage);
    }

    function sampleHistogram(frames, alphaThreshold, sampleLimit) {
        const histogram = new Map();
        let pixelTotal = 0;
        const sources = [];
        for (const frame of frames) {
            const source = framePixels(frame);
            sources.push(source);
            pixelTotal += source.width * source.height;
        }
        const limit = Math.max(1024, Number(sampleLimit) || DEFAULT_SAMPLE_LIMIT);
        const stride = Math.max(1, Math.ceil(pixelTotal / limit));
        let carry = 0;
        for (const source of sources) {
            const data = source.data;
            const pixelCount = source.width * source.height;
            for (let offset = carry; offset < pixelCount; offset += stride) {
                const base = offset * 4;
                const alpha = Number(data[base + 3]);
                if (!Number.isFinite(alpha) || alpha < alphaThreshold) continue;
                const red = clampByte(data[base]);
                const green = clampByte(data[base + 1]);
                const blue = clampByte(data[base + 2]);
                const key = (red << 16) | (green << 8) | blue;
                histogram.set(key, (histogram.get(key) || 0) + 1);
            }
            carry = (carry - pixelCount) % stride;
            if (carry < 0) carry += stride;
        }
        return histogram;
    }

    function entriesFromHistogram(histogram) {
        const entries = [];
        for (const [key, count] of histogram) {
            entries.push({
                red: (key >> 16) & 0xff,
                green: (key >> 8) & 0xff,
                blue: key & 0xff,
                count,
            });
        }
        return entries;
    }

    // Entry 0 stays reserved for GIF transparency, so an N-entry table carries
    // at most N-1 real colours. Remaining slots are padded with the overall mean
    // rather than left black: a decoder that walks the whole table (or a frame
    // that references a padded index after a bug) should not flash black.
    function buildAdaptivePalette(frames, colorCount, options = {}) {
        const count = normalizeColorCount(colorCount);
        const alphaThreshold = Math.max(0, Math.min(255, Number.isFinite(Number(options.alphaThreshold))
            ? Number(options.alphaThreshold)
            : 128));
        const histogram = sampleHistogram(frames, alphaThreshold, options.sampleLimit);
        if (histogram.size < 2) return null;
        const entries = entriesFromHistogram(histogram);
        const colors = medianCut(entries, count - 1);
        if (!colors.length) return null;
        let meanRed = 0;
        let meanGreen = 0;
        let meanBlue = 0;
        let weight = 0;
        for (const entry of entries) {
            weight += entry.count;
            meanRed += entry.red * entry.count;
            meanGreen += entry.green * entry.count;
            meanBlue += entry.blue * entry.count;
        }
        const padding = weight
            ? [clampByte(meanRed / weight), clampByte(meanGreen / weight), clampByte(meanBlue / weight)]
            : [0, 0, 0];
        const palette = new Uint8Array(count * 3);
        colors.forEach((color, index) => palette.set(color, (index + 1) * 3));
        for (let index = colors.length + 1; index < count; index += 1) {
            palette.set(padding, index * 3);
        }
        return { palette, colors: colors.length, colorCount: count };
    }

    function buildNearestIndexer(adaptive) {
        const palette = adaptive.palette;
        const colors = adaptive.colors;
        const cache = new Map();
        const lookup = (red, green, blue) => {
            const key = (red << 16) | (green << 8) | blue;
            const cached = cache.get(key);
            if (cached !== undefined) return cached;
            let best = 1;
            let bestDistance = Number.POSITIVE_INFINITY;
            for (let index = 0; index < colors; index += 1) {
                const base = (index + 1) * 3;
                const deltaRed = red - palette[base];
                const deltaGreen = green - palette[base + 1];
                const deltaBlue = blue - palette[base + 2];
                // Luma-weighted squared distance: cheaper than a perceptual metric
                // and, unlike plain Euclidean RGB, it stops trading a green error
                // for an equal-size blue error.
                const distance = deltaRed * deltaRed * 0.299
                    + deltaGreen * deltaGreen * 0.587
                    + deltaBlue * deltaBlue * 0.114;
                if (distance < bestDistance) {
                    bestDistance = distance;
                    best = index + 1;
                }
            }
            // Hand-drawn frames repeat colours heavily, so the memo is where the
            // speed comes from; the cap keeps a pathological frame from growing it
            // without bound.
            if (cache.size < MAX_INDEX_CACHE) cache.set(key, best);
            return best;
        };
        return lookup;
    }

    function latticeLookup(colorCount) {
        return (red, green, blue) => colorIndex(red, green, blue, colorCount);
    }

    function framePixels(frame) {
        if (!frame) throw new GifEncoderError('GIF 帧不能为空', 'invalid_frame');
        if (frame.data && frame.width && frame.height) {
            const width = Math.round(Number(frame.width));
            const height = Math.round(Number(frame.height));
            const expected = width * height * 4;
            if (!(width > 0 && height > 0) || frame.data.length < expected) {
                throw new GifEncoderError('GIF 帧像素尺寸无效', 'invalid_frame');
            }
            return { width, height, data: frame.data };
        }
        if (typeof frame.getContext === 'function') {
            const width = Math.round(Number(frame.width));
            const height = Math.round(Number(frame.height));
            if (!(width > 0 && height > 0)) {
                throw new GifEncoderError('GIF Canvas 尺寸无效', 'invalid_frame');
            }
            const context = frame.getContext('2d', { willReadFrequently: true });
            if (!context || typeof context.getImageData !== 'function') {
                throw new GifEncoderError('GIF Canvas 不支持读取像素', 'invalid_frame');
            }
            return { width, height, data: context.getImageData(0, 0, width, height).data };
        }
        throw new GifEncoderError('GIF 帧必须提供 ImageData 或 Canvas', 'invalid_frame');
    }

    function resolveDimensions(frame, options) {
        const source = framePixels(frame);
        let width = Math.max(1, Math.round(finitePositive(options.width, source.width)));
        let height = Math.max(1, Math.round(finitePositive(options.height, source.height)));
        const maxDimension = Number(options.maxDimension);
        if (Number.isFinite(maxDimension) && maxDimension > 0) {
            const scale = Math.min(1, maxDimension / Math.max(width, height));
            width = Math.max(1, Math.round(width * scale));
            height = Math.max(1, Math.round(height * scale));
        }
        if (width > MAX_GIF_DIMENSION || height > MAX_GIF_DIMENSION) {
            throw new GifEncoderError('GIF 尺寸超过格式限制', 'dimensions_too_large');
        }
        return { width, height };
    }

    function quantizeFrame(frame, width, height, options) {
        const source = framePixels(frame);
        const pixels = new Uint8Array(width * height);
        const alphaThreshold = Math.max(0, Math.min(255, Number.isFinite(Number(options.alphaThreshold))
            ? Number(options.alphaThreshold)
            : 128));
        const sourceWidth = source.width;
        const sourceHeight = source.height;
        const sameSize = sourceWidth === width && sourceHeight === height;
        let outputOffset = 0;
        for (let y = 0; y < height; y += 1) {
            const sourceY = sameSize
                ? y
                : Math.min(sourceHeight - 1, Math.floor(y * sourceHeight / height));
            for (let x = 0; x < width; x += 1) {
                const sourceX = sameSize
                    ? x
                    : Math.min(sourceWidth - 1, Math.floor(x * sourceWidth / width));
                const sourceOffset = (sourceY * sourceWidth + sourceX) * 4;
                const alpha = Number(source.data[sourceOffset + 3]);
                if (!Number.isFinite(alpha) || alpha < alphaThreshold) {
                    pixels[outputOffset] = TRANSPARENT_INDEX;
                } else {
                    const base = sourceOffset;
                    pixels[outputOffset] = options.lookup(
                        source.data[base],
                        source.data[base + 1],
                        source.data[base + 2],
                    );
                }
                outputOffset += 1;
            }
        }
        return pixels;
    }

    // GIF LZW codes are packed least-significant bit first.  The dictionary
    // key packs a 12-bit prefix and one 8-bit suffix into a regular integer,
    // avoiding large string allocations for 512px animation frames.
    function lzwEncode(indices, minimumCodeSize = 8) {
        const clearCode = 1 << minimumCodeSize;
        const endCode = clearCode + 1;
        // Keeping the dictionary at the initial code width is a deliberate
        // compatibility choice.  A clear code is cheap for the small,
        // palette-indexed frames used by the editor and avoids decoder
        // differences around the first code-width boundary in older GIF
        // readers. Stop one code before that boundary so the encoder and
        // decoder never disagree about the width of the clear code.
        const maxCode = (1 << (minimumCodeSize + 1)) - 1;
        let nextCode;
        let codeSize;
        let dictionary;
        let bitBuffer = 0;
        let bitCount = 0;
        const bytes = [];

        const reset = () => {
            dictionary = new Map();
            nextCode = endCode + 1;
            codeSize = minimumCodeSize + 1;
        };
        const emit = (code) => {
            bitBuffer |= (code << bitCount);
            bitCount += codeSize;
            while (bitCount >= 8) {
                bytes.push(bitBuffer & 0xff);
                bitBuffer >>>= 8;
                bitCount -= 8;
            }
        };

        reset();
        emit(clearCode);
        if (!indices.length) {
            emit(endCode);
        } else {
            let prefix = indices[0];
            for (let index = 1; index < indices.length; index += 1) {
                const suffix = indices[index];
                const key = (prefix << 8) | suffix;
                const existing = dictionary.get(key);
                if (existing !== undefined) {
                    prefix = existing;
                    continue;
                }
                emit(prefix);
                if (nextCode < maxCode) {
                    dictionary.set(key, nextCode);
                    nextCode += 1;
                } else {
                    emit(clearCode);
                    reset();
                }
                prefix = suffix;
            }
            emit(prefix);
            emit(endCode);
        }
        if (bitCount > 0) bytes.push(bitBuffer & 0xff);
        return Uint8Array.from(bytes);
    }

    function appendSubBlocks(target, bytes) {
        for (let offset = 0; offset < bytes.length; offset += 255) {
            const length = Math.min(255, bytes.length - offset);
            target.push(length);
            for (let index = 0; index < length; index += 1) target.push(bytes[offset + index]);
        }
        target.push(0);
    }

    function frameDelayCentiseconds(frame, options) {
        const explicit = Number(frame?.delayMs);
        const fallback = 1000 / finitePositive(options.fps, 20);
        const milliseconds = Number.isFinite(explicit) && explicit > 0 ? explicit : fallback;
        const delay = milliseconds / 10;
        return delay < 1.5 ? 1 : Math.max(2, Math.min(0xffff, delay));
    }

    // Accepts either the descriptor returned by buildAdaptivePalette (so a caller
    // that re-encodes the same frames at several sizes can sample once) or a bare
    // Uint8Array RGB table.
    function resolvePalette(frames, options, colorCount) {
        const supplied = options.palette;
        if (supplied) {
            const table = supplied instanceof Uint8Array ? supplied : supplied.palette;
            if (table && table.length >= colorCount * 3) {
                const colors = Number(supplied.colors);
                // Copy exactly colorCount*3 bytes: the packed colour-table field can
                // only declare a power-of-two size, so a longer table would desync
                // every byte that follows it.
                const normalized = new Uint8Array(colorCount * 3);
                for (let index = 0; index < normalized.length; index += 1) {
                    normalized[index] = table[index];
                }
                return {
                    palette: normalized,
                    colors: Math.max(1, Math.min(colorCount - 1, Number.isFinite(colors) ? Math.round(colors) : colorCount - 1)),
                };
            }
        }
        if (options.adaptive === false) return null;
        return buildAdaptivePalette(frames, colorCount, options);
    }

    function encode(framesInput, options = {}) {
        const frames = Array.from(framesInput || []);
        if (!frames.length) throw new GifEncoderError('GIF 至少需要一帧', 'no_frames');
        const dimensions = resolveDimensions(frames[0], options);
        const width = dimensions.width;
        const height = dimensions.height;
        const colorCount = normalizeColorCount(options.colors);
        const colorTableBits = Math.round(Math.log2(colorCount)) - 1;
        const minimumCodeSize = Math.max(2, Math.round(Math.log2(colorCount)));
        const adaptive = resolvePalette(frames, options, colorCount);
        const palette = adaptive ? adaptive.palette : buildPalette(colorCount);
        const lookup = adaptive
            ? buildNearestIndexer(adaptive)
            : latticeLookup(colorCount);
        const output = [];

        writeAscii(output, 'GIF89a');
        writeU16(output, width);
        writeU16(output, height);
        // Global colour table present; 8 bits per primary colour.  The table
        // size is 128 by default or 256 in quality mode.
        output.push(0xf0 | colorTableBits | 0x80, TRANSPARENT_INDEX, 0);
        for (let index = 0; index < palette.length; index += 1) output.push(palette[index]);

        const loopCount = options.loop === false
            ? null
            : Math.max(0, Math.min(0xffff, Math.round(Number(options.loopCount) || 0)));
        if (loopCount !== null) {
            output.push(0x21, 0xff, 0x0b);
            writeAscii(output, 'NETSCAPE2.0');
            output.push(0x03, 0x01, loopCount & 0xff, (loopCount >>> 8) & 0xff, 0x00);
        }

        let exactTime = 0;
        let encodedTime = 0;
        for (const frame of frames) {
            const indices = quantizeFrame(frame, width, height, { ...options, colorCount, lookup });
            // GIF timestamps are stored in centiseconds. Round cumulative time
            // so a 33.33 ms interval does not lose 3.33 ms on every frame.
            exactTime += frameDelayCentiseconds(frame, options);
            const nextTime = Math.round(exactTime);
            const delay = nextTime - encodedTime;
            encodedTime = nextTime;
            const hasTransparency = options.transparent !== false;
            // Restore-to-background prevents transparent pixels in a later
            // full-size frame from revealing the previous frame underneath.
            // Without disposal=2, moving character parts leave trails in
            // viewers that composite GIF frames rather than replacing them.
            output.push(0x21, 0xf9, 0x04, 0x08 | (hasTransparency ? 0x01 : 0x00));
            output.push(delay & 0xff, (delay >>> 8) & 0xff);
            output.push(hasTransparency ? TRANSPARENT_INDEX : 0, 0x00);
            output.push(0x2c);
            writeU16(output, 0);
            writeU16(output, 0);
            writeU16(output, width);
            writeU16(output, height);
            output.push(0x00); // no local colour table; use the global table
            output.push(minimumCodeSize);
            appendSubBlocks(output, lzwEncode(indices, minimumCodeSize));
        }
        output.push(0x3b);
        return Uint8Array.from(output);
    }

    function toBlob(frames, options = {}) {
        const bytes = encode(frames, options);
        if (typeof Blob === 'function') return new Blob([bytes], { type: 'image/gif' });
        return bytes;
    }

    // Lets a caller sample the palette once from the complete frame list and reuse
    // it across repeated encodes, so a byte-budget retry loop cannot make the
    // animation change colour between attempts.
    function buildPaletteFromFrames(frames, options = {}) {
        const list = Array.from(frames || []);
        if (!list.length) throw new GifEncoderError('GIF 至少需要一帧', 'no_frames');
        return buildAdaptivePalette(list, normalizeColorCount(options.colors), options);
    }

    return Object.freeze({
        GifEncoderError,
        palette: PALETTE,
        encode,
        toBlob,
        lzwEncode,
        buildPaletteFromFrames,
    });
}));
