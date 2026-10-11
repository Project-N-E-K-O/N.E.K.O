/** Validate upload containers before image decode; browser decoding applies EXIF orientation. */
(function () {
    'use strict';

    function fail(code) {
        const error = new Error(code);
        error.code = code;
        throw error;
    }

    function dimensions(bytes) {
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        const ascii = function (offset, count) {
            return String.fromCharCode.apply(null, bytes.subarray(offset, offset + count));
        };
        if (bytes.length >= 24 && ascii(0, 8) === '\x89PNG\r\n\x1a\n' && ascii(12, 4) === 'IHDR') {
            let offset = 8;
            while (offset + 12 <= bytes.length) {
                const size = view.getUint32(offset);
                if (ascii(offset + 4, 4) === 'acTL') fail('chat_avatar_invalid_image');
                if (size > bytes.length - offset - 12) break; // Decoder rejects truncated chunks.
                offset += size + 12;
            }
            return { width: view.getUint32(16), height: view.getUint32(20) };
        }
        if (bytes.length >= 4 && bytes[0] === 0xff && bytes[1] === 0xd8) {
            let offset = 2;
            while (offset + 4 <= bytes.length) {
                if (bytes[offset++] !== 0xff) fail('chat_avatar_invalid_image');
                while (bytes[offset] === 0xff) ++offset;
                const marker = bytes[offset++];
                if (marker === 0xd9 || marker === 0xda) break;
                if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
                if (offset + 2 > bytes.length) break;
                const size = view.getUint16(offset);
                if (size < 2 || offset + size > bytes.length) break;
                if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
                    if (size < 8) break;
                    return { width: view.getUint16(offset + 5), height: view.getUint16(offset + 3) };
                }
                offset += size;
            }
            fail('chat_avatar_invalid_image');
        }
        if (bytes.length >= 25 && ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WEBP') {
            const kind = ascii(12, 4);
            if (kind === 'VP8X' && bytes.length >= 30) {
                if (bytes[20] & 0x02) fail('chat_avatar_invalid_image'); // Animated WebP.
                const read24 = function (offset) { return bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16); };
                return { width: read24(24) + 1, height: read24(27) + 1 };
            }
            if (kind === 'VP8 ' && bytes.length >= 30 && ascii(23, 3) === '\x9d\x01\x2a') {
                return { width: view.getUint16(26, true) & 0x3fff, height: view.getUint16(28, true) & 0x3fff };
            }
            if (kind === 'VP8L' && bytes[20] === 0x2f) {
                return {
                    width: 1 + (bytes[21] | ((bytes[22] & 0x3f) << 8)),
                    height: 1 + ((bytes[22] >> 6) | (bytes[23] << 2) | ((bytes[24] & 0x0f) << 10))
                };
            }
        }
        fail('chat_avatar_invalid_image');
    }

    function checkPixels(width, height, maximum) {
        if (!width || !height || !Number.isFinite(width * height)) fail('chat_avatar_invalid_image');
        if (width * height > maximum) fail('chat_avatar_too_many_pixels');
    }

    async function decodeFile(file, limits) {
        if (!limits) fail('chat_avatar_unavailable');
        if (!file || file.size > limits.source_max_bytes) fail('chat_avatar_too_large');
        const bytes = new Uint8Array(await file.arrayBuffer());
        const header = dimensions(bytes);
        checkPixels(header.width, header.height, limits.source_max_pixels);
        const url = URL.createObjectURL(file);
        let retained = false;
        try {
            const image = new Image();
            await new Promise(function (resolve, reject) {
                image.onload = function () { image.onload = image.onerror = null; resolve(); };
                image.onerror = function () { image.onload = image.onerror = null; reject(new Error('chat_avatar_invalid_image')); };
                image.src = url;
            });
            checkPixels(image.naturalWidth, image.naturalHeight, limits.source_max_pixels);
            retained = true;
            let released = false;
            return {
                url: url, width: image.naturalWidth, height: image.naturalHeight,
                release: function () {
                    if (released) return;
                    released = true;
                    URL.revokeObjectURL(url);
                }
            };
        } catch (cause) {
            if (cause && cause.code) throw cause;
            fail('chat_avatar_invalid_image');
        } finally {
            if (!retained) URL.revokeObjectURL(url);
        }
    }

    function pngBlob(dataUrl, maximumBytes) {
        if (!dataUrl.startsWith('data:image/png;base64,')) fail('chat_avatar_invalid_image');
        const binary = atob(dataUrl.slice(22));
        if (binary.length > maximumBytes) fail('chat_avatar_too_large');
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; ++i) bytes[i] = binary.charCodeAt(i);
        return new Blob([bytes], { type: 'image/png' });
    }

    window.appChatAvatarImage = { decodeFile: decodeFile, pngBlob: pngBlob };
})();
