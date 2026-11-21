(function () {
    'use strict';

    const PALETTE_LIGHT = [
        '#38bdf8', '#818cf8', '#34d399', '#fb923c',
        '#f472b6', '#2dd4bf', '#a78bfa', '#fbbf24',
        '#60a5fa', '#4ade80', '#c084fc', '#fb7185',
    ];

    const PALETTE_DARK = [
        '#7dd3fc', '#a5b4fc', '#6ee7b7', '#fdba74',
        '#f9a8d4', '#5eead4', '#c4b5fd', '#fcd34d',
        '#93c5fd', '#86efac', '#d8b4fe', '#fda4af',
    ];

    function isDarkTheme() {
        return document.documentElement.getAttribute('data-theme') === 'dark';
    }

    function hexToRgba(hex, alpha) {
        const clean = hex.replace('#', '');
        const num = parseInt(clean, 16);
        const r = (num >> 16) & 255;
        const g = (num >> 8) & 255;
        const b = num & 255;
        return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
    }

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

    function mount(hostEl, data, opts) {
        if (!hostEl || !data) return;
        const options = opts || {};
        hostEl.textContent = '';

        // Cold-start honest progress view when < min_required embedded entries
        if (!data.ready) {
            const cur = Number(data.embedded_count || 0);
            const need = Number(data.min_required || 15);
            const pct = Math.max(4, Math.min(100, Math.round((cur / Math.max(1, need)) * 100)));

            const wrap = document.createElement('div');
            wrap.className = 'starmap-coldstart';
            const title = document.createElement('div');
            title.className = 'starmap-coldstart-title';
            title.textContent = tr(
                'memory.starMapColdStartTitle',
                '正在点亮你们的记忆星图（已点亮 {{cur}} / {{need}} 颗星）',
                { cur: cur, need: need }
            );
            const desc = document.createElement('p');
            desc.className = 'starmap-coldstart-desc';
            desc.textContent = tr(
                'memory.starMapColdStartDesc',
                '等日常对话再沉淀几段珍贵记忆，这里就会自然汇聚成一片属于你们的主题星云。'
            );
            const bar = document.createElement('div');
            bar.className = 'starmap-progress-track';
            const fill = document.createElement('div');
            fill.className = 'starmap-progress-fill';
            fill.style.width = pct + '%';
            bar.appendChild(fill);

            wrap.appendChild(title);
            wrap.appendChild(bar);
            wrap.appendChild(desc);
            hostEl.appendChild(wrap);
            return;
        }

        const points = Array.isArray(data.points) ? data.points : [];
        const clusters = Array.isArray(data.clusters) ? data.clusters : [];
        const pointByUid = {};
        points.forEach(function (p) { pointByUid[p.uid] = p; });
        const clusterById = {};
        clusters.forEach(function (c) { clusterById[c.id] = c; });

        const layout = document.createElement('div');
        layout.className = 'starmap-layout';

        const stage = document.createElement('div');
        stage.className = 'starmap-stage';

        const canvas = document.createElement('canvas');
        canvas.className = 'starmap-canvas';
        canvas.setAttribute('aria-label', tr('memory.starMapTitle', '专属记忆星图'));
        stage.appendChild(canvas);

        const tooltip = document.createElement('div');
        tooltip.className = 'starmap-tooltip';
        tooltip.hidden = true;
        stage.appendChild(tooltip);

        const controls = document.createElement('div');
        controls.className = 'starmap-canvas-controls';
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'memory-pill-btn is-sm';
        resetBtn.textContent = tr('memory.starMapResetView', '重置视角');
        controls.appendChild(resetBtn);
        stage.appendChild(controls);

        const detail = document.createElement('aside');
        detail.className = 'starmap-detail-card';

        layout.appendChild(stage);
        layout.appendChild(detail);
        hostEl.appendChild(layout);

        // Cluster legend pills below stage
        if (clusters.length) {
            const legend = document.createElement('div');
            legend.className = 'starmap-legend';
            clusters.forEach(function (c) {
                const pill = document.createElement('button');
                pill.type = 'button';
                pill.className = 'starmap-legend-pill';
                const dot = document.createElement('span');
                dot.className = 'starmap-legend-dot';
                const pal = isDarkTheme() ? PALETTE_DARK : PALETTE_LIGHT;
                dot.style.background = pal[c.id % pal.length];
                const txt = document.createElement('span');
                txt.textContent = c.label + ' (' + c.count + ')';
                pill.appendChild(dot);
                pill.appendChild(txt);
                pill.addEventListener('click', function () {
                    const medoid = pointByUid[c.medoid_uid] || points.find(function (p) { return p.cluster_id === c.id; });
                    if (medoid) {
                        selectPoint(medoid);
                    }
                });
                legend.appendChild(pill);
            });
            hostEl.appendChild(legend);
        }

        // Camera state
        let scale = 1.0;
        let offsetX = 0;
        let offsetY = 0;
        let hoveredUid = null;
        let selectedUid = points.length ? points[0].uid : null;
        let isDragging = false;
        let dragMoved = false;
        let dragStartX = 0;
        let dragStartY = 0;
        let dragOrigOffsetX = 0;
        let dragOrigOffsetY = 0;

        function worldToScreen(wx, wy, w, h) {
            const pad = 42;
            const halfW = Math.max(40, (w / 2) - pad);
            const halfH = Math.max(40, (h / 2) - pad);
            const r = Math.min(halfW, halfH);
            return [
                (w / 2) + offsetX + (wx * r * scale),
                (h / 2) + offsetY + (wy * r * scale),
            ];
        }

        function colorForCluster(cid) {
            const dark = isDarkTheme();
            if (cid < 0) return dark ? '#64748b' : '#94a3b8';
            const pal = dark ? PALETTE_DARK : PALETTE_LIGHT;
            return pal[cid % pal.length];
        }

        function draw() {
            const rect = stage.getBoundingClientRect();
            const w = Math.max(300, Math.round(rect.width || 560));
            const h = Math.max(260, Math.round(rect.height || 300));
            const dpr = Math.min(window.devicePixelRatio || 1, 2);

            if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
                canvas.width = Math.round(w * dpr);
                canvas.height = Math.round(h * dpr);
                canvas.style.width = w + 'px';
                canvas.style.height = h + 'px';
            }

            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            ctx.save();
            ctx.scale(dpr, dpr);

            const dark = isDarkTheme();
            // Soft sky background gradient
            const bg = ctx.createRadialGradient(w * 0.5, h * 0.45, 20, w * 0.5, h * 0.5, Math.max(w, h) * 0.75);
            if (dark) {
                bg.addColorStop(0, '#162436');
                bg.addColorStop(1, '#0f1722');
            } else {
                bg.addColorStop(0, '#f4fbff');
                bg.addColorStop(1, '#e8f5fd');
            }
            ctx.fillStyle = bg;
            ctx.fillRect(0, 0, w, h);

            // Subtle ambient nebula halos per cluster
            clusters.forEach(function (c) {
                const pos = worldToScreen(c.cx, c.cy, w, h);
                const radius = Math.max(38, Math.min(95, (28 + c.count * 6) * Math.sqrt(scale)));
                const col = colorForCluster(c.id);
                const halo = ctx.createRadialGradient(pos[0], pos[1], 4, pos[0], pos[1], radius);
                halo.addColorStop(0, hexToRgba(col, dark ? 0.22 : 0.24));
                halo.addColorStop(1, hexToRgba(col, 0.0));
                ctx.fillStyle = halo;
                ctx.beginPath();
                ctx.arc(pos[0], pos[1], radius, 0, Math.PI * 2);
                ctx.fill();
            });

            // Highlight neighbor links for selected or hovered star
            const focusUid = hoveredUid || selectedUid;
            const focusPoint = focusUid ? pointByUid[focusUid] : null;
            const neighborSet = {};
            if (focusPoint && Array.isArray(focusPoint.neighbors)) {
                const p1 = worldToScreen(focusPoint.x, focusPoint.y, w, h);
                focusPoint.neighbors.forEach(function (nUid) {
                    const nb = pointByUid[nUid];
                    if (!nb) return;
                    neighborSet[nUid] = true;
                    const p2 = worldToScreen(nb.x, nb.y, w, h);
                    ctx.beginPath();
                    ctx.moveTo(p1[0], p1[1]);
                    ctx.lineTo(p2[0], p2[1]);
                    ctx.strokeStyle = dark ? 'rgba(125, 211, 252, 0.45)' : 'rgba(20, 126, 166, 0.35)';
                    ctx.lineWidth = 1.5;
                    ctx.stroke();
                });
            }

            // Draw stars
            points.forEach(function (p) {
                const pos = worldToScreen(p.x, p.y, w, h);
                const isSelected = p.uid === selectedUid;
                const isHovered = p.uid === hoveredUid;
                const isNeighbor = Boolean(neighborSet[p.uid]);
                const baseR = 3.5 + Number(p.strength || 1) * 1.1;
                const r = isSelected ? baseR + 2.6 : (isHovered ? baseR + 1.8 : baseR);
                const col = colorForCluster(p.cluster_id);

                if (isSelected || p.user_pinned) {
                    ctx.beginPath();
                    ctx.arc(pos[0], pos[1], r + 4, 0, Math.PI * 2);
                    ctx.fillStyle = hexToRgba(col, isSelected ? 0.32 : 0.18);
                    ctx.fill();
                }

                ctx.beginPath();
                ctx.arc(pos[0], pos[1], r, 0, Math.PI * 2);
                ctx.fillStyle = col;
                ctx.globalAlpha = (focusPoint && !isSelected && !isHovered && !isNeighbor) ? 0.58 : 1.0;
                ctx.fill();
                ctx.globalAlpha = 1.0;

                if (isSelected || isHovered) {
                    ctx.lineWidth = 2;
                    ctx.strokeStyle = dark ? '#ffffff' : '#0f172a';
                    ctx.stroke();
                }
            });

            // Draw cluster theme pills at cluster centroids
            ctx.font = '600 12px "Segoe UI", "Noto Sans SC", sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            clusters.forEach(function (c) {
                const pos = worldToScreen(c.cx, c.cy, w, h);
                const label = c.label || '';
                if (!label) return;
                const tw = ctx.measureText(label).width;
                const pw = tw + 14;
                const ph = 22;
                const rx = pos[0] - pw / 2;
                const ry = pos[1] - ph / 2 - 14;

                ctx.fillStyle = dark ? 'rgba(15, 23, 34, 0.82)' : 'rgba(255, 255, 255, 0.88)';
                ctx.strokeStyle = hexToRgba(colorForCluster(c.id), 0.7);
                ctx.lineWidth = 1;
                ctx.beginPath();
                if (typeof ctx.roundRect === 'function') {
                    ctx.roundRect(rx, ry, pw, ph, 11);
                } else {
                    ctx.rect(rx, ry, pw, ph);
                }
                ctx.fill();
                ctx.stroke();

                ctx.fillStyle = dark ? '#e2f2ff' : '#134e6f';
                ctx.fillText(label, pos[0], ry + ph / 2);
            });

            ctx.restore();
        }

        function hitTest(clientX, clientY) {
            const rect = canvas.getBoundingClientRect();
            const mx = clientX - rect.left;
            const my = clientY - rect.top;
            let best = null;
            let bestDist = 14;
            points.forEach(function (p) {
                const pos = worldToScreen(p.x, p.y, rect.width, rect.height);
                const d = Math.hypot(pos[0] - mx, pos[1] - my);
                if (d < bestDist) {
                    bestDist = d;
                    best = p;
                }
            });
            return best;
        }

        function renderDetail(point) {
            detail.textContent = '';
            if (!point) {
                const empty = document.createElement('div');
                empty.className = 'starmap-detail-empty';
                empty.textContent = tr('memory.starMapClickHint', '点击任意一颗星，查看背后的记忆与相连片段');
                detail.appendChild(empty);
                return;
            }

            const cluster = clusterById[point.cluster_id];
            const head = document.createElement('div');
            head.className = 'memory-entry-badges';
            if (cluster) {
                const tag = document.createElement('span');
                tag.className = 'memory-mini-badge is-accent';
                tag.textContent = cluster.label;
                head.appendChild(tag);
            }
            const stars = document.createElement('span');
            stars.className = 'memory-mini-badge';
            stars.textContent = '★'.repeat(Math.max(1, Math.min(3, Number(point.strength || 1))));
            head.appendChild(stars);
            detail.appendChild(head);

            const body = document.createElement('p');
            body.className = 'starmap-detail-text';
            body.textContent = point.text;
            detail.appendChild(body);

            if (point.created_at) {
                const meta = document.createElement('div');
                meta.className = 'imprint-note-meta';
                meta.textContent = String(point.created_at).slice(0, 10);
                detail.appendChild(meta);
            }

            // Related stars jump links
            const neighbors = (point.neighbors || [])
                .map(function (uid) { return pointByUid[uid]; })
                .filter(Boolean);
            if (neighbors.length) {
                const relTitle = document.createElement('div');
                relTitle.className = 'starmap-rel-title';
                relTitle.textContent = tr('memory.starMapRelatedStars', '与之相连的记忆');
                detail.appendChild(relTitle);

                const relList = document.createElement('div');
                relList.className = 'starmap-rel-list';
                neighbors.slice(0, 3).forEach(function (nb) {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'starmap-rel-item';
                    btn.textContent = nb.text.length > 34 ? nb.text.slice(0, 34) + '...' : nb.text;
                    btn.addEventListener('click', function () { selectPoint(nb); });
                    relList.appendChild(btn);
                });
                detail.appendChild(relList);
            }

            const actions = document.createElement('div');
            actions.className = 'memory-card-actions';

            if (typeof options.onTrace === 'function') {
                const traceBtn = document.createElement('button');
                traceBtn.type = 'button';
                traceBtn.className = 'memory-pill-btn is-sm';
                traceBtn.textContent = tr('memory.actionWhyRemember', '为什么这么记？');
                traceBtn.addEventListener('click', function () { options.onTrace(point); });
                actions.appendChild(traceBtn);
            }
            if (typeof options.onCorrect === 'function') {
                const fixBtn = document.createElement('button');
                fixBtn.type = 'button';
                fixBtn.className = 'memory-pill-btn is-primary is-sm';
                fixBtn.textContent = tr('memory.starMapGoCorrect', '去校准');
                fixBtn.addEventListener('click', function () { options.onCorrect(point); });
                actions.appendChild(fixBtn);
            }
            detail.appendChild(actions);
        }

        function selectPoint(point) {
            selectedUid = point ? point.uid : null;
            renderDetail(point);
            draw();
        }

        // Mouse interactions
        canvas.addEventListener('pointerdown', function (ev) {
            isDragging = true;
            dragMoved = false;
            dragStartX = ev.clientX;
            dragStartY = ev.clientY;
            dragOrigOffsetX = offsetX;
            dragOrigOffsetY = offsetY;
            if (canvas.setPointerCapture) {
                try { canvas.setPointerCapture(ev.pointerId); } catch (_e) {}
            }
        });

        canvas.addEventListener('pointermove', function (ev) {
            if (isDragging) {
                const dx = ev.clientX - dragStartX;
                const dy = ev.clientY - dragStartY;
                if (Math.hypot(dx, dy) > 4) dragMoved = true;
                offsetX = dragOrigOffsetX + dx;
                offsetY = dragOrigOffsetY + dy;
                tooltip.hidden = true;
                draw();
                return;
            }
            const hit = hitTest(ev.clientX, ev.clientY);
            const nextHover = hit ? hit.uid : null;
            if (nextHover !== hoveredUid) {
                hoveredUid = nextHover;
                canvas.style.cursor = hit ? 'pointer' : 'grab';
                draw();
            }
            if (hit) {
                const rect = stage.getBoundingClientRect();
                tooltip.textContent = hit.text.length > 48 ? hit.text.slice(0, 48) + '...' : hit.text;
                tooltip.style.left = Math.min(rect.width - 220, Math.max(8, ev.clientX - rect.left + 12)) + 'px';
                tooltip.style.top = Math.max(8, ev.clientY - rect.top - 34) + 'px';
                tooltip.hidden = false;
            } else {
                tooltip.hidden = true;
            }
        });

        canvas.addEventListener('pointerup', function (ev) {
            const wasDrag = dragMoved;
            isDragging = false;
            dragMoved = false;
            if (!wasDrag) {
                const hit = hitTest(ev.clientX, ev.clientY);
                if (hit) {
                    selectPoint(hit);
                }
            }
        });

        canvas.addEventListener('pointerleave', function () {
            isDragging = false;
            hoveredUid = null;
            tooltip.hidden = true;
            draw();
        });

        canvas.addEventListener('wheel', function (ev) {
            ev.preventDefault();
            const factor = ev.deltaY < 0 ? 1.15 : 0.87;
            scale = Math.max(0.6, Math.min(3.5, scale * factor));
            draw();
        }, { passive: false });

        resetBtn.addEventListener('click', function () {
            scale = 1.0;
            offsetX = 0;
            offsetY = 0;
            draw();
        });

        renderDetail(selectedUid ? pointByUid[selectedUid] : null);
        window.requestAnimationFrame(draw);

        let ro = null;
        if (typeof ResizeObserver !== 'undefined') {
            ro = new ResizeObserver(function () { draw(); });
            ro.observe(stage);
        }

        return function unmount() {
            if (ro) {
                ro.disconnect();
                ro = null;
            }
        };
    }

    window.nekoMemoryStarMap = {
        mount: mount,
    };
})();
