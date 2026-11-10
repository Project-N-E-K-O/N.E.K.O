/* Authored expression gestures and face-anchored accents. No independent timers/owners. */
(function () {
    'use strict';
    const NS = 'http://www.w3.org/2000/svg';
    // Deliberately limited to the audited YUI rig. Eyes and lips stay owned by
    // the runtime; accepting arbitrary profile channels would break that contract.
    const MOTION_IDS = new Set([
        'ParamAngleX',
        'ParamAngleY',
        'ParamAngleZ',
        'ParamBodyAngleX',
        'ParamBodyAngleY',
        'ParamBodyAngleZ',
        'Param90',
        'Param91',
        'Param92',
        'Param93',
        'Param94',
        'Param95',
        'Param96'
    ]);
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const MAX_MOTIONS = 12;
    const CUE_FRAME_INTERVAL_MS = 32;

    function resolveMotions(core, motion) {
        return (Array.isArray(motion) ? motion : [])
            .slice(0, MAX_MOTIONS)
            .filter(
                (p) =>
                    p &&
                    MOTION_IDS.has(p.Id) &&
                    Number.isFinite(p.Value) &&
                    Number.isFinite(p.Amplitude) &&
                    Number.isFinite(p.Period)
            )
            .map((p) => ({
                ...p,
                index: core.getParameterIndex(p.Id),
                Amplitude: clamp(p.Amplitude, -3, 3),
                Period: clamp(p.Period, 1.5, 12),
                Phase: Number.isFinite(p.Phase) ? p.Phase : 0
            }))
            .filter((p) => p.index >= 0 && p.index < core.getParameterCount());
    }

    function applyMotions(core, motions, weight, elapsed, reduced) {
        for (const motion of motions) {
            const minimum = core.getParameterMinimumValue(motion.index);
            const maximum = core.getParameterMaximumValue(motion.index);
            const offset = reduced
                ? 0
                : motion.Amplitude * Math.sin((elapsed / 1000 / motion.Period) * Math.PI * 2 + motion.Phase);
            const target = clamp(motion.Value + offset, minimum, maximum);
            const current = core.getParameterValueByIndex(motion.index);
            core.setParameterValueByIndex(motion.index, current + (target - current) * weight);
        }
    }

    function node(name, attributes = {}) {
        const element = document.createElementNS(NS, name);
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
        return element;
    }

    window.nekoExpressionPerformance = {
        create(manager, model, core, config) {
            if (!config || typeof config !== 'object') return null;
            const cue = ['tears', 'anger'].includes(config.cue) ? config.cue : null;
            const strength = Number.isFinite(config.strength) ? clamp(config.strength, 0.25, 1) : 1;
            const reduced = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
            const motions = resolveMotions(core, config.motion);
            let svg = null,
                drops = [],
                mark = null,
                disposed = false,
                lastDraw = -Infinity,
                lastAppliedAt = 0;
            function visible() {
                const rect = manager.getHeadScreenRectInfo?.()?.rect;
                return !disposed && manager.currentModel === model && !model.destroyed
                    && model.visible !== false && model.renderable !== false && model.worldAlpha > 0.1
                    && document.visibilityState === 'visible' && rect && rect.width >= 4 && rect.height >= 4
                    && rect.left + rect.width > 0 && rect.top + rect.height > 0
                    && rect.left < window.innerWidth && rect.top < window.innerHeight;
            }
            function visibleCue() {
                if (!visible() || !svg?.isConnected || Date.now() - lastAppliedAt > 500) return null;
                const style = window.getComputedStyle(svg);
                if (style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) < 0.1) return null;
                return cue === 'tears' && !drops.some(({ element }) => {
                    const rect = element.getBoundingClientRect();
                    return Number(element.getAttribute('opacity')) > 0.15 && rect.width > 0 && rect.height > 0
                        && rect.right > 0 && rect.bottom > 0 && rect.left < window.innerWidth && rect.top < window.innerHeight;
                })
                    ? null : cue;
            }
            function mount() {
                svg = node('svg', {
                    viewBox: '0 0 100 100',
                    'aria-hidden': 'true',
                    'data-neko-expression-cue': cue
                });
                Object.assign(svg.style, {
                    position: 'fixed',
                    left: '0',
                    top: '0',
                    overflow: 'visible',
                    pointerEvents: 'none',
                    zIndex: '20',
                    transformOrigin: '0 0'
                });
                if (cue === 'tears') {
                    for (const side of [-1, 1])
                        for (let i = 0; i < 3; i++) {
                            const drop = node('path', {
                                d: 'M0 -12 C-3 -5 -8 -1 -8 5 A8 8 0 0 0 8 5 C8 -1 3 -5 0 -12Z',
                                fill: '#75d4ff',
                                stroke: '#f0fcff',
                                'stroke-width': 2
                            });
                            svg.appendChild(drop);
                            drops.push({ element: drop, side, offset: i / 3 });
                        }
                } else {
                    for (const x of [24, 76]) {
                        svg.appendChild(
                            node('ellipse', { cx: x, cy: 75, rx: 15, ry: 6, fill: '#ed6474', opacity: 0.28 })
                        );
                    }
                    mark = node('g', {
                        fill: 'none',
                        stroke: '#ed4d62',
                        'stroke-width': 6,
                        'stroke-linecap': 'round',
                        'stroke-linejoin': 'round'
                    });
                    // Four inward corners form an anger vein, not a platform-dependent emoji.
                    mark.appendChild(
                        node('path', {
                            d: 'M-16 -6 Q-6 -6 -6 -16 M6 -16 Q6 -6 16 -6 M16 6 Q6 6 6 16 M-6 16 Q-6 6 -16 6'
                        })
                    );
                    svg.appendChild(mark);
                }
                document.body.appendChild(svg);
            }
            function dispose() {
                disposed = true;
                svg?.remove();
                svg = null;
                drops = [];
                mark = null;
            }
            return {
                apply(actualCore, weight, elapsed) {
                    if (disposed) return;
                    if (manager.currentModel !== model || model.destroyed || actualCore !== core) {
                        dispose();
                        return;
                    }
                    applyMotions(core, motions, weight, elapsed, reduced);
                    lastAppliedAt = Date.now();
                    if (!cue || elapsed - lastDraw < CUE_FRAME_INTERVAL_MS) return;
                    lastDraw = elapsed;
                    const rect = manager.getHeadScreenRectInfo?.()?.rect;
                    if (
                        !rect ||
                        ![rect.left, rect.top, rect.width, rect.height].every(Number.isFinite) ||
                        rect.width < 4 ||
                        rect.height < 4 ||
                        model.visible === false ||
                        model.renderable === false
                    ) {
                        if (svg) svg.style.opacity = '0';
                        return;
                    }
                    if (!svg) mount();
                    svg.style.transform = `translate(${rect.left}px, ${rect.top}px)`;
                    svg.style.width = `${rect.width}px`;
                    svg.style.height = `${rect.height}px`;
                    svg.style.opacity = String(clamp(weight, 0, 1) * strength);
                    if (cue === 'tears') {
                        for (const { element, side, offset } of drops) {
                            const u = reduced ? 0.32 + offset * 0.38 : (elapsed / 1650 + offset) % 1;
                            const x = (side < 0 ? 25 : 75) + side * 52 * u;
                            const y = 58 - 22 * u + 72 * u * u;
                            element.setAttribute(
                                'transform',
                                `translate(${x} ${y}) rotate(${-side * (48 - 80 * u)}) scale(${0.58 + 0.25 * u})`
                            );
                            element.setAttribute('opacity', String(Math.sin(Math.PI * u) * 0.95));
                        }
                    } else {
                        const pulse = reduced ? 1 : 1 + 0.06 * Math.sin((elapsed / 1600) * Math.PI * 2);
                        mark.setAttribute('transform', `translate(84 13) rotate(12) scale(${pulse})`);
                    }
                },
                dispose,
                snapshot: () => ({
                    cue,
                    motionIds: motions.map((p) => p.Id),
                    active: !disposed,
                    reducedMotion: reduced,
                    visibleCue: visibleCue()
                })
            };
        }
    };
})();
