/*
 * 模板库：把"上次打开的那一个模板"扩成"最近用过的几个模板"。
 *
 * 只做数据，不碰 DOM、不读文件、不 import 任何页面脚本 —— 这样它能在 Node 里直接单测，
 * 也保证"存什么/淘汰什么"的规则只有一份实现。
 *
 * 为什么要限体积：库条目里带一张缩略图 dataURL，而 localStorage 的配额是整个源共享的
 * （约 5MB）。这个仓库里现有的写入全是 `try { setItem } catch { /* 忽略 *\/ }`，写爆了
 * 只会静默丢掉；库要是把配额吃干，连带把布局、导出设置一起挤掉，那比丢一张缩略图糟得多。
 * 所以这里主动限量，并且**先丢缩略图、再丢最久没用的条目**。
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
    const MAX_ENTRIES = 12;
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

    /** 插入或原地更新；同 key 保留 addedAt，越界的最久没用的先走。 */
    function upsert(store, entry) {
        const next = { ...emptyStore(), ...store };
        const index = findIndex(next, entry.key);
        if (index >= 0) {
            next.entries[index] = { ...next.entries[index], ...entry, addedAt: next.entries[index].addedAt || entry.addedAt };
        } else {
            next.entries.unshift(entry);
        }
        while (next.entries.length > MAX_ENTRIES) {
            // 按"最久没碰"淘汰，而不是按加入顺序 —— 常用的那条不该被新来的挤掉
            let oldest = 0;
            next.entries.forEach((candidate, i) => {
                if ((candidate.lastUsedAt || 0) < (next.entries[oldest].lastUsedAt || 0)) oldest = i;
            });
            next.entries.splice(oldest, 1);
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
     * 体积超预算时的降级顺序：先扔缩略图（能重画），再按最久没用扔整条。
     * 返回 null 表示连这个都救不回来 —— 调用方应当只保留当前这一条。
     */
    function shrinkToFit(store, budget = BUDGET_CHARS) {
        let next = { ...emptyStore(), ...store };
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
        next = { ...next, entries: [...next.entries] };
        while (next.entries.length > 1 && size(next) > budget) {
            let oldest = -1;
            next.entries.forEach((entry, index) => {
                if (entry.key === next.currentKey) return;
                if (oldest < 0 || (entry.lastUsedAt || 0) < (next.entries[oldest].lastUsedAt || 0)) {
                    oldest = index;
                }
            });
            if (oldest < 0) break;
            next.entries.splice(oldest, 1);
        }
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
        MAX_ENTRIES,
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
