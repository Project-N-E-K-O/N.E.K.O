(function () {
    'use strict';

    var MAX_FILES = 6;
    var MAX_TEXT_BYTES = 1024 * 1024;
    var MAX_TEXT_CHARS = 32000;
    var MAX_TOTAL_TEXT_CHARS = 90000;
    var MAX_IMAGE_BYTES = 10 * 1024 * 1024;
    var MAX_DOCUMENT_BYTES = 16 * 1024 * 1024;
    var MAX_IMAGE_PIXELS = 24000000;
    var MAX_IMAGE_SIDE = 1280;
    var MAX_IMAGE_DATA_URL_BYTES = 1200 * 1024;
    var TEXT_EXTENSIONS = [
        'txt', 'md', 'markdown', 'json', 'jsonl', 'csv', 'tsv', 'yaml', 'yml',
        'xml', 'html', 'htm', 'css', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx',
        'py', 'java', 'c', 'cc', 'cpp', 'h', 'hpp', 'cs', 'go', 'rs', 'rb',
        'php', 'sh', 'bash', 'zsh', 'fish', 'bat', 'cmd', 'ps1', 'ini', 'toml',
        'env', 'log', 'sql'
    ];
    var DOCUMENT_EXTENSIONS = ['pdf', 'docx', 'xlsx', 'pptx'];

    function getExtension(name) {
        var match = /\.([^.\\\/]+)$/.exec(String(name || '').toLowerCase());
        return match ? match[1] : '';
    }

    function safeName(name) {
        return String(name || '')
            .replace(/[\u0000-\u001F\u007F<>]/g, '')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 160) || 'unnamed';
    }

    async function readPrefix(file, count) {
        var slice = file.slice(0, Math.max(0, count || 0));
        return new Uint8Array(await slice.arrayBuffer());
    }

    function startsWith(bytes, signature) {
        if (!bytes || bytes.length < signature.length) return false;
        for (var i = 0; i < signature.length; i += 1) {
            if (bytes[i] !== signature[i]) return false;
        }
        return true;
    }

    function detectBinaryKind(bytes) {
        if (startsWith(bytes, [0xFF, 0xD8, 0xFF])) return 'jpeg';
        if (startsWith(bytes, [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])) return 'png';
        if (startsWith(bytes, [0x47, 0x49, 0x46, 0x38])) return 'gif';
        if (startsWith(bytes, [0x42, 0x4D])) return 'bmp';
        if (bytes.length >= 12
                && String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]) === 'RIFF'
                && String.fromCharCode(bytes[8], bytes[9], bytes[10], bytes[11]) === 'WEBP') {
            return 'webp';
        }
        if (startsWith(bytes, [0x25, 0x50, 0x44, 0x46])) return 'pdf';
        if (startsWith(bytes, [0x50, 0x4B, 0x03, 0x04])
                || startsWith(bytes, [0x50, 0x4B, 0x05, 0x06])
                || startsWith(bytes, [0x50, 0x4B, 0x07, 0x08])) {
            return 'zip';
        }
        if (startsWith(bytes, [0x4D, 0x5A])) return 'exe';
        if (startsWith(bytes, [0x7F, 0x45, 0x4C, 0x46])) return 'elf';
        if (startsWith(bytes, [0xCF, 0xFA, 0xED, 0xFE])
                || startsWith(bytes, [0xFE, 0xED, 0xFA, 0xCF])) {
            return 'macho';
        }
        return '';
    }

    function hasTextBom(bytes) {
        return startsWith(bytes, [0xEF, 0xBB, 0xBF])
            || startsWith(bytes, [0xFF, 0xFE])
            || startsWith(bytes, [0xFE, 0xFF]);
    }

    function looksBinaryPrefix(bytes) {
        if (!bytes || bytes.length === 0 || hasTextBom(bytes)) return false;
        var inspected = Math.min(bytes.length, 512);
        var zeroCount = 0;
        var controlCount = 0;
        for (var i = 0; i < inspected; i += 1) {
            var code = bytes[i];
            if (code === 0) zeroCount += 1;
            if (code < 32 && code !== 9 && code !== 10 && code !== 13) controlCount += 1;
        }
        return zeroCount > 0 || controlCount / inspected > 0.02;
    }

    function isImageKind(kind) {
        return kind === 'jpeg' || kind === 'png' || kind === 'gif' || kind === 'webp' || kind === 'bmp';
    }

    function isDocumentExtension(ext) {
        return DOCUMENT_EXTENSIONS.indexOf(String(ext || '').toLowerCase()) >= 0;
    }

    function hasKnownTextType(file) {
        var mime = String(file.type || '').toLowerCase();
        var ext = getExtension(file.name);
        if (mime.indexOf('text/') === 0) return true;
        if (/\/(json|xml|javascript|x-javascript|x-yaml|yaml|csv)$/i.test(mime)) return true;
        return TEXT_EXTENSIONS.indexOf(ext) >= 0;
    }

    function sniffStrictUtf8Text(prefix) {
        if (prefix && prefix.length > 0) {
            var inspected = Math.min(prefix.length, 512);
            var zeroCount = 0;
            var controlCount = 0;
            for (var i = 0; i < inspected; i += 1) {
                var code = prefix[i];
                if (code === 0) zeroCount += 1;
                if (code < 32 && code !== 9 && code !== 10 && code !== 13) controlCount += 1;
            }
            if (zeroCount > 0 || controlCount / inspected >= 0.02) return false;
            return decodeWith('utf-8', prefix, true) !== null;
        }
        return false;
    }

    function decodeWith(label, bytes, fatal) {
        try {
            return new TextDecoder(label, { fatal: !!fatal }).decode(bytes);
        } catch (_) {
            return null;
        }
    }

    function decodeText(bytes, options) {
        var requireStrictDecode = !!(options && options.requireStrictDecode);
        if (startsWith(bytes, [0xEF, 0xBB, 0xBF])) {
            return { text: decodeWith('utf-8', bytes.slice(3), true), encoding: 'utf-8-bom' };
        }
        if (startsWith(bytes, [0xFF, 0xFE])) {
            return { text: decodeWith('utf-16le', bytes.slice(2), true), encoding: 'utf-16le' };
        }
        if (startsWith(bytes, [0xFE, 0xFF])) {
            return { text: decodeWith('utf-16be', bytes.slice(2), true), encoding: 'utf-16be' };
        }

        var utf8 = decodeWith('utf-8', bytes, true);
        if (utf8 !== null) return { text: utf8, encoding: 'utf-8' };
        if (requireStrictDecode) return { text: null, encoding: '' };

        var gb = decodeWith('gb18030', bytes, false);
        if (gb !== null) return { text: gb, encoding: 'gb18030' };

        return { text: decodeWith('utf-8', bytes, false) || '', encoding: 'utf-8-replacement' };
    }

    function inspectTextQuality(text) {
        var source = String(text || '');
        if (!source.trim()) return { ok: false, reason: 'empty_text' };

        var replacementCount = 0;
        var controlCount = 0;
        var bidiCount = 0;
        for (var i = 0; i < source.length; i += 1) {
            var code = source.charCodeAt(i);
            if (source[i] === '\uFFFD') replacementCount += 1;
            if ((code < 32 && code !== 9 && code !== 10 && code !== 13) || (code >= 0x7F && code <= 0x9F)) {
                controlCount += 1;
            }
            if ((code >= 0x202A && code <= 0x202E) || (code >= 0x2066 && code <= 0x2069)) {
                bidiCount += 1;
            }
        }

        var length = Math.max(1, source.length);
        if (replacementCount / length > 0.005 || replacementCount > 16) {
            return { ok: false, reason: 'garbled_text' };
        }
        if (controlCount / length > 0.01 || controlCount > 64) {
            return { ok: false, reason: 'control_chars' };
        }
        if (bidiCount > 16) {
            return { ok: false, reason: 'bidi_controls' };
        }
        return { ok: true, reason: '' };
    }

    function sanitizeText(text) {
        return String(text || '')
            .replace(/\r\n?/g, '\n')
            .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/g, '')
            .replace(/[\u202A-\u202E\u2066-\u2069]/g, '')
            .trim();
    }

    function getDataUrlEncodedBytes(dataUrl) {
        var text = String(dataUrl || '');
        var commaIndex = text.indexOf(',');
        if (commaIndex < 0) return text.length;
        return Math.ceil((text.length - commaIndex - 1) * 3 / 4);
    }

    function readUint16BE(bytes, offset) {
        return (bytes[offset] << 8) | bytes[offset + 1];
    }

    function readUint16LE(bytes, offset) {
        return bytes[offset] | (bytes[offset + 1] << 8);
    }

    function readUint24LE(bytes, offset) {
        return bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16);
    }

    function readInt32LE(bytes, offset) {
        var value = bytes[offset]
            | (bytes[offset + 1] << 8)
            | (bytes[offset + 2] << 16)
            | (bytes[offset + 3] << 24);
        return value;
    }

    function readUint32BE(bytes, offset) {
        return ((bytes[offset] * 0x1000000)
            + ((bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3])) >>> 0;
    }

    function getJpegHeaderSize(bytes) {
        if (!startsWith(bytes, [0xFF, 0xD8])) return null;
        var offset = 2;
        while (offset + 9 < bytes.length) {
            if (bytes[offset] !== 0xFF) {
                offset += 1;
                continue;
            }
            while (offset < bytes.length && bytes[offset] === 0xFF) offset += 1;
            var marker = bytes[offset];
            offset += 1;
            if (marker === 0xD9 || marker === 0xDA) return null;
            if (offset + 2 > bytes.length) return null;
            var length = readUint16BE(bytes, offset);
            if (length < 2 || offset + length > bytes.length) return null;
            if ((marker >= 0xC0 && marker <= 0xC3)
                    || (marker >= 0xC5 && marker <= 0xC7)
                    || (marker >= 0xC9 && marker <= 0xCB)
                    || (marker >= 0xCD && marker <= 0xCF)) {
                return {
                    width: readUint16BE(bytes, offset + 5),
                    height: readUint16BE(bytes, offset + 3)
                };
            }
            offset += length;
        }
        return null;
    }

    function getImageHeaderSize(bytes, kind) {
        if (!bytes || bytes.length < 10) return null;
        if (kind === 'png' && bytes.length >= 24) {
            return { width: readUint32BE(bytes, 16), height: readUint32BE(bytes, 20) };
        }
        if (kind === 'gif' && bytes.length >= 10) {
            return { width: readUint16LE(bytes, 6), height: readUint16LE(bytes, 8) };
        }
        if (kind === 'bmp' && bytes.length >= 26) {
            return { width: Math.abs(readInt32LE(bytes, 18)), height: Math.abs(readInt32LE(bytes, 22)) };
        }
        if (kind === 'jpeg') {
            return getJpegHeaderSize(bytes);
        }
        if (kind === 'webp' && bytes.length >= 30) {
            var subtype = String.fromCharCode(bytes[12], bytes[13], bytes[14], bytes[15]);
            if (subtype === 'VP8X') {
                return { width: readUint24LE(bytes, 24) + 1, height: readUint24LE(bytes, 27) + 1 };
            }
            if (subtype === 'VP8 ' && bytes.length >= 30
                    && bytes[23] === 0x9D && bytes[24] === 0x01 && bytes[25] === 0x2A) {
                return { width: readUint16LE(bytes, 26) & 0x3FFF, height: readUint16LE(bytes, 28) & 0x3FFF };
            }
            if (subtype === 'VP8L' && bytes.length >= 25 && bytes[20] === 0x2F) {
                var bits = bytes[21] | (bytes[22] << 8) | (bytes[23] << 16) | (bytes[24] << 24);
                return { width: (bits & 0x3FFF) + 1, height: ((bits >>> 14) & 0x3FFF) + 1 };
            }
        }
        return null;
    }

    async function getImageHeaderSizeForFile(file, kind, prefix) {
        if (kind !== 'jpeg') {
            var header = prefix && prefix.length >= 64 ? prefix : await readPrefix(file, 65536);
            return getImageHeaderSize(header, kind);
        }

        var maxBytes = Math.min(file.size, MAX_IMAGE_BYTES);
        var count = Math.min(Math.max((prefix && prefix.length) || 0, 65536), maxBytes);
        while (count > 0 && count <= maxBytes) {
            var bytes = prefix && prefix.length >= count ? prefix : await readPrefix(file, count);
            var size = getImageHeaderSize(bytes, kind);
            if (size) return size;
            if (count >= maxBytes) return null;
            count = Math.min(maxBytes, count * 2);
        }
        return null;
    }

    function loadImageFromDataUrl(dataUrl) {
        return new Promise(function (resolve, reject) {
            var image = new Image();
            var settled = false;
            function finish(callback, value) {
                if (settled) return;
                settled = true;
                callback(value);
            }
            image.onload = function () {
                if (!image.naturalWidth || !image.naturalHeight) {
                    finish(reject, new Error('invalid_image_size'));
                    return;
                }
                finish(resolve, image);
            };
            image.onerror = function () {
                finish(reject, new Error('invalid_image_data'));
            };
            image.src = dataUrl;
        });
    }

    function readBlobAsDataUrl(blob) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () { resolve(String(reader.result || '')); };
            reader.onerror = function () { reject(reader.error || new Error('read_failed')); };
            reader.readAsDataURL(blob);
        });
    }

    async function loadImage(file) {
        if (typeof createImageBitmap === 'function') {
            try {
                return await createImageBitmap(file);
            } catch (_) {}
        }
        return loadImageFromDataUrl(await readBlobAsDataUrl(file));
    }

    function drawImageToJpeg(image, width, height, quality) {
        var canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        var context = canvas.getContext('2d');
        if (!context) throw new Error('canvas_unavailable');
        context.fillStyle = '#fff';
        context.fillRect(0, 0, width, height);
        context.drawImage(image, 0, 0, width, height);
        var dataUrl = canvas.toDataURL('image/jpeg', quality);
        if (!/^data:image\/jpe?g;base64,/i.test(dataUrl)) {
            throw new Error('image_encode_failed');
        }
        return dataUrl;
    }

    function getImageSize(image) {
        return {
            width: image.width || image.naturalWidth || 0,
            height: image.height || image.naturalHeight || 0
        };
    }

    async function parseImageFile(file, kind, prefix) {
        if (file.size > MAX_IMAGE_BYTES) {
            return { rejected: { reason: 'image_too_large' } };
        }

        var headerSize = await getImageHeaderSizeForFile(file, kind, prefix);
        if (!headerSize || !headerSize.width || !headerSize.height
                || headerSize.width * headerSize.height > MAX_IMAGE_PIXELS) {
            return { rejected: { reason: 'image_dimensions_invalid' } };
        }

        var image = await loadImage(file);
        var size = getImageSize(image);
        if (!size.width || !size.height || size.width * size.height > MAX_IMAGE_PIXELS) {
            if (image && typeof image.close === 'function') image.close();
            return { rejected: { reason: 'image_dimensions_invalid' } };
        }

        var scale = Math.min(1, MAX_IMAGE_SIDE / size.width, MAX_IMAGE_SIDE / size.height);
        var width = Math.max(1, Math.round(size.width * scale));
        var height = Math.max(1, Math.round(size.height * scale));
        var qualities = [0.82, 0.72, 0.62, 0.52, 0.42];
        var dataUrl = '';

        for (var attempt = 0; attempt < 5; attempt += 1) {
            for (var i = 0; i < qualities.length; i += 1) {
                dataUrl = drawImageToJpeg(image, width, height, qualities[i]);
                if (getDataUrlEncodedBytes(dataUrl) <= MAX_IMAGE_DATA_URL_BYTES) {
                    if (image && typeof image.close === 'function') image.close();
                    return {
                        item: {
                            type: 'image',
                            name: safeName(file.name),
                            mime: file.type || 'image/' + kind,
                            size: file.size,
                            width: width,
                            height: height,
                            dataUrl: dataUrl,
                            animated: kind === 'gif'
                        }
                    };
                }
            }
            width = Math.max(1, Math.round(width * 0.82));
            height = Math.max(1, Math.round(height * 0.82));
        }

        if (image && typeof image.close === 'function') image.close();
        return { rejected: { reason: 'image_encode_too_large' } };
    }

    async function parseTextFile(file, kind, options) {
        if (kind && kind !== 'text') {
            return { rejected: { reason: 'binary_file' } };
        }
        if (file.size > MAX_TEXT_BYTES) {
            return { rejected: { reason: 'text_too_large' } };
        }

        var bytes = new Uint8Array(await file.arrayBuffer());
        var decoded = decodeText(bytes, options);
        if (!decoded.text) {
            return { rejected: { reason: 'binary_file' } };
        }
        var text = sanitizeText(decoded.text);
        var quality = inspectTextQuality(text);
        if (!quality.ok) {
            return { rejected: { reason: quality.reason } };
        }
        if (text.length > MAX_TEXT_CHARS) {
            return { rejected: { reason: 'text_too_long' } };
        }

        var ext = getExtension(file.name);
        if (ext === 'json') {
            try {
                JSON.parse(text);
            } catch (_) {
                return { rejected: { reason: 'json_invalid' } };
            }
        }

        return {
            item: {
                type: 'text',
                name: safeName(file.name),
                mime: file.type || 'text/plain',
                size: file.size,
                chars: text.length,
                encoding: decoded.encoding,
                content: text
            }
        };
    }

    async function parseDocumentFile(file) {
        if (file.size > MAX_DOCUMENT_BYTES) {
            return { rejected: { reason: 'document_too_large' } };
        }

        var formData = new FormData();
        formData.append('file', file, file.name || 'document');

        var response = null;
        var payload = null;
        try {
            response = await fetch('/api/avatar-drop/parse-document', {
                method: 'POST',
                body: formData,
                credentials: 'same-origin'
            });
            payload = await response.json().catch(function () { return null; });
        } catch (_) {
            return { rejected: { reason: 'document_parse_unavailable' } };
        }

        if (!response || !response.ok || !payload || payload.ok !== true || !payload.item) {
            var detail = payload && payload.detail && typeof payload.detail === 'object' ? payload.detail : {};
            return { rejected: { reason: detail.code || 'document_parse_failed' } };
        }

        var item = payload.item || {};
        var content = String(item.content || '').trim();
        var quality = inspectTextQuality(content);
        if (!quality.ok) {
            return { rejected: { reason: quality.reason } };
        }
        if (!content) {
            return { rejected: { reason: 'no_readable_text' } };
        }

        return {
            item: {
                type: 'text',
                name: safeName(item.name || file.name),
                mime: String(item.mime || file.type || 'application/octet-stream'),
                size: Number(item.size || file.size || 0),
                chars: content.length,
                encoding: String(item.encoding || 'document-parser'),
                documentType: String(item.documentType || getExtension(file.name) || ''),
                truncated: item.truncated === true,
                content: content
            }
        };
    }

    async function parseOneFile(file) {
        var prefix = await readPrefix(file, 512);
        var kind = detectBinaryKind(prefix);
        var ext = getExtension(file.name);
        var mime = String(file.type || '').toLowerCase();

        if (ext === 'docm' || ext === 'xlsm' || ext === 'pptm') {
            return { rejected: { reason: 'macro_document_unsupported' } };
        }
        if (ext === 'doc' || ext === 'xls' || ext === 'ppt') {
            return { rejected: { reason: 'legacy_office_unsupported' } };
        }
        if (isDocumentExtension(ext) || kind === 'pdf') {
            return parseDocumentFile(file);
        }
        if (mime === 'image/svg+xml' || ext === 'svg') {
            return { rejected: { reason: 'svg_unsupported' } };
        }
        if (isImageKind(kind)) {
            return parseImageFile(file, kind, prefix);
        }
        if (mime.indexOf('image/') === 0) {
            return { rejected: { reason: 'image_type_unsupported' } };
        }
        if (kind) {
            return parseTextFile(file, kind);
        }
        if (looksBinaryPrefix(prefix)) {
            return { rejected: { reason: 'binary_file' } };
        }
        var knownTextType = hasKnownTextType(file);
        var strictSniffedText = !knownTextType && sniffStrictUtf8Text(prefix);
        if (!knownTextType && !strictSniffedText) {
            return { rejected: { reason: 'unsupported_file' } };
        }
        return parseTextFile(file, 'text', { requireStrictDecode: strictSniffedText });
    }

    async function parseFiles(fileList) {
        var files = Array.from(fileList || []).filter(function (file) {
            return file instanceof File;
        });
        var accepted = [];
        var rejected = [];
        var totalTextChars = 0;
        var overLimitCount = 0;
        var overLimitBytes = 0;

        for (var i = 0; i < files.length; i += 1) {
            var file = files[i];
            if (i >= MAX_FILES) {
                overLimitCount += 1;
                overLimitBytes += Number(file.size) || 0;
                continue;
            }

            try {
                var result = await parseOneFile(file);
                if (result && result.item) {
                    if (result.item.type === 'text') {
                        if (totalTextChars + result.item.chars > MAX_TOTAL_TEXT_CHARS) {
                            rejected.push({ name: safeName(file.name), size: file.size, reason: 'total_text_too_long' });
                            continue;
                        }
                        totalTextChars += result.item.chars;
                    }
                    accepted.push(result.item);
                } else {
                    rejected.push(Object.assign({
                        name: safeName(file.name),
                        size: file.size
                    }, result && result.rejected ? result.rejected : { reason: 'read_failed' }));
                }
            } catch (_) {
                rejected.push({ name: safeName(file.name), size: file.size, reason: 'read_failed' });
            }
        }
        if (overLimitCount > 0) {
            rejected.push({
                name: overLimitCount + ' more files',
                size: overLimitBytes,
                reason: 'too_many_files',
                count: overLimitCount
            });
        }

        return { accepted: accepted, rejected: rejected };
    }

    window.NekoAvatarDropParser = {
        parseFiles: parseFiles,
        limits: {
            maxFiles: MAX_FILES,
            maxTextBytes: MAX_TEXT_BYTES,
            maxTextChars: MAX_TEXT_CHARS,
            maxDocumentBytes: MAX_DOCUMENT_BYTES,
            maxImageBytes: MAX_IMAGE_BYTES,
            maxImageSide: MAX_IMAGE_SIDE
        }
    };
})();
