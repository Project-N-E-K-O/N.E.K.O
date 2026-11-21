/*
 * 组（分组导出用的那层）与「存下来的取景」的数据规则。
 *
 * 和上一轮的模板库一样：只做数据，不碰 DOM、不读文件，规则能在 Node 里单测。
 *
 * 两类成员要分清，不然导出时会打架：
 *   - scene：模板包里本来就有的场景（只读，我们只是挑中它）
 *   - shot ：我们在编辑器里调好之后"存下来的取景"（引用一个来源场景 + 一份外观快照）
 * 组里两者混着放，导出按顺序出文件。
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }

    root.NekoEmoteGroupLibrary = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    // 选不了目录时，成品只能攒在内存里再一次性 zip。这道栏只护那条退路。
    // 选了目录之后逐张落盘，上限改用 MAX_ITEMS_PER_GROUP。
    const MAX_BATCH = 150;
    const MAX_GROUP_NAME = 40;
    const MAX_ITEMS_PER_GROUP = 400;
    const IMPORTED_PREFIX = 'list:';

    function text(value) {
        return String(value == null ? '' : value).trim();
    }

    let idSeed = 0;
    function nextId(prefix) {
        idSeed += 1;
        return `${prefix}${Date.now().toString(36)}-${idSeed.toString(36)}`;
    }

    /** 将从模板动作生成的组设为只读。 */
    function importedAnimationGroup(scenes, name) {
        const sceneIds = scenes.map((scene) => scene?.id).filter(Boolean);
        if (!sceneIds.length) return null;
        return {
            id: `${IMPORTED_PREFIX}spine:animations`,
            name,
            count: sceneIds.length,
            readOnly: true,
            items: sceneIds.map((sceneId) => ({ kind: 'scene', ref: sceneId })),
        };
    }

    /** 组记下的那一套换装/位移。之后加进来的每一条都用它预览和导出，不跟屏幕上后来的滑杆。 */
    function normalizeLook(look) {
        const transform = look?.transform && typeof look.transform === 'object' ? look.transform : {};
        return {
            skins: Array.isArray(look?.skins) ? look.skins.map((item) => ({ ...item })) : [],
            slots: Array.isArray(look?.slots) ? look.slots.map((item) => ({ ...item })) : [],
            transform: {
                x: Number(transform.x) || 0,
                y: Number(transform.y) || 0,
                rotation: Number(transform.rotation) || 0,
                scale: Number(transform.scale) || 1,
            },
        };
    }

    function makeGroup(input) {
        const src = input || {};
        const items = (Array.isArray(src.items) ? src.items : [])
            .map((item) => {
                const kind = item?.kind === 'shot' ? 'shot' : 'scene';
                const ref = text(item?.ref);
                return ref ? { kind, ref } : null;
            })
            .filter(Boolean)
            .slice(0, MAX_ITEMS_PER_GROUP);
        return {
            id: text(src.id) || nextId('group:'),
            name: (text(src.name) || '未命名组').slice(0, MAX_GROUP_NAME),
            readOnly: false,
            items,
            // 没有 look 的旧组保持 null，调用方再回退到第一条取景，别冻成一份全 0
            look: src.look && typeof src.look === 'object' ? normalizeLook(src.look) : null,
            createdAt: Number(src.createdAt) || Date.now(),
        };
    }

    function hasItem(group, kind, ref) {
        return (group.items || []).some((item) => item.kind === kind && item.ref === String(ref));
    }

    function addItem(group, kind, ref) {
        if (!group || group.readOnly) return group;
        const next = { ...group, items: [...(group.items || [])] };
        if (hasItem(next, kind, ref)) return next;
        if (next.items.length >= MAX_ITEMS_PER_GROUP) return next;
        next.items.push({ kind, ref: String(ref) });
        return next;
    }

    function removeItem(group, kind, ref) {
        if (!group || group.readOnly) return group;
        return { ...group, items: (group.items || []).filter((item) => !(item.kind === kind && item.ref === String(ref))) };
    }

    /**
     * 删掉自建组，并清掉只被这一组引用的取景。
     * 别的组还指着的取景留着。只读的导入组删不掉，原样退回。
     */
    function deleteGroup(groups, shots, id) {
        const list = Array.isArray(groups) ? groups : [];
        const target = list.find((group) => group && group.id === id);
        if (!target || target.readOnly) return { groups: list, shots: Array.isArray(shots) ? shots : [] };
        const nextGroups = list.filter((group) => group && group.id !== id);
        const stillUsed = new Set();
        nextGroups.forEach((group) => {
            (group.items || []).forEach((item) => {
                if (item && item.kind === 'shot') stillUsed.add(String(item.ref));
            });
        });
        const dropped = new Set(
            (target.items || [])
                .filter((item) => item && item.kind === 'shot')
                .map((item) => String(item.ref)),
        );
        const nextShots = (Array.isArray(shots) ? shots : []).filter((shot) => {
            const shotId = String(shot && shot.id || '');
            return !dropped.has(shotId) || stillUsed.has(shotId);
        });
        return { groups: nextGroups, shots: nextShots };
    }

    function renameGroup(groups, id, name) {
        return (groups || []).map((group) => (group.id === id && !group.readOnly
            ? { ...group, name: (text(name) || group.name).slice(0, MAX_GROUP_NAME) }
            : group));
    }

    /** 去重命名：`新组`、`新组 2`、`新组 3`… 只跟已有名字比，大小写视为同一个。 */
    function uniqueGroupName(groups, base) {
        const taken = new Set((groups || []).map((group) => text(group.name).toLocaleLowerCase()));
        const root = (text(base) || '未命名组').slice(0, MAX_GROUP_NAME);
        if (!taken.has(root.toLocaleLowerCase())) return root;
        for (let n = 2; n < 1000; n += 1) {
            const suffix = ` ${n}`;
            const candidate = `${root.slice(0, MAX_GROUP_NAME - suffix.length)}${suffix}`;
            if (!taken.has(candidate.toLocaleLowerCase())) return candidate;
        }
        return `${root}-${Date.now()}`;
    }

    /** 一条"存下来的取景"：引用来源场景 + 当时的外观快照。 */
    function makeShot(input) {
        const src = input || {};
        // 取景的形状必须和 currentLookDefaults() / applyLookToState() 用的一致：transform 住在
        // look 下面。放在顶层读会得到空对象，于是大小、角度、位移全都被静默丢成默认值。
        return {
            id: text(src.id) || nextId('shot:'),
            name: (text(src.name) || '取景').slice(0, MAX_GROUP_NAME),
            sourceSceneId: text(src.sourceSceneId),
            progress: Math.max(0, Math.min(100, Number(src.progress) || 0)),
            look: normalizeLook(src.look),
            createdAt: Number(src.createdAt) || Date.now(),
        };
    }

    function shotsFrom(list) {
        return (Array.isArray(list) ? list : [])
            .filter((shot) => shot && typeof shot === 'object')
            .map((shot) => makeShot(shot));
    }

    function groupsFrom(list) {
        return (Array.isArray(list) ? list : [])
            .filter((group) => group && typeof group === 'object' && !String(group.id || '').startsWith(IMPORTED_PREFIX))
            .map((group) => makeGroup(group));
    }

    // Windows 文件名里 \\ / : * ? " < > | 和控制字符都不能要；结尾的点号/空格也会被悄悄剥掉。
    // 只按"能不能写"来清洗，不做 transliterate —— 中文文件名在这台机器上本来就正常。
    function sanitizeFileName(value, fallback = 'emote') {
        const cleaned = text(value)
            // eslint-disable-next-line no-control-regex
            .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_')
            .replace(/[.\s_]+$/, '')
            .replace(/^[.\s_]+/, '')
            .slice(0, 80);
        return cleaned || fallback;
    }

    /**
     * 出文件名：序号前缀保证导出后仍是组内的顺序（文件名排序＝肉眼顺序），
     * 同名不覆盖。序号位数按总数走，9 张以内用 `01_`，超过 99 张用 `001_`。
     */
    function planFileNames(items) {
        const width = String(Math.max(items.length, 1)).length < 2 ? 2 : String(items.length).length;
        const taken = new Map();
        return items.map((item, index) => {
            const ordinal = String(index + 1).padStart(width, '0');
            const base = sanitizeFileName(item.name, item.kind === 'shot' ? 'shot' : 'scene');
            let file = `${ordinal}_${base}.${item.ext || 'gif'}`;
            const seen = taken.get(file.toLocaleLowerCase()) || 0;
            if (seen) {
                taken.set(file.toLocaleLowerCase(), seen + 1);
                const suffix = seen + 1;
                file = `${ordinal}_${base}-${suffix}.${item.ext || 'gif'}`;
                taken.set(file.toLocaleLowerCase(), (taken.get(file.toLocaleLowerCase()) || 0) + 1);
            } else {
                taken.set(file.toLocaleLowerCase(), 1);
            }
            return { ...item, file, index };
        });
    }

    /**
     * 导出计划：把组里的成员摊平成"要出几张、每张是谁、叫什么文件名"。
     * shot 要能烘焙必须有 sourceSceneId（来源场景被删掉/换包之后就没了），这种条目
     * 单独报告，不能静默少几张 —— 用户以为导出了 20 张实际只有 18 张最难查。
     *
     * 场景成员存的是 id 引用，名字得回查（`nameOf`）：不然导出「模板自带」的整份列表时，
     * 文件名会是 `01_ModelA#0.gif` 这种没人认得出的东西。
     */
    function planGroupExport({ group, shots, ext = 'gif', limit = MAX_BATCH, nameOf } = {}) {
        const items = [];
        const skipped = [];
        const shotById = new Map((Array.isArray(shots) ? shots : []).map((shot) => [shot.id, shot]));
        (group?.items || []).forEach((item) => {
            if (item.kind === 'scene') {
                const label = typeof nameOf === 'function' ? text(nameOf(item.ref)) : '';
                items.push({ kind: 'scene', ref: item.ref, name: item.name || label || item.ref, ext });
                return;
            }
            const shot = shotById.get(item.ref);
            if (!shot) {
                skipped.push({ kind: 'shot', ref: item.ref, reason: 'missing-shot', name: item.ref });
                return;
            }
            if (!shot.sourceSceneId) {
                skipped.push({ kind: 'shot', ref: shot.id, reason: 'no-source', name: shot.name });
                return;
            }
            items.push({ kind: 'shot', ref: shot.id, name: shot.name, ext });
        });
        const truncated = items.length > limit;
        return {
            items: planFileNames(truncated ? items.slice(0, limit) : items),
            skipped,
            total: items.length,
            limit,
            truncated,
            ready: (truncated ? items.slice(0, limit) : items).length > 0,
        };
    }

    /**
     * 导出引擎：把"计划里每一项"变成一个文件。
     *
     * produce / zip / store 都是注入进来的。没有目录可写时，zip 拿到全部字节再打包。
     * 有 store 时每张写完就丢掉字节，最后把文件名交给 zipStored，由页面从磁盘逐张读出来压缩。
     */
    async function runGroupExport(options) {
        const {
            plan, produce, zip, store, zipStored, onProgress, shouldAbort,
        } = options || {};
        const items = (plan && Array.isArray(plan.items)) ? plan.items : [];
        const canStore = typeof store === 'function';
        if (typeof produce !== 'function' || (typeof zip !== 'function' && !canStore)) {
            throw new Error('导出引擎缺少 produce / zip');
        }
        const files = [];
        const stored = [];
        const failed = [];

        async function packStored() {
            if (!stored.length || typeof zipStored !== 'function') return undefined;
            return zipStored(stored.slice());
        }

        async function aborted(done) {
            const blob = await packStored();
            return {
                aborted: true, files, failed, stored, blob,
                savedToFolder: stored.length > 0 && typeof zipStored === 'function',
                done, total: items.length,
            };
        }

        for (let index = 0; index < items.length; index += 1) {
            if (typeof shouldAbort === 'function' && shouldAbort()) {
                return aborted(index);
            }
            const item = items[index];
            try {
                const bytes = await produce(item);
                if (!bytes || !bytes.length) {
                    failed.push({ file: item.file, reason: 'empty' });
                } else if (canStore) {
                    await store({ path: item.file, data: bytes });
                    stored.push(item.file);
                } else {
                    files.push({ path: item.file, data: bytes });
                }
            } catch (error) {
                failed.push({ file: item.file, reason: (error && error.message) || 'produce-failed' });
            }
            if (typeof onProgress === 'function') {
                onProgress({ done: index + 1, total: items.length, file: item.file });
            }
        }
        if (typeof shouldAbort === 'function' && shouldAbort()) return aborted(items.length);
        if (canStore) {
            const blob = await packStored();
            return {
                aborted: false, files, failed, stored, blob,
                savedToFolder: stored.length > 0,
                done: items.length, total: items.length,
            };
        }
        if (!files.length) return { aborted: false, files, failed, done: items.length, total: items.length };
        const blob = await zip(files);
        return { aborted: false, files, failed, blob, done: items.length, total: items.length };
    }

    return Object.freeze({
        MAX_BATCH,
        MAX_GROUP_NAME,
        MAX_ITEMS_PER_GROUP,
        IMPORTED_PREFIX,
        importedAnimationGroup,
        makeGroup,
        makeShot,
        groupsFrom,
        shotsFrom,
        addItem,
        removeItem,
        hasItem,
        renameGroup,
        deleteGroup,
        uniqueGroupName,
        sanitizeFileName,
        planFileNames,
        planGroupExport,
        runGroupExport,
    });
}));
