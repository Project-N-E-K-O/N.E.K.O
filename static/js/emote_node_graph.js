/*
 * Node-graph model for the Emote maker's state editor.
 *
 * Deliberately free of DOM, canvas and Spine: this file only knows what a node is,
 * which sockets it exposes, how a graph is validated, and how a graph is folded into
 * concrete per-state bake parameters. Keeping it pure means the vocabulary — the part
 * most likely to be wrong — is unit-testable, and the editor UI stays a thin skin.
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }

    root.NekoEmoteNodeGraph = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const LOOK = 'look';

    // Keep retired modifiers readable so old saves retain their source-to-output links.
    const legacyModifier = {
        category: 'modify',
        inputs: [{ id: LOOK, kind: LOOK }],
        outputs: [{ id: LOOK, kind: LOOK }],
        params: [],
    };
    const TYPES = {
        'mod.slider': legacyModifier,
        'mod.text': legacyModifier,
        'source.default': {
            category: 'source',
            inputs: [],
            outputs: [{ id: LOOK, kind: LOOK }],
            params: [],
        },
        'source.scene': {
            category: 'source',
            inputs: [],
            outputs: [{ id: LOOK, kind: LOOK }],
            params: [{ id: 'sceneId', widget: 'scene', default: '' }],
        },
        // 用户图片：整格内容就是这些图（按序轮播的静态源），没有骨骼/皮肤可言。
        // imageIds 指向表演台里导入的用户图片，不在场景清单里；imageId 保留单图
        // 时代的键，normalize 后永远同步为列表第一项，老图照常能读。
        'source.image': {
            category: 'source',
            inputs: [],
            outputs: [{ id: LOOK, kind: LOOK }],
            params: [
                { id: 'imageId', widget: 'text', default: '' },
                { id: 'imageIds', widget: 'images', default: [] },
            ],
        },
        'mod.skin': {
            category: 'modify',
            inputs: [{ id: LOOK, kind: LOOK }],
            outputs: [{ id: LOOK, kind: LOOK }],
            params: [{ id: 'groups', widget: 'skins', default: [] }],
        },
        'mod.slotTint': {
            category: 'modify',
            inputs: [{ id: LOOK, kind: LOOK }],
            outputs: [{ id: LOOK, kind: LOOK }],
            params: [
                { id: 'tag', widget: 'slotGroup', default: '' },
                { id: 'color', widget: 'color', default: '#ffffff' },
                { id: 'secondary', widget: 'color', default: '#000000' },
            ],
        },
        'mod.transform': {
            category: 'modify',
            inputs: [{ id: LOOK, kind: LOOK }],
            outputs: [{ id: LOOK, kind: LOOK }],
            params: [
                { id: 'x', widget: 'number', min: -500, max: 500, step: 1, default: 0 },
                { id: 'y', widget: 'number', min: -500, max: 500, step: 1, default: 0 },
                { id: 'scale', widget: 'number', min: 0.2, max: 3, step: 0.01, default: 1 },
                { id: 'mirror', widget: 'checkbox', default: false },
            ],
        },
        'output.state': {
            category: 'output',
            inputs: [{ id: LOOK, kind: LOOK }],
            outputs: [],
            params: [
                { id: 'stateKey', widget: 'state', default: 'idle_image' },
                // 起止用「秒」而不是帧号：真包动画动辄上百帧，"第 37 帧"对使用者没有
                // 意义，而时长可以从包里查到。seconds = 0 表示演完整段。
                { id: 'startAt', widget: 'number', min: 0, max: 30, step: 0.05, default: 0 },
                { id: 'seconds', widget: 'number', min: 0, max: 30, step: 0.05, default: 0 },
                // 大小/角度只有分层模式会真正生效（平铺 GIF 没有层），UI 要按格式提示
                { id: 'scalePercent', widget: 'number', min: 10, max: 500, step: 5, default: 100 },
                { id: 'degrees', widget: 'number', min: -180, max: 180, step: 1, default: 0 },
            ],
        },
    };

    /**
     * 往上游找一个指定类别的源头（通常是 source）。
     * 只查直接上游是不够的：旧存档的来源和输出之间可能还有外观节点，
     * 而且插入顺序决定直接上游是谁，界面会因此说谎。
     */
    function findSource(graph, nodeId, category) {
        let current = nodeId;
        const seen = new Set();
        while (current && !seen.has(current)) {
            seen.add(current);
            const link = graph.links.find((entry) => entry.to === current);
            if (!link) return null;
            const node = graph.nodes.find((entry) => entry.id === link.from);
            if (!node) return null;
            const type = typeOf(node);
            if (!type) return null;
            if (type.category === (category || 'source')) return node;
            current = node.id;
        }
        return null;
    }

    /**
     * 决定哪些输出真的该进包。三条规则都对应过一次真实故障：
     *  · 没接动画的槽位不产出 —— 否则"跟随默认外观"会被烘成当前场景的图，
     *    用户只是点过去看一眼，模型里就多了一张他从未选过的动作；
     *  · 同一个槽位只留一条输出 —— 分层装配遇到重复键会直接失败；
     *  · 包里没有的槽位丢掉 —— 平铺装配遇到未知状态键同样抛错。
     * 必需槽（待机）例外：它跟随默认外观是合法且基本模式也这么做。
     */
    function planOutputs(graph, outputs, knownStates, requiredStates) {
        const known = new Set(Array.isArray(knownStates) ? knownStates : []);
        const required = new Set(Array.isArray(requiredStates) ? requiredStates : []);
        const kept = [];
        const dropped = [];
        const taken = new Set();
        (Array.isArray(outputs) ? outputs : []).forEach((output) => {
            const key = output && output.stateKey;
            if (!known.has(key)) {
                dropped.push({ output, reason: 'unknown-state' });
                return;
            }
            if (taken.has(key)) {
                dropped.push({ output, reason: 'duplicate' });
                return;
            }
            const source = findSource(graph, output.nodeId, 'source');
            // 绑了用户图片的格子同样是"用户真的设置过"：图片状态没有场景，
            // 只认 source.scene 会把它当"未选动作"丢掉。
            const assigned = Boolean(source && (
                (source.type === 'source.scene' && String(source.params.sceneId || ''))
                || (source.type === 'source.image' && String(source.params.imageId || ''))));
            if (!assigned && !required.has(key)) {
                dropped.push({ output, reason: 'unassigned' });
                return;
            }
            taken.add(key);
            kept.push(output);
        });
        return { kept, dropped };
    }

    function typeOf(node) {
        return node && Object.hasOwn(TYPES, node.type) ? TYPES[node.type] : null;
    }

    function normalizeColor(value, fallback) {
        const text = String(value || '').trim();
        return /^#[0-9a-fA-F]{6}$/.test(text) ? text.toLowerCase() : fallback;
    }

    function normalizeNumber(value, min, max, fallback) {
        const number = Number(value);
        if (!Number.isFinite(number)) return fallback;
        return Math.min(max, Math.max(min, number));
    }

    // Params are untrusted once they have been through localStorage or a JSON import,
    // so every widget type gets an explicit coercion here rather than being passed through.
    function coerceParam(spec, value) {
        switch (spec.widget) {
            case 'number':
                return normalizeNumber(value, spec.min, spec.max, spec.default);
            case 'color':
                return normalizeColor(value, spec.default);
            case 'checkbox':
                return value === true;
            case 'text':
                return String(value === undefined || value === null ? '' : value).slice(0, 120);
            case 'images':
                return Array.isArray(value)
                    ? [...new Set(value
                        .map((item) => String(item == null ? '' : item).trim())
                        .filter(Boolean))]
                    : [];
            case 'skins':
                return Array.isArray(value)
                    ? value
                        .filter((item) => item && typeof item.group === 'string')
                        .map((item) => ({ group: item.group, value: String(item.value == null ? '' : item.value) }))
                    : [];
            case 'scene':
            case 'slotGroup':
            case 'state':
                // 这些是指向包内资源的引用名。数字/对象硬转成字符串会造出一个"看起来
                // 合法、其实查无此项"的名字，所以只接受字符串。
                return typeof value === 'string' ? value.slice(0, 300) : spec.default;
            default:
                return spec.default;
        }
    }

    function normalizeParams(type, params) {
        const definition = typeOf({ type });
        const source = params && typeof params === 'object' ? params : {};
        const out = {};
        if (!definition) return out;
        definition.params.forEach((spec) => {
            out[spec.id] = coerceParam(spec, source[spec.id]);
        });
        if (type === 'source.image') {
            // 单图时代只有 imageId；列表时代两者同步：列表为空时从单值捞回，
            // 列表非空时单值永远等于第一项 —— 老的读取方不用改也能工作。
            if (!out.imageIds.length && out.imageId) out.imageIds = [out.imageId];
            if (out.imageIds.length) out.imageId = out.imageIds[0];
            else out.imageId = '';
        }
        return out;
    }

    function makeNode(type, x, y, params, id) {
        if (!Object.hasOwn(TYPES, type)) return null;
        return {
            id: id || `${type}-${Math.random().toString(36).slice(2, 9)}`,
            type,
            x: Number.isFinite(Number(x)) ? Number(x) : 0,
            y: Number.isFinite(Number(y)) ? Number(y) : 0,
            params: normalizeParams(type, params),
        };
    }

    function emptyGraph() {
        return { schemaVersion: 1, nodes: [], links: [] };
    }

    function isValid(graph) {
        return Boolean(graph)
            && typeof graph === 'object'
            && Array.isArray(graph.nodes)
            && Array.isArray(graph.links);
    }

    // Rebuilds a graph from anything (storage, JSON import, paste). Unknown node types are
    // dropped rather than guessed at, and any link touching a dropped node goes with it —
    // a half-loaded graph that references a type we no longer have would crash the editor.
    function normalizeGraph(input) {
        if (!isValid(input)) return emptyGraph();
        const nodes = [];
        const dropped = [];
        input.nodes.forEach((node, index) => {
            if (!node || !Object.hasOwn(TYPES, node.type)) {
                dropped.push(node && node.id);
                return;
            }
            const normalized = makeNode(
                node.type,
                node.x,
                node.y,
                node.params,
                String(node.id || `${node.type}-${index}`),
            );
            if (nodes.some((existing) => existing.id === normalized.id)) {
                normalized.id = `${normalized.type}-${index}-${Math.random().toString(36).slice(2, 6)}`;
            }
            nodes.push(normalized);
        });
        const byId = new Set(nodes.map((node) => node.id));
        const links = [];
        input.links.forEach((link) => {
            if (!link || !byId.has(link.from) || !byId.has(link.to)) return;
            const from = nodes.find((node) => node.id === link.from);
            const to = nodes.find((node) => node.id === link.to);
            if (!canConnect(from, link.fromSocket, to, link.toSocket)) return;
            if (links.some((existing) => (
                existing.to === link.to && existing.toSocket === link.toSocket
            ))) {
                return; // an input socket holds one link; first one wins deterministically
            }
            links.push({
                from: link.from,
                fromSocket: link.fromSocket || LOOK,
                to: link.to,
                toSocket: link.toSocket || LOOK,
            });
        });
        // 同一个槽位留两条输出，界面和装配就会各说各话：界面读到第一条
        // （往往是"跟随默认外观"），烘包却把两条都烘了、后写的盖掉先写的。
        const collapsed = collapseDuplicateOutputs(nodes, links);
        return { schemaVersion: 1, nodes: collapsed.nodes, links: collapsed.links, dropped: dropped.concat(collapsed.dropped) };
    }

    /**
     * 一个槽位只留一条输出，优先保留"确实接了动画"的那条：
     * 用户挑过的动作不能被一条点选时顺手生成的默认外观节点盖掉。
     */
    function collapseDuplicateOutputs(nodes, links) {
        const byState = new Map();
        nodes.forEach((node) => {
            const type = typeOf(node);
            if (!type || type.category !== 'output') return;
            const key = node.params && node.params.stateKey;
            if (!key) return;
            if (!byState.has(key)) byState.set(key, []);
            byState.get(key).push(node);
        });
        const losers = new Set();
        byState.forEach((group) => {
            if (group.length < 2) return;
            let best = group[0];
            let bestScore = outputSourceScore(group[0], nodes, links);
            group.slice(1).forEach((candidate) => {
                const score = outputSourceScore(candidate, nodes, links);
                if (score > bestScore) {
                    best = candidate;
                    bestScore = score;
                }
            });
            group.forEach((node) => { if (node !== best) losers.add(node.id); });
        });
        if (!losers.size) return { nodes, links, dropped: [] };
        return {
            nodes: nodes.filter((node) => !losers.has(node.id)),
            links: links.filter((link) => !losers.has(link.from) && !losers.has(link.to)),
            dropped: [...losers],
        };
    }

    // 3 = 上游接了具体动画，2 = 上游是别的源头，0 = 没接线（跟随默认外观）
    function outputSourceScore(node, nodes, links) {
        let current = node.id;
        const seen = new Set([current]);
        let best = 0;
        while (true) {
            const link = links.find((entry) => entry.to === current);
            if (!link) return best;
            const source = nodes.find((entry) => entry.id === link.from);
            if (!source || seen.has(source.id)) return best;
            seen.add(source.id);
            const type = typeOf(source);
            if (type && type.category === 'source') {
                if (source.type === 'source.scene' && String(source.params.sceneId || '')) return 3;
                if (source.type === 'source.image' && String(source.params.imageId || '')) return 3;
                best = Math.max(best, 2);
                return best;
            }
            best = Math.max(best, 1);
            current = source.id;
        }
    }

    function canConnect(fromNode, fromSocket, toNode, toSocket) {
        const fromType = typeOf(fromNode);
        const toType = typeOf(toNode);
        if (!fromType || !toType) return false;
        if (fromNode.id === toNode.id) return false;
        const out = (fromType.outputs || []).some((s) => s.id === (fromSocket || LOOK));
        const inc = (toType.inputs || []).some((s) => s.id === (toSocket || LOOK));
        return out && inc;
    }

    // Longest-path ordering with cycle rejection: a cycle would make evaluation loop, and
    // the editor lets users drag links freely, so cycles are reachable through the UI.
    function evaluateOrder(graph) {
        const byId = new Map(graph.nodes.map((node) => [node.id, node]));
        const incoming = new Map(graph.nodes.map((node) => [node.id, []]));
        graph.links.forEach((link) => {
            if (incoming.has(link.to)) incoming.get(link.to).push(link.from);
        });
        const order = [];
        const state = new Map(); // 0 unvisited, 1 visiting, 2 done
        const visiting = new Set();

        const visit = (id, stack) => {
            const mark = state.get(id) || 0;
            if (mark === 2) return true;
            if (mark === 1) return false; // back edge -> cycle
            state.set(id, 1);
            for (const parent of incoming.get(id) || []) {
                if (!byId.has(parent)) continue;
                if (!visit(parent, stack.concat(id))) {
                    state.set(id, 0);
                    return false;
                }
            }
            state.set(id, 2);
            order.push(id);
            return true;
        };

        for (const node of graph.nodes) {
            if (!visit(node.id, [])) return { order: [], cycle: true };
        }
        return { order, cycle: false, visiting };
    }

    function applyNode(node, look) {
        const params = node.params || {};
        const next = {
            sceneId: look.sceneId,
            imageId: look.imageId || '',
            imageIds: (look.imageIds || []).slice(),
            skins: look.skins.slice(),
            slots: look.slots.map((slot) => ({ ...slot })),
            transform: { ...look.transform },
        };
        switch (node.type) {
            case 'source.default':
                // 起点就是编辑器当前状态：这就是"默认节点"。
                break;
            case 'source.scene':
                if (params.sceneId) next.sceneId = params.sceneId;
                break;
            case 'source.image': {
                // 图片是整格内容的替换，不继承场景的外观。
                // 列表即轮播顺序；空列表但单值还在（旧图）也认。
                const ids = Array.isArray(params.imageIds) && params.imageIds.length
                    ? params.imageIds
                    : (params.imageId ? [params.imageId] : []);
                if (ids.length) {
                    next.sceneId = '';
                    next.imageId = ids[0];
                    next.imageIds = ids.slice();
                }
                break;
            }
            case 'mod.skin':
                (params.groups || []).forEach((group) => {
                    const index = next.skins.findIndex((item) => item.group === group.group);
                    if (index >= 0) next.skins[index] = { ...group };
                    else next.skins.push({ ...group });
                });
                break;
            case 'mod.slotTint': {
                const tag = String(params.tag || '');
                if (tag) {
                    next.slots = next.slots.map((slot) => (
                        String(slot.name || '').includes(tag)
                            ? {
                                ...slot,
                                tintColor: hexToRgba(params.color, slot.tintColor),
                                tintBlackColor: hexToRgba(params.secondary, slot.tintBlackColor),
                            }
                            : { ...slot }
                    ));
                }
                break;
            }
            case 'mod.transform':
                next.transform = {
                    ...next.transform,
                    x: normalizeNumber(next.transform.x + params.x, -2000, 2000, 0),
                    y: normalizeNumber(next.transform.y + params.y, -2000, 2000, 0),
                    scale: normalizeNumber(next.transform.scale * params.scale, 0.05, 8, 1),
                    mirror: Boolean(next.transform.mirror) !== Boolean(params.mirror),
                };
                break;
            default:
                break;
        }
        return next;
    }

    function hexToRgba(hex, fallback) {
        const text = normalizeColor(hex, '');
        if (!text) return fallback ? { ...fallback } : null;
        return {
            r: parseInt(text.slice(1, 3), 16) / 255,
            g: parseInt(text.slice(3, 5), 16) / 255,
            b: parseInt(text.slice(5, 7), 16) / 255,
            a: 1,
        };
    }

    // Folds the graph into bake targets: one entry per output node, carrying the look that
    // reached it. Anything the existing bake pipeline already understands — nothing invented.
    function evaluate(graph, defaults) {
        const base = {
            sceneId: (defaults && defaults.sceneId) || '',
            imageId: '',
            imageIds: [],
            skins: ((defaults && defaults.skins) || []).map((item) => ({ ...item })),
            slots: ((defaults && defaults.slots) || []).map((item) => ({ ...item })),
            transform: {
                x: 0, y: 0, scale: 1, mirror: false,
                ...((defaults && defaults.transform) || {}),
            },
        };
        const normalized = normalizeGraph(graph);
        const { order, cycle } = evaluateOrder(normalized);
        if (cycle) {
            return { outputs: [], warnings: ['cycle'], cycle: true, nodes: normalized.nodes };
        }
        const byId = new Map(normalized.nodes.map((node) => [node.id, node]));
        const looks = new Map();
        const outputs = [];
        const warnings = [];
        order.forEach((id) => {
            const node = byId.get(id);
            if (!node) return;
            const definition = typeOf(node);
            const inputLink = normalized.links.find((link) => link.to === node.id);
            const upstream = inputLink ? looks.get(inputLink.from) : null;
            const start = upstream || base;
            const result = applyNode(node, start);
            looks.set(id, result);
            if (definition.category === 'output') {
                const sceneId = result.sceneId;
                const imageId = result.imageId || '';
                // 图片状态没有场景：不缺场景，别报 no-scene
                if (!sceneId && !imageId) warnings.push('no-scene:' + id);
                const params = node.params || {};
                outputs.push({
                    nodeId: id,
                    stateKey: params.stateKey || 'idle_image',
                    startAt: Math.max(0, Number(params.startAt) || 0),
                    seconds: Math.max(0, Number(params.seconds) || 0),
                    scale: Math.min(5, Math.max(0.1, (Number(params.scalePercent) || 100) / 100)),
                    rotation: (Math.min(180, Math.max(-180, Number(params.degrees) || 0)) * Math.PI) / 180,
                    look: result,
                    // 图片状态：内容是这些图而不是场景动画（sceneId 为空）
                    imageId: result.imageId || '',
                    imageIds: (result.imageIds || []).slice(),
                });
            }
        });
        return { outputs, warnings, cycle: false, nodes: normalized.nodes };
    }

    function unlinkInput(graph, to, toSocket) {
        return {
            ...graph,
            links: graph.links.filter((existing) => !(
                existing.to === to && (existing.toSocket || LOOK) === (toSocket || LOOK)
            )),
        };
    }

    function removeNode(graph, id) {
        return {
            schemaVersion: 1,
            nodes: graph.nodes.filter((node) => node.id !== id),
            links: graph.links.filter((link) => link.from !== id && link.to !== id),
        };
    }

    // An input socket takes one link; connecting a second replaces the first, Blender-style.
    function addLink(graph, from, fromSocket, to, toSocket) {
        const fromNode = graph.nodes.find((node) => node.id === from);
        const toNode = graph.nodes.find((node) => node.id === to);
        const sourceSocket = fromSocket || LOOK;
        const targetSocket = toSocket || LOOK;
        if (!canConnect(fromNode, sourceSocket, toNode, targetSocket)) return graph;
        const withoutExisting = unlinkInput(graph, to, targetSocket);
        const candidate = {
            ...withoutExisting,
            links: withoutExisting.links.concat([{
                from, fromSocket: sourceSocket, to, toSocket: targetSocket,
            }]),
        };
        const { cycle } = evaluateOrder(normalizeGraph(candidate));
        // Reject the edit rather than leave a cyclic graph the user cannot evaluate away.
        return cycle ? graph : candidate;
    }

    function defaultGraph() {
        const source = makeNode('source.default', 80, 120, {}, 'default');
        const output = makeNode('output.state', 420, 120, { stateKey: 'idle_image', seconds: 0 }, 'out-idle');
        return {
            schemaVersion: 1,
            nodes: [source, output].filter(Boolean),
            links: [{ from: 'default', fromSocket: LOOK, to: 'out-idle', toSocket: LOOK }],
        };
    }

    return Object.freeze({
        LOOK,
        TYPES,
        canConnect,
        findSource,
        planOutputs,
        defaultGraph,
        emptyGraph,
        evaluate,
        makeNode,
        normalizeGraph,
        normalizeParams,
        addLink,
        removeNode,
        typeOf,
    });
}));
