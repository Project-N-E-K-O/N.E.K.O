/**
 * Scripted Motion - 剧本驱动的确定性动作播放（仅 viewer 多屏剧场使用）
 *
 * 与 live2d-emotion.js 的随机 playMotion 不同，这里完全按照剧本指定播放：
 *  - 普通动作："group" 或 "group:index"，直接调用模型原生 motion(group, index)，不随机。
 *  - 特殊动作：左enter / 右enter / 左leave / 右leave / lookat左 / lookat右，
 *    通过直接操作模型 transform / 注视参数实现，无需 motion3 文件。
 *
 * 由 controler 通过 monitor 下发的 {type:"motion_sequence"} 消息驱动。
 */
(function () {
    'use strict';

    const FORCE_PRIORITY = 3; // pixi-live2d-display MotionPriority.FORCE
    // 朝向屏幕最侧面的头部角度（ParamAngle 单位，约 ±30）；左为负、右为正。
    const LOOKAT_ANGLE = 28;
    let homeCaptured = false;
    let homeX = 0;
    let homeY = 0;
    let homeAlpha = 1;

    let sequenceTimers = [];
    let activeTween = null;

    function getMgr() {
        return window.live2dManager || null;
    }

    function getModel() {
        return window.live2dManager && window.live2dManager.currentModel;
    }

    // 解除剧本注视，恢复随机 idle 视线。
    function clearScriptedLookAt() {
        const mgr = getMgr();
        if (mgr) mgr._scriptedLookAt = null;
    }

    function getScreenWidth() {
        const app = window.live2dManager && window.live2dManager.pixi_app;
        if (app && app.renderer && app.renderer.screen && app.renderer.screen.width > 0) {
            return app.renderer.screen.width;
        }
        return window.innerWidth || 1280;
    }

    // 首次触碰特殊动作前记录"在场"位置，供 enter 归位 / leave 起点使用。
    function captureHome(model) {
        if (homeCaptured) return;
        homeX = model.x;
        homeY = model.y;
        homeAlpha = (typeof model.alpha === 'number') ? model.alpha : 1;
        homeCaptured = true;
    }

    function easeInOutQuad(t) {
        return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    }

    function cancelTween() {
        if (activeTween) {
            cancelAnimationFrame(activeTween);
            activeTween = null;
        }
    }

    // 在 duration 内把 model 的 x/alpha 从 from 补间到 to。
    function tweenTransform(model, from, to, duration) {
        cancelTween();
        const start = performance.now();
        function step(now) {
            const p = Math.min(1, (now - start) / duration);
            const e = easeInOutQuad(p);
            model.x = from.x + (to.x - from.x) * e;
            if (typeof model.alpha === 'number') {
                model.alpha = from.alpha + (to.alpha - from.alpha) * e;
            }
            if (p < 1) {
                activeTween = requestAnimationFrame(step);
            } else {
                activeTween = null;
            }
        }
        activeTween = requestAnimationFrame(step);
    }

    // 解析 "group" / "group:index"
    function parseGroupSpec(spec) {
        const idx = spec.lastIndexOf(':');
        if (idx > 0) {
            const group = spec.slice(0, idx);
            const index = parseInt(spec.slice(idx + 1), 10);
            return { group, index: Number.isFinite(index) ? index : 0 };
        }
        return { group: spec, index: 0 };
    }

    const SPECIAL = {
        '左enter': { kind: 'enter', side: 'left' },
        '右enter': { kind: 'enter', side: 'right' },
        '左leave': { kind: 'leave', side: 'left' },
        '右leave': { kind: 'leave', side: 'right' },
        'lookat左': { kind: 'lookat', side: 'left' },
        'lookat右': { kind: 'lookat', side: 'right' },
        // 容错别名
        'enter_left': { kind: 'enter', side: 'left' },
        'enter_right': { kind: 'enter', side: 'right' },
        'leave_left': { kind: 'leave', side: 'left' },
        'leave_right': { kind: 'leave', side: 'right' },
        'lookat_left': { kind: 'lookat', side: 'left' },
        'lookat_right': { kind: 'lookat', side: 'right' },
    };

    function playSpecial(model, def, duration) {
        captureHome(model);
        const screenW = getScreenWidth();
        if (def.kind === 'leave') {
            const targetX = def.side === 'left' ? homeX - screenW : homeX + screenW;
            tweenTransform(model,
                { x: model.x, alpha: (typeof model.alpha === 'number') ? model.alpha : 1 },
                { x: targetX, alpha: 0 },
                duration);
        } else if (def.kind === 'enter') {
            const startX = def.side === 'left' ? homeX - screenW : homeX + screenW;
            model.x = startX;
            if (typeof model.alpha === 'number') model.alpha = 0;
            tweenTransform(model,
                { x: startX, alpha: 0 },
                { x: homeX, alpha: homeAlpha },
                duration);
        } else if (def.kind === 'lookat') {
            // y 轴中点、x 轴最侧面：头部转到最侧面、视线水平。
            // 通过 _scriptedLookAt 覆盖 _updateRandomLookAt（mouseTracking 关时生效）。
            const mgr = getMgr();
            if (mgr) {
                mgr._scriptedLookAt = { x: def.side === 'left' ? -LOOKAT_ANGLE : LOOKAT_ANGLE, y: 0 };
            }
        }
    }

    // 播放单个剧本动作（特殊或普通）。duration 用于特殊动作的补间/保持时长。
    window.playScriptedMotion = function (spec, duration) {
        const model = getModel();
        if (!model) {
            console.warn('[ScriptedMotion] 模型未加载，跳过:', spec);
            return;
        }
        if (typeof spec !== 'string' || !spec.trim()) return;
        spec = spec.trim();
        const dur = (typeof duration === 'number' && duration > 0) ? duration : 6000;

        const special = SPECIAL[spec];
        if (special) {
            // 非 lookat 的动作开始时解除上一次的定向注视，避免姿态卡住。
            if (special.kind !== 'lookat') clearScriptedLookAt();
            playSpecial(model, special, dur);
            return;
        }

        // 其它普通动作开始时也解除定向注视
        clearScriptedLookAt();

        // 普通动作：完全按指定 group/index 播放，不随机
        const { group, index } = parseGroupSpec(spec);
        try {
            if (model.motion) {
                model.motion(group, index, FORCE_PRIORITY);
            }
        } catch (e) {
            console.warn('[ScriptedMotion] 播放动作失败:', spec, e);
        }
    };

    function clearSequence() {
        sequenceTimers.forEach((t) => clearTimeout(t));
        sequenceTimers = [];
        clearScriptedLookAt();
    }

    /**
     * 播放一个动作序列：delay 后播放第一个，之后每 interval 播下一个。
     * 新序列会取消上一个未完成的序列。
     */
    window.playMotionSequence = function (motions, delay, interval) {
        clearSequence();
        cancelTween();
        if (!Array.isArray(motions) || motions.length === 0) return;
        const startDelay = (typeof delay === 'number' && delay >= 0) ? delay : 1500;
        const slot = (typeof interval === 'number' && interval > 0) ? interval : 6000;

        motions.forEach((spec, i) => {
            const at = startDelay + i * slot;
            const timer = setTimeout(() => {
                window.playScriptedMotion(spec, slot);
            }, at);
            sequenceTimers.push(timer);
        });
    };

    window.clearMotionSequence = clearSequence;
})();
