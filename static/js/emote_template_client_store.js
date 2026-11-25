/*
 * Client-side, ephemeral ZIP asset store for Spine ZIP packages.
 *
 * This bridge is deliberately independent from the server metadata adapter:
 * the selected File is read into memory, bounded with the same limits as the
 * Python adapter, and never written to disk/localStorage.  The Spine renderer
 * consumes `open()`/`url()` without knowing how the ZIP was
 * decoded.  The UMD wrapper also makes the small API smoke-testable in Node.
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory(
            typeof require === 'function' ? require('../libs/fflate.umd.js') : null,
            typeof globalThis !== 'undefined' ? globalThis : root
        );
        return;
    }

    root.NekoEmoteClientZipStore = factory(root.fflate, root);
}(typeof window !== 'undefined' ? window : globalThis, function (fflate, root) {
    'use strict';

    const MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;
    const MAX_ARCHIVE_ENTRIES = 5000;
    const MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024;
    const MAX_FILE_BYTES = 64 * 1024 * 1024;
    const MAX_COMPRESSION_RATIO = 1000;
    const ALLOWED_SUFFIXES = Object.freeze([
        '.json', '.png', '.atlas', '.skel', '.atlas.txt', '.skel.bytes'
    ]);
    const MIME_TYPES = Object.freeze({
        '.json': 'application/json',
        '.png': 'image/png',
        '.atlas': 'text/plain',
        '.skel': 'application/octet-stream',
        '.atlas.txt': 'text/plain',
        '.skel.bytes': 'application/octet-stream'
    });

    class ClientZipAssetStoreError extends Error {
        constructor(message, code = 'invalid_archive') {
            super(message);
            this.name = 'ClientZipAssetStoreError';
            this.code = code;
        }
    }

    function normalizePath(value) {
        let path = String(value || '').replace(/\\/g, '/').normalize('NFC');
        if (!path || path.startsWith('/') || path.includes('\0')) {
            throw new ClientZipAssetStoreError('ZIP 包含不安全的绝对路径', 'unsafe_path');
        }
        const parts = [];
        for (const part of path.split('/')) {
            if (!part || part === '.') continue;
            if (part === '..') {
                throw new ClientZipAssetStoreError('ZIP 包含不安全的路径穿越', 'unsafe_path');
            }
            parts.push(part);
        }
        if (!parts.length) return '';
        return parts.join('/');
    }

    function suffix(path) {
        const lower = String(path || '').toLowerCase();
        if (lower.endsWith('.atlas.txt')) return '.atlas.txt';
        if (lower.endsWith('.skel.bytes')) return '.skel.bytes';
        const index = lower.lastIndexOf('.');
        return index >= 0 ? lower.slice(index) : '';
    }

    function isAllowed(path) {
        const value = suffix(path);
        return ALLOWED_SUFFIXES.includes(value);
    }

    function mimeFor(path) {
        return MIME_TYPES[suffix(path)] || 'application/octet-stream';
    }

    function toUint8Array(value) {
        if (value instanceof Uint8Array) return value;
        if (value instanceof ArrayBuffer) return new Uint8Array(value);
        if (ArrayBuffer.isView(value)) {
            return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
        }
        throw new ClientZipAssetStoreError('ZIP 内容不是二进制数据', 'invalid_input');
    }

    function zipEntryNames(bytes) {
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        let end = bytes.length - 22;
        const minimum = Math.max(0, bytes.length - 65557);
        while (end >= minimum && view.getUint32(end, true) !== 0x06054b50) end -= 1;
        if (end < minimum) throw new ClientZipAssetStoreError('ZIP 目录无效', 'decode_failed');
        if (view.getUint16(end + 4, true) || view.getUint16(end + 6, true)
            || view.getUint16(end + 8, true) !== view.getUint16(end + 10, true)) {
            throw new ClientZipAssetStoreError('ZIP 目录计数或磁盘编号无效', 'decode_failed');
        }
        let count = view.getUint16(end + 8, true); // Match the bundled decoder.
        let size = view.getUint32(end + 12, true);
        let offset = view.getUint32(end + 16, true);
        let directoryLimit = end;
        if (count === 0xffff || offset === 0xffffffff) {
            const locator = end - 20;
            if (locator < 0 || view.getUint32(locator, true) !== 0x07064b50
                || view.getUint32(locator + 4, true) !== 0
                || view.getUint32(locator + 12, true) !== 0
                || view.getUint32(locator + 16, true) !== 1) {
                throw new ClientZipAssetStoreError('ZIP64 目录无效', 'decode_failed');
            }
            const record = view.getUint32(locator + 8, true);
            if (record + 56 > locator || view.getUint32(record, true) !== 0x06064b50
                || view.getUint32(record + 8, true) !== 0 || view.getUint32(record + 4, true) < 44
                || record + 12 + view.getUint32(record + 4, true) > locator
                || view.getUint32(record + 16, true) !== 0 || view.getUint32(record + 20, true) !== 0
                || view.getUint32(record + 28, true) !== 0 || view.getUint32(record + 36, true) !== 0
                || view.getUint32(record + 44, true) !== 0 || view.getUint32(record + 52, true) !== 0
                || view.getUint32(record + 24, true) !== view.getUint32(record + 32, true)
                || (count !== 0xffff && count !== view.getUint32(record + 32, true))
                || (offset !== 0xffffffff && offset !== view.getUint32(record + 48, true))
                || (size !== 0xffffffff && size !== view.getUint32(record + 40, true))) {
                throw new ClientZipAssetStoreError('ZIP64 目录超出上限', 'decode_failed');
            }
            count = view.getUint32(record + 32, true);
            size = view.getUint32(record + 40, true);
            offset = view.getUint32(record + 48, true);
            directoryLimit = record;
        }
        const directoryEnd = offset + size;
        if (directoryEnd > directoryLimit) {
            throw new ClientZipAssetStoreError('ZIP 目录超出范围', 'decode_failed');
        }
        if (count > MAX_ARCHIVE_ENTRIES) {
            throw new ClientZipAssetStoreError('ZIP 文件数超过上限（最多 5000 个）', 'too_many_entries');
        }
        const names = new Map();
        const utf8 = new TextDecoder('utf-8', { fatal: true });
        // zipfile's fallback is CP437, while fflate exposes legacy names as Latin-1.
        const cp437 = 'ÇüéâäàåçêëèïîìÄÅÉæÆôöòûùÿÖÜ¢£¥₧ƒáíóúñÑªº¿⌐¬½¼¡«»'
            + '░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀'
            + 'αßΓπΣσµτΦΘΩδ∞φε∩≡±≥≤⌠⌡÷≈°∙·√ⁿ²■\u00a0';
        for (let index = 0; index < count; index += 1) {
            if (offset + 46 > directoryEnd || view.getUint32(offset, true) !== 0x02014b50
                || view.getUint16(offset + 34, true) !== 0) {
                throw new ClientZipAssetStoreError('ZIP 目录条目无效', 'decode_failed');
            }
            const flaggedUtf8 = Boolean(view.getUint16(offset + 8, true) & 0x800);
            const length = view.getUint16(offset + 28, true);
            const start = offset + 46;
            const next = start + length + view.getUint16(offset + 30, true) + view.getUint16(offset + 32, true);
            if (next > directoryEnd) throw new ClientZipAssetStoreError('ZIP 文件名无效', 'decode_failed');
            const raw = bytes.subarray(start, start + length);
            const extraEnd = start + length + view.getUint16(offset + 30, true);
            const zip64Fields = [24, 20, 42].filter((field) => view.getUint32(offset + field, true) === 0xffffffff).length;
            let zip64Found = false;
            for (let extra = start + length; extra < extraEnd;) {
                if (extra + 4 > extraEnd) throw new ClientZipAssetStoreError('ZIP 扩展字段无效', 'decode_failed');
                const tag = view.getUint16(extra, true);
                const size = view.getUint16(extra + 2, true);
                if (extra + 4 + size > extraEnd || (tag === 1 && size < zip64Fields * 8)) {
                    throw new ClientZipAssetStoreError('ZIP64 扩展字段无效', 'decode_failed');
                }
                if (tag === 1) zip64Found = true;
                extra += 4 + size;
            }
            if (zip64Fields && !zip64Found) {
                throw new ClientZipAssetStoreError('ZIP64 扩展字段缺失', 'decode_failed');
            }
            const key = flaggedUtf8 ? utf8.decode(raw) : Array.from(raw, (byte) => String.fromCharCode(byte)).join('');
            let decoded;
            try { decoded = utf8.decode(raw); } catch (error) {
                if (flaggedUtf8) throw error;
                try { decoded = new TextDecoder('gb18030', { fatal: true }).decode(raw); } catch (_) {
                    decoded = Array.from(raw, (byte) => byte < 128 ? String.fromCharCode(byte) : cp437[byte - 128]).join('');
                }
            }
            if (names.has(key)) throw new ClientZipAssetStoreError('ZIP 包含重复文件名', 'duplicate_path');
            names.set(key, decoded);
            offset = next;
        }
        return names;
    }

    function unzipBounded(bytes) {
        if (!fflate || typeof fflate.unzipSync !== 'function') {
            return Promise.reject(new ClientZipAssetStoreError(
                '客户端 ZIP 解压组件未加载', 'decoder_unavailable'
            ));
        }
        if (bytes.byteLength > MAX_ARCHIVE_BYTES) {
            return Promise.reject(new ClientZipAssetStoreError(
                '模板包过大，当前导入上限为 64 MB', 'archive_too_large'
            ));
        }

        let entryCount = 0;
        let uncompressedTotal = 0;
        let validationError = null;
        const seen = new Set();
        let entryNames;
        try { entryNames = zipEntryNames(bytes); } catch (error) { return Promise.reject(error); }
        const filter = (meta) => {
            entryCount += 1;
            if (entryCount > MAX_ARCHIVE_ENTRIES) {
                validationError ||= new ClientZipAssetStoreError(
                    'ZIP 文件数超过上限（最多 5000 个）', 'too_many_entries'
                );
                return false;
            }

            let path = '';
            try {
                path = normalizePath(entryNames.get(meta?.name) ?? meta?.name);
            } catch (error) {
                validationError ||= error;
                return false;
            }
            if (!path) return false;

            const key = path.toLocaleLowerCase();
            if (seen.has(key)) {
                validationError ||= new ClientZipAssetStoreError(
                    `ZIP 包含重复路径：${path}`, 'duplicate_path'
                );
                return false;
            }
            seen.add(key);

            const compressed = Number(meta?.size) || 0;
            const expanded = Number(meta?.originalSize) || 0;
            if (expanded > MAX_FILE_BYTES) {
                validationError ||= new ClientZipAssetStoreError(
                    `ZIP 文件过大：${path}`, 'file_too_large'
                );
                return false;
            }
            uncompressedTotal += expanded;
            if (uncompressedTotal > MAX_UNCOMPRESSED_BYTES) {
                validationError ||= new ClientZipAssetStoreError(
                    'ZIP 解压后大小超过上限（最多 512 MB）', 'expanded_too_large'
                );
                return false;
            }
            if (compressed > 0 && expanded / compressed > MAX_COMPRESSION_RATIO) {
                validationError ||= new ClientZipAssetStoreError(
                    `ZIP 压缩比异常：${path}`, 'compression_ratio'
                );
                return false;
            }

            // Validate every path, but only retain active template resources.
            // Scripts, executables and nested archives
            // are intentionally never handed to the renderer.
            return isAllowed(path);
        };

        // The browser runs this decoder in our dedicated worker. Node and
        // environments without Worker use the same bounded decoder directly.
        let files;
        try {
            files = fflate.unzipSync(bytes, { filter });
        } catch (error) {
            return Promise.reject(new ClientZipAssetStoreError(
                `ZIP 解压失败：${error.message || error}`, 'decode_failed'
            ));
        }
        if (validationError) return Promise.reject(validationError);

        const retained = new Map();
        for (const [rawName, value] of Object.entries(files || {})) {
            let path;
            try {
                path = normalizePath(entryNames.get(rawName) ?? rawName);
            } catch (pathError) {
                return Promise.reject(pathError);
            }
            if (!path || !isAllowed(path)) continue;
            retained.set(path, new Uint8Array(value));
        }
        if (!retained.size) {
            return Promise.reject(new ClientZipAssetStoreError(
                'ZIP 中没有可读取的 Spine 资源', 'no_assets'
            ));
        }
        return Promise.resolve(retained);
    }

    function unzipInWorker(bytes) {
        return new Promise((resolve, reject) => {
            const worker = new root.Worker('/static/js/emote_template_zip_worker.js');
            worker.onmessage = ({ data }) => {
                worker.terminate();
                if (!data?.success) {
                    reject(new ClientZipAssetStoreError(data?.message || 'ZIP 解压失败', data?.code || 'decode_failed'));
                    return;
                }
                resolve(new Map(data.entries.map(([path, buffer]) => [path, new Uint8Array(buffer)])));
            };
            worker.onerror = () => {
                worker.terminate();
                reject(new ClientZipAssetStoreError('ZIP 解压线程不可用', 'decoder_unavailable'));
            };
            try {
                // Do not detach the caller's buffer; retained assets return by transfer.
                const input = bytes.slice();
                worker.postMessage(input.buffer, [input.buffer]);
            } catch (error) {
                worker.terminate();
                reject(new ClientZipAssetStoreError(String(error.message || error), 'decode_failed'));
            }
        });
    }

    class ClientZipAssetStore {
        constructor(entries, metadata = {}) {
            this._entries = entries;
            this._metadata = Object.freeze({
                compressedBytes: Number(metadata.compressedBytes) || 0,
                entryCount: entries.size,
                uncompressedBytes: [...entries.values()].reduce((sum, bytes) => sum + bytes.byteLength, 0)
            });
            this._urls = new Map();
            this._released = false;
        }

        static async fromBytes(value, metadata = {}) {
            const bytes = toUint8Array(value);
            if (bytes.byteLength > MAX_ARCHIVE_BYTES) {
                throw new ClientZipAssetStoreError('模板包过大，当前导入上限为 64 MB', 'archive_too_large');
            }
            const entries = await (root?.document && typeof root.Worker === 'function'
                ? unzipInWorker(bytes) : unzipBounded(bytes));
            return new ClientZipAssetStore(entries, {
                ...metadata,
                compressedBytes: bytes.byteLength
            });
        }

        static async fromFile(file) {
            if (!file || typeof file.arrayBuffer !== 'function') {
                throw new ClientZipAssetStoreError('没有可读取的模板包', 'invalid_input');
            }
            const size = Number(file.size) || 0;
            if (size > MAX_ARCHIVE_BYTES) {
                throw new ClientZipAssetStoreError(
                    '模板包过大，当前导入上限为 64 MB', 'archive_too_large'
                );
            }
            const bytes = await file.arrayBuffer();
            return ClientZipAssetStore.fromBytes(bytes, { filename: file.name || '' });
        }

        _assertLive() {
            if (this._released) {
                throw new ClientZipAssetStoreError('模板资源已释放', 'released');
            }
        }

        _lookup(path) {
            this._assertLive();
            let normalized;
            try {
                normalized = normalizePath(path);
            } catch (_) {
                return null;
            }
            const exact = this._entries.get(normalized);
            if (exact) return exact;

            // ZIP filename decoders do not all agree on legacy GBK/CP437
            // names.  The Python metadata adapter and a browser decoder can
            // therefore disagree only on a package-root prefix.  Resolve a
            // missing path by case-folded basename/tail only when it is
            // unambiguous; never guess between two same-named texture pages.
            const requestedParts = normalized.split('/');
            const requestedBase = requestedParts.at(-1).toLowerCase();
            const baseMatches = [];
            for (const [candidate, bytes] of this._entries) {
                if (candidate.split('/').at(-1).toLowerCase() === requestedBase) {
                    baseMatches.push({ candidate, bytes });
                }
            }
            if (baseMatches.length === 1) return baseMatches[0].bytes;

            for (let tailLength = Math.min(requestedParts.length, 8); tailLength >= 2; tailLength -= 1) {
                const tail = requestedParts.slice(-tailLength).join('/').toLowerCase();
                const tailMatches = [];
                for (const [candidate, bytes] of this._entries) {
                    if (candidate.toLowerCase().endsWith(`/${tail}`)) {
                        tailMatches.push({ candidate, bytes });
                    }
                }
                if (tailMatches.length === 1) return tailMatches[0].bytes;
                if (tailMatches.length > 1) break;
            }
            return null;
        }

        has(path) {
            return Boolean(this._lookup(path));
        }

        list() {
            this._assertLive();
            return [...this._entries.keys()];
        }

        get(path) {
            const bytes = this._lookup(path);
            return bytes ? bytes.slice() : null;
        }

        takeEntries() {
            this._assertLive();
            const entries = [...this._entries];
            this._entries.clear(); // Ownership moves to the recipient; do not zero these buffers.
            this.release();
            return entries;
        }

        async open(path, options = {}) {
            const bytes = this._lookup(path);
            if (!bytes) throw new ClientZipAssetStoreError(`模板资源不存在：${path}`, 'not_found');
            const mode = options.as || 'arrayBuffer';
            if (mode === 'bytes') return bytes.slice();
            if (mode === 'text') {
                return new TextDecoder(options.encoding || 'utf-8').decode(bytes);
            }
            if (mode === 'blob') {
                return new Blob([bytes], { type: options.type || mimeFor(path) });
            }
            return bytes.slice().buffer;
        }

        url(path, options = {}) {
            const normalized = normalizePath(path);
            const bytes = this._lookup(normalized);
            if (!bytes) throw new ClientZipAssetStoreError(`模板资源不存在：${path}`, 'not_found');
            this._assertLive();
            const cached = this._urls.get(normalized);
            if (cached) return cached;
            const urlApi = root && root.URL;
            if (!urlApi || typeof urlApi.createObjectURL !== 'function') {
                throw new ClientZipAssetStoreError('当前环境不支持 Blob URL', 'url_unavailable');
            }
            const url = urlApi.createObjectURL(new Blob([bytes], {
                type: options.type || mimeFor(normalized)
            }));
            this._urls.set(normalized, url);
            return url;
        }

        stats() {
            return { ...this._metadata, objectUrls: this._urls.size, released: this._released };
        }

        release() {
            if (this._released) return;
            const urlApi = root && root.URL;
            if (urlApi && typeof urlApi.revokeObjectURL === 'function') {
                for (const url of this._urls.values()) {
                    try { urlApi.revokeObjectURL(url); } catch (_) { /* best effort */ }
                }
            }
            this._urls.clear();
            for (const bytes of this._entries.values()) bytes.fill(0);
            this._entries.clear();
            this._released = true;
        }
    }

    return Object.freeze({
        ClientZipAssetStore,
        ClientZipAssetStoreError,
        constants: Object.freeze({
            MAX_ARCHIVE_BYTES,
            MAX_ARCHIVE_ENTRIES,
            MAX_UNCOMPRESSED_BYTES,
            MAX_FILE_BYTES,
            MAX_COMPRESSION_RATIO
        }),
        normalizePath
    });
}));
