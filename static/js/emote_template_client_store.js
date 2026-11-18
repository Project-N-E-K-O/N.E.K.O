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
                path = normalizePath(meta?.name);
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

        // Use the synchronous decoder deliberately.  The browser package is
        // small enough for the local Spine ZIP import path, while the async
        // fflate worker adapter is not available in every Electron/Node
        // embedding (and would make the same API nondeterministic in tests).
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
                path = normalizePath(rawName);
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
            const entries = await unzipBounded(bytes);
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
