/*
 * 模板库：保存所有模板的编辑记录，最近使用的模板另保留缩略图缓存。
 *
 * 只做数据，不碰 DOM、不读文件、不 import 任何页面脚本 —— 这样它能在 Node 里直接单测，
 * 也保证保存与缓存裁剪的规则只有一份实现。
 *
 * 为什么要限体积：库条目里带一张缩略图 dataURL，而 localStorage 的配额是整个源共享的
 * （约 5MB）。这个仓库里现有的写入全是 `try { setItem } catch { /* 忽略 *\/ }`，写爆了
 * 只会静默丢掉；库要是把配额吃干，连带把布局、导出设置一起挤掉，那比丢一张缩略图糟得多。
 * 所以只裁剪可重建的缩略图；编辑记录存不下时报告失败，不淘汰其它模板。
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }

    root.NekoEmoteTemplateLibrary = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const STORAGE_KEY = 'neko-emote-maker-template-library';
    const SCHEMA_VERSION = 1;
    const MAX_THUMBNAILS = 12;
    // 96px 的 PNG dataURL 实测十几 KB；超过这个数就当没图，不占地方
    const MAX_THUMB_CHARS = 24 * 1024;
    // 整份 JSON 的软上限：给布局/导出设置留出余量
    const BUDGET_CHARS = 1.5 * 1024 * 1024;

    class TemplateLibraryError extends Error {
        constructor(message, code = 'template_library_error', details = null) {
            super(message);
            this.name = 'TemplateLibraryError';
            this.code = code;
            this.details = details;
        }
    }

    function text(value) {
        return String(value == null ? '' : value).trim();
    }

    /**
     * 副本占用要"真实显示"：数字来自后端 stat，这里只负责把它讲成人话。
     * 1 MB 以下用 KB（贴图补几张的量级），不到 1 KB 也别显示 0 KB。
     */
    function formatBytes(bytes) {
        const value = Number(bytes);
        if (!Number.isFinite(value) || value <= 0) return '';
        if (value < 1024) return '1 KB';
        if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
        const mb = value / (1024 * 1024);
        return `${mb >= 100 ? Math.round(mb) : Math.round(mb * 10) / 10} MB`;
    }

    /**
     * 同一个模板反复导入应当命中同一条目（否则库会被重复项塞满）。
     * Spine 使用后端计算的资源内容标识，与 ZIP 文件名无关。
     */
    function entryKeyFor(pkg) {
        if (!pkg) return '';
        if (pkg.source?.builtIn === true && /^builtin:[a-z0-9]+(?:[.-][a-z0-9]+)*$/.test(pkg.id || '')) return pkg.id;
        if (pkg.source?.kind === 'spine' && /^spine-[0-9a-f]{64}$/.test(pkg.id || '')) return pkg.id;
        return '';
    }

    function emptyStore() {
        return { schemaVersion: SCHEMA_VERSION, entries: [], currentKey: '' };
    }

    function normalizeSession(session) {
        const src = session && typeof session === 'object' ? session : {};
        return {
            sceneId: text(src.sceneId),
            groupId: text(src.groupId),
            groups: Array.isArray(src.groups) ? [...src.groups] : [],
            shots: Array.isArray(src.shots) ? [...src.shots] : [],
            packSceneIds: src.packSceneIds && typeof src.packSceneIds === 'object'
                ? { ...src.packSceneIds } : {},
            packName: text(src.packName),
            packFormat: src.packFormat === 'layered' ? 'layered' : 'flat',
            packPriority: String(src.packPriority || '').trim().toLowerCase() === 'emotion'
                ? 'emotion' : 'talking',
            character: src.character && typeof src.character === 'object' ? {
                skins: Array.isArray(src.character.skins) ? [...src.character.skins] : [],
                slots: Array.isArray(src.character.slots) ? [...src.character.slots] : [],
            } : { skins: [], slots: [] },
            adjustments: src.adjustments && typeof src.adjustments === 'object'
                ? { ...src.adjustments } : {},
            adjustOverwrite: src.adjustOverwrite !== false,
            colorMode: text(src.colorMode),
            secondaryColor: text(src.secondaryColor),
            // 表演台拖出来的大小/角度、以及"哪个状态演哪段"都活在节点图里，
            // 所以每个模板必须各自带一份，否则切回来设置就串到别的形象上了。
            nodeGraph: src.nodeGraph && typeof src.nodeGraph === 'object' ? src.nodeGraph : null,
        };
    }

    // 库卡片显示名与用户改名的长度上限：再长就被省略号吃掉了
    const MAX_LABEL_CHARS = 60;

    function makeEntry(input) {
        const src = input || {};
        const key = text(src.key) || entryKeyFor(src.pkg);
        if (!key) throw new TemplateLibraryError('拿不到模板标识，无法入库', 'no_key');
        const thumb = text(src.thumb);
        return {
            key,
            packageName: text(src.pkg?.source?.packageName) || text(src.packageName),
            displayName: text(src.pkg?.model?.displayName) || text(src.displayName),
            // sourceName 保留上传 ZIP 的显示名，alias 是用户改名后的结果。
            sourceName: text(src.sourceName).slice(0, MAX_LABEL_CHARS),
            alias: text(src.alias).slice(0, MAX_LABEL_CHARS),
            thumb: thumb.length <= MAX_THUMB_CHARS ? thumb : '',
            addedAt: Number(src.addedAt) || Date.now(),
            lastUsedAt: Number(src.lastUsedAt) || Date.now(),
            session: normalizeSession(src.session),
        };
    }

    /**
     * 卡片显示名的唯一裁决处：用户改的名 > 来源名 > 包名（去 .zip）> 模板自报名。
     */
    function entryLabel(entry) {
        if (!entry) return '';
        const pkg = text(entry.packageName).replace(/\.zip$/i, '');
        return text(entry.alias)
            || text(entry.sourceName)
            || pkg
            || text(entry.displayName);
    }

    function renameEntry(store, key, alias) {
        const next = { ...emptyStore(), ...store };
        const entry = find(next, key);
        if (!entry) return next;
        // 改成空 = 回到默认名；键不动 —— 改名只是外观，不是身份
        entry.alias = text(alias).slice(0, MAX_LABEL_CHARS);
        return next;
    }

    function findIndex(store, key) {
        return (store.entries || []).findIndex((entry) => entry.key === String(key));
    }

    function find(store, key) {
        const index = findIndex(store, key);
        return index >= 0 ? store.entries[index] : null;
    }

    /** 插入或原地更新；同 key 保留 addedAt，所有编辑记录都保留。 */
    function upsert(store, entry) {
        const next = { ...emptyStore(), ...store, entries: [...(store.entries || [])] };
        const index = findIndex(next, entry.key);
        if (index >= 0) {
            next.entries[index] = { ...next.entries[index], ...entry, addedAt: next.entries[index].addedAt || entry.addedAt };
        } else {
            next.entries.unshift(entry);
        }
        return next;
    }

    /** 旧缓存曾淘汰过卡片时，仍让磁盘上归用户所有的副本可以重开或删除。 */
    function restoreArchives(store, archives) {
        let next = store;
        for (const archive of archives || []) {
            const key = text(archive.key);
            if (!/^(?:spine-[0-9a-f]{64}|builtin:[a-z0-9]+(?:[.-][a-z0-9]+)*)$/.test(key)
                || find(next, key)) continue;
            next = upsert(next, makeEntry({ key, displayName: key,
                addedAt: archive.savedAt, lastUsedAt: archive.savedAt }));
        }
        return next;
    }

    function remove(store, key) {
        const next = { ...emptyStore(), ...store };
        next.entries = (next.entries || []).filter((entry) => entry.key !== String(key));
        if (next.currentKey === String(key)) next.currentKey = '';
        return next;
    }

    function touch(store, key) {
        const next = { ...emptyStore(), ...store };
        const entry = find(next, key);
        if (!entry) return next;
        entry.lastUsedAt = Date.now();
        next.currentKey = entry.key;
        return next;
    }

    function markCurrent(store, key) {
        const next = { ...emptyStore(), ...store };
        next.currentKey = String(key || '');
        return next;
    }

    /**
     * 只为最近的模板缓存缩略图，体积超预算时继续丢图，始终保留全部编辑记录。
     * 返回 null 表示去掉全部缩略图后仍存不下，不写入残缺的库。
     */
    function shrinkToFit(store, budget = BUDGET_CHARS) {
        const recent = new Set([...(store.entries || [])]
            .sort((a, b) => (b.lastUsedAt || 0) - (a.lastUsedAt || 0))
            .slice(0, MAX_THUMBNAILS).map((entry) => entry.key));
        let next = { ...emptyStore(), ...store, entries: (store.entries || [])
            .map((entry) => recent.has(entry.key) ? entry : ({ ...entry, thumb: '' })) };
        const size = (candidate) => JSON.stringify(candidate).length;
        if (size(next) <= budget) return next;
        // 缩略图是"好看"，不是"有用"：先牺牲它
        next = {
            ...next,
            entries: next.entries.map((entry) => entry.key === next.currentKey ? entry : ({ ...entry, thumb: '' })),
        };
        if (size(next) <= budget) return next;
        next = { ...next, entries: next.entries.map((entry) => ({ ...entry, thumb: '' })) };
        if (size(next) <= budget) return next;
        return null;
    }

    function serialize(store, budget = BUDGET_CHARS) {
        const shrunk = shrinkToFit(store, budget);
        if (!shrunk) throw new TemplateLibraryError('模板库超出可存空间', 'quota', { entries: store.entries.length });
        return JSON.stringify(shrunk);
    }

    function parse(raw) {
        if (!raw) return emptyStore();
        let data;
        try {
            data = JSON.parse(raw);
        } catch (_) {
            return emptyStore();
        }
        if (!data || !Array.isArray(data.entries)) return emptyStore();
        const entries = [];
        data.entries.forEach((entry) => {
            try {
                entries.push(makeEntry(entry));
            } catch (_) {
                // 认不出的条目丢掉，别让它把整库带崩
            }
        });
        return {
            schemaVersion: SCHEMA_VERSION,
            entries,
            currentKey: text(data.currentKey),
        };
    }

    function load(storage) {
        if (!storage || typeof storage.getItem !== 'function') return emptyStore();
        let raw = null;
        try {
            raw = storage.getItem(STORAGE_KEY);
        } catch (_) {
            return emptyStore();
        }
        return parse(raw);
    }

    function save(storage, store) {
        if (!storage || typeof storage.setItem !== 'function') {
            throw new TemplateLibraryError('没有可用的本地存储', 'no_storage');
        }
        storage.setItem(STORAGE_KEY, serialize(store));
        return true;
    }

    /**
     * 老数据迁移：以前只有 `neko-emote-maker-last-template` 这一份会话快照。
     * 库里没有条目时把它读成第一条，用户升级后不会看到"库是空的"。
     */
    function migrateFromLegacy(legacy, storage) {
        if (!legacy || typeof legacy !== 'object' || !legacy.fingerprint) return null;
        const store = load(storage);
        if (find(store, legacy.fingerprint)) return null;
        const next = upsert(store, makeEntry({
            key: legacy.fingerprint,
            packageName: legacy.packageName,
            displayName: legacy.displayName,
            session: {
                ...legacy,
                nodeGraph: null,
            },
        }));
        return markCurrent(touch(next, legacy.fingerprint), legacy.fingerprint);
    }

    return Object.freeze({
        STORAGE_KEY,
        SCHEMA_VERSION,
        MAX_THUMBNAILS,
        MAX_THUMB_CHARS,
        BUDGET_CHARS,
        MAX_LABEL_CHARS,
        TemplateLibraryError,
        entryKeyFor,
        entryLabel,
        renameEntry,
        formatBytes,
        emptyStore,
        makeEntry,
        normalizeSession,
        find,
        upsert,
        restoreArchives,
        remove,
        touch,
        markCurrent,
        shrinkToFit,
        serialize,
        parse,
        load,
        save,
        migrateFromLegacy,
    });
}));
