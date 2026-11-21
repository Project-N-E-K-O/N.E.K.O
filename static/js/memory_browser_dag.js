(function () {
    'use strict';

    const SVG_NS = 'http://www.w3.org/2000/svg';

    function tr(key, fallback, vars) {
        if (typeof window.t === 'function') {
            try {
                const res = window.t(key, vars);
                if (res && res !== key) return res;
            } catch (_e) {
                // fallback
            }
        }
        let text = fallback || key;
        if (vars && typeof vars === 'object') {
            Object.keys(vars).forEach(function (k) {
                text = text.replace(new RegExp('\\{\\{' + k + '\\}\\}', 'g'), String(vars[k]));
            });
        }
        return text;
    }

    function svgEl(tag, attrs, text) {
        const node = document.createElementNS(SVG_NS, tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (k) {
                if (attrs[k] !== null && attrs[k] !== undefined) {
                    node.setAttribute(k, String(attrs[k]));
                }
            });
        }
        if (text !== undefined && text !== null) {
            node.textContent = String(text);
        }
        return node;
    }

    function computeConnectedSet(startId, edges) {
        const connectedNodes = new Set([startId]);
        const connectedEdges = new Set();

        // Traverse upstream (target === cur) and downstream (source === cur) separately
        const upQueue = [startId];
        const upVisited = new Set([startId]);
        while (upQueue.length) {
            const cur = upQueue.shift();
            edges.forEach(function (e, idx) {
                if (e.target === cur) {
                    connectedEdges.add(idx);
                    connectedNodes.add(e.source);
                    if (!upVisited.has(e.source)) {
                        upVisited.add(e.source);
                        upQueue.push(e.source);
                    }
                }
            });
        }

        const downQueue = [startId];
        const downVisited = new Set([startId]);
        while (downQueue.length) {
            const cur = downQueue.shift();
            edges.forEach(function (e, idx) {
                if (e.source === cur) {
                    connectedEdges.add(idx);
                    connectedNodes.add(e.target);
                    if (!downVisited.has(e.target)) {
                        downVisited.add(e.target);
                        downQueue.push(e.target);
                    }
                }
            });
        }

        return { nodes: connectedNodes, edges: connectedEdges };
    }

    function truncateLabel(str, maxChars) {
        const clean = String(str || '').replace(/\s+/g, ' ').trim();
        if (clean.length <= maxChars) return clean;
        return clean.slice(0, maxChars - 1) + '…';
    }

    function mount(container, graphData, callbacks) {
        if (!container) return;
        container.textContent = '';

        const cb = callbacks || {};
        const lanes = (graphData && Array.isArray(graphData.lanes))
            ? graphData.lanes
            : ['message', 'recent_memo', 'fact', 'reflection', 'persona_entry'];
        const nodes = (graphData && Array.isArray(graphData.nodes)) ? graphData.nodes : [];
        const edges = (graphData && Array.isArray(graphData.edges)) ? graphData.edges : [];

        if (!nodes.length) {
            const empty = document.createElement('div');
            empty.className = 'memory-empty-state';
            empty.textContent = tr('memory.dagEmpty', '当前暂无可绘制的记忆因果节点。');
            container.appendChild(empty);
            return;
        }

        const laneLabels = {
            message: tr('memory.dagLane_message', '原始对话'),
            recent_memo: tr('memory.dagLane_recent_memo', '近期摘要'),
            fact: tr('memory.dagLane_fact', '提取事实'),
            reflection: tr('memory.dagLane_reflection', '归纳反思'),
            persona_entry: tr('memory.dagLane_persona_entry', '长期人设'),
        };

        const laneColors = {
            message: '#64748b',
            recent_memo: '#0ea5e9',
            fact: '#14b8a6',
            reflection: '#6366f1',
            persona_entry: '#2563eb',
        };

        const nodesByLane = {};
        lanes.forEach(function (lane) { nodesByLane[lane] = []; });
        nodes.forEach(function (n) {
            if (nodesByLane[n.lane]) {
                nodesByLane[n.lane].push(n);
            }
        });

        const maxRows = Math.max(
            2,
            lanes.reduce(function (acc, l) { return Math.max(acc, nodesByLane[l].length); }, 0)
        );
        const colW = 196;
        const colGap = 24;
        const padX = 16;
        const headerH = 34;
        const cardH = 44;
        const rowGap = 10;
        const svgW = padX * 2 + lanes.length * colW + (lanes.length - 1) * colGap;
        const svgH = headerH + 16 + maxRows * (cardH + rowGap) + 12;

        const posById = {};
        lanes.forEach(function (lane, colIdx) {
            const x = padX + colIdx * (colW + colGap);
            nodesByLane[lane].forEach(function (n, rowIdx) {
                const y = headerH + 10 + rowIdx * (cardH + rowGap);
                posById[n.id] = { x: x, y: y, cx: x + colW / 2, cy: y + cardH / 2, node: n };
            });
        });

        let selectedId = null;

        const scrollWrap = document.createElement('div');
        scrollWrap.className = 'memory-dag-scroll';

        const inspector = document.createElement('div');
        inspector.className = 'memory-dag-inspector';

        function renderSvg() {
            scrollWrap.textContent = '';
            const svg = svgEl('svg', {
                class: 'memory-dag-svg',
                viewBox: '0 0 ' + svgW + ' ' + svgH,
                width: svgW,
                height: svgH,
                role: 'img',
                'aria-label': tr('memory.dagAriaLabel', '五泳道记忆因果图'),
            });

            const activeSet = selectedId ? computeConnectedSet(selectedId, edges) : null;

            // Lane headers & column backdrops
            lanes.forEach(function (lane, colIdx) {
                const x = padX + colIdx * (colW + colGap);
                svg.appendChild(svgEl('rect', {
                    x: x - 4,
                    y: 4,
                    width: colW + 8,
                    height: svgH - 8,
                    rx: 10,
                    class: 'memory-dag-lane-bg',
                }));
                svg.appendChild(svgEl('text', {
                    x: x + colW / 2,
                    y: 23,
                    'text-anchor': 'middle',
                    class: 'memory-dag-lane-title',
                }, (laneLabels[lane] || lane) + ' (' + nodesByLane[lane].length + ')'));
            });

            // Edges layer
            const edgeLayer = svgEl('g', { class: 'memory-dag-edges' });
            edges.forEach(function (e, idx) {
                const from = posById[e.source];
                const to = posById[e.target];
                if (!from || !to) return;
                const x1 = from.x + colW;
                const y1 = from.cy;
                const x2 = to.x;
                const y2 = to.cy;
                const midX = (x1 + x2) / 2;
                const d = 'M ' + x1 + ' ' + y1 + ' C ' + midX + ' ' + y1 + ', ' + midX + ' ' + y2 + ', ' + x2 + ' ' + y2;
                let cls = 'memory-dag-edge';
                if (e.inferred) cls += ' is-inferred';
                if (activeSet) {
                    cls += activeSet.edges.has(idx) ? ' is-active' : ' is-dimmed';
                }
                edgeLayer.appendChild(svgEl('path', { d: d, class: cls }));
            });
            svg.appendChild(edgeLayer);

            // Nodes layer
            const nodeLayer = svgEl('g', { class: 'memory-dag-nodes' });
            nodes.forEach(function (n) {
                const pos = posById[n.id];
                if (!pos) return;
                const isSelected = selectedId === n.id;
                const isDimmed = Boolean(activeSet && !activeSet.nodes.has(n.id));
                const g = svgEl('g', {
                    class: 'memory-dag-node'
                        + (isSelected ? ' is-selected' : '')
                        + (isDimmed ? ' is-dimmed' : ''),
                    tabindex: '0',
                    role: 'button',
                    'aria-label': n.label || n.id,
                    'data-dag-node-id': n.id,
                });
                g.appendChild(svgEl('rect', {
                    x: pos.x,
                    y: pos.y,
                    width: colW,
                    height: cardH,
                    rx: 8,
                    class: 'memory-dag-node-rect',
                }));
                g.appendChild(svgEl('rect', {
                    x: pos.x,
                    y: pos.y,
                    width: 4,
                    height: cardH,
                    rx: 2,
                    fill: laneColors[n.lane] || '#2563eb',
                }));
                g.appendChild(svgEl('text', {
                    x: pos.x + 12,
                    y: pos.y + 18,
                    class: 'memory-dag-node-text',
                }, truncateLabel(n.label, 14)));
                g.appendChild(svgEl('text', {
                    x: pos.x + 12,
                    y: pos.y + 34,
                    class: 'memory-dag-node-meta',
                }, (n.entity || '') + (n.status ? ' · ' + n.status : '')));

                function onSelect() {
                    selectedId = (selectedId === n.id) ? null : n.id;
                    renderSvg();
                    renderInspector();
                }
                g.addEventListener('click', onSelect);
                g.addEventListener('keydown', function (ev) {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                        ev.preventDefault();
                        onSelect();
                    }
                });
                nodeLayer.appendChild(g);
            });
            svg.appendChild(nodeLayer);
            scrollWrap.appendChild(svg);
        }

        function renderInspector() {
            inspector.textContent = '';
            const chosen = selectedId && posById[selectedId] ? posById[selectedId].node : null;
            if (!chosen) {
                const hint = document.createElement('span');
                hint.className = 'imprint-note-meta';
                hint.textContent = tr(
                    'memory.dagSelectHint',
                    '点击任意节点高亮其上下游因果链路（共 {{nodes}} 个节点 · {{edges}} 条因果边）。',
                    { nodes: nodes.length, edges: edges.length }
                );
                inspector.appendChild(hint);
                return;
            }
            const info = document.createElement('div');
            info.className = 'memory-dag-inspector-info';
            const badge = document.createElement('span');
            badge.className = 'memory-mini-badge is-accent';
            badge.textContent = laneLabels[chosen.lane] || chosen.lane;
            const txt = document.createElement('span');
            txt.className = 'memory-dag-inspector-text';
            txt.textContent = chosen.label || '';
            info.appendChild(badge);
            info.appendChild(txt);
            inspector.appendChild(info);

            if (
                (chosen.kind === 'persona' || chosen.kind === 'reflection' || chosen.kind === 'fact')
                && typeof cb.onTrace === 'function'
            ) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'memory-pill-btn is-sm is-primary';
                btn.textContent = tr('memory.actionWhyRemember', '为什么这么记？');
                btn.addEventListener('click', function () {
                    cb.onTrace({ kind: chosen.kind, id: chosen.raw_id, text: chosen.label });
                });
                inspector.appendChild(btn);
            }
        }

        renderSvg();
        renderInspector();
        container.appendChild(scrollWrap);
        container.appendChild(inspector);
    }

    window.nekoMemoryDag = { mount: mount };
})();
