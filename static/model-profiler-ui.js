/**
 * Model Profiler UI - 模型性能分析器 UI 面板
 * 
 * 依赖：model-profiler.js (ModelProfiler)
 * 
 * 用法：
 *   const profilerUI = new ModelProfilerUI({
 *       container: document.getElementById('profiler-mount'),
 *       getManager: () => window.mmdManager || window.vrmManager,
 *       getRenderer: () => (window.mmdManager || window.vrmManager)?.renderer
 *   });
 *   profilerUI.mount();
 */

class ModelProfilerUI {
    /**
     * @param {object} opts
     * @param {HTMLElement} opts.container - 挂载容器
     * @param {() => object} opts.getManager - 获取当前模型管理器的函数
     * @param {() => THREE.WebGLRenderer} opts.getRenderer - 获取渲染器的函数
     * @param {() => ({key: string, fallback: string}|null)} [opts.getUnsupportedReason] - 返回 {key, fallback} 则三个快照面板都显示该提示并跳过实际采样（用于 Live2D 等不支持类型）；key 用于 _refreshI18n 重渲染，fallback 是 i18n 未就绪时的兜底文本
     * @param {boolean} [opts.collapsed=true] - 初始是否折叠
     */
    constructor(opts) {
        this.container = opts.container;
        this.getManager = opts.getManager;
        this.getRenderer = opts.getRenderer;
        this.getUnsupportedReason = opts.getUnsupportedReason;
        this.collapsed = opts.collapsed !== false;

        this.profiler = new ModelProfiler({ historySize: 300 });
        this._updateTimer = null;
        this._chartCanvas = null;
        this._chartCtx = null;
        this._mounted = false;
        this._elements = {};
        this._badgeKey = 'profiler.badge.idle';
        this._lastSnapshot = null;
        this._lastAssessment = null;
        this._onLocaleChange = () => this._refreshI18n();
    }

    // i18n helper：window.t 不可用时回退到中文
    _t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.t === 'function') {
            try {
                const v = window.t(key);
                if (v && v !== key) return v;
            } catch (e) { /* swallow */ }
        }
        return fallback;
    }

    // ═══════════════════ 生命周期 ═══════════════════

    mount() {
        if (this._mounted) return;
        this._mounted = true;
        this._buildDOM();
        this._bindEvents();
        window.addEventListener('localechange', this._onLocaleChange);
        if (window.i18n && typeof window.i18n.on === 'function') {
            window.i18n.on('languageChanged', this._onLocaleChange);
        }
    }

    unmount() {
        this._cleanupDebugHelpers();
        this.profiler.dispose();
        if (this._updateTimer) {
            clearInterval(this._updateTimer);
            this._updateTimer = null;
        }
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        window.removeEventListener('localechange', this._onLocaleChange);
        if (window.i18n && typeof window.i18n.off === 'function') {
            window.i18n.off('languageChanged', this._onLocaleChange);
        }
        if (this.container) {
            this.container.innerHTML = '';
        }
        this._mounted = false;
    }

    // ═══════════════════ DOM 构建 ═══════════════════

    _buildDOM() {
        const t = (k, fb) => this._t(k, fb);
        const panel = document.createElement('div');
        panel.className = 'profiler-panel' + (this.collapsed ? ' collapsed' : '');
        panel.innerHTML = `
            <div class="profiler-header">
                <span class="profiler-title" data-i18n-key="profiler.title">📊 ${t('profiler.title','性能分析器')}</span>
                <div class="profiler-header-right">
                    <span class="profiler-badge profiler-badge-idle" data-i18n-key="profiler.badge.idle">${t('profiler.badge.idle','待机')}</span>
                    <button class="profiler-toggle-btn"
                            data-i18n-title-key="profiler.toggle.title"
                            data-i18n-aria-key="profiler.toggle.aria"
                            title="${t('profiler.toggle.title','展开/折叠')}"
                            aria-label="${t('profiler.toggle.aria','展开/折叠性能分析器')}">▼</button>
                </div>
            </div>
            <div class="profiler-body">
                <div class="profiler-controls">
                    <button class="profiler-btn profiler-start-btn" data-i18n-key="profiler.btn.start" data-i18n-prefix="▶ ">▶ ${t('profiler.btn.start','开始采集')}</button>
                    <button class="profiler-btn profiler-stop-btn" disabled data-i18n-key="profiler.btn.stop" data-i18n-prefix="⏹ ">⏹ ${t('profiler.btn.stop','停止')}</button>
                    <button class="profiler-btn profiler-snapshot-btn" data-i18n-key="profiler.btn.snapshot" data-i18n-prefix="📷 ">📷 ${t('profiler.btn.snapshot','快照')}</button>
                    <button class="profiler-btn profiler-reset-btn" data-i18n-key="profiler.btn.reset" data-i18n-prefix="🔄 ">🔄 ${t('profiler.btn.reset','重置')}</button>
                </div>

                <!-- 调试可视化 -->
                <div class="profiler-section">
                    <div class="profiler-section-title" data-i18n-key="profiler.debug.title">${t('profiler.debug.title','调试可视化')}</div>
                    <div class="profiler-controls">
                        <button class="profiler-btn profiler-toggle-physics" data-debug="physics-kinematic"
                                data-i18n-key="profiler.debug.physicsKinematic" data-i18n-prefix="⬤ "
                                data-i18n-title-key="profiler.debug.physicsKinematicTip"
                                title="${t('profiler.debug.physicsKinematicTip','Kinematic 刚体 (Mode 0)：跟随骨骼动画，不受物理影响')}"
                                style="border-color:#FF6B35;color:#FF6B35;">⬤ ${t('profiler.debug.physicsKinematic','骨骼刚体')}</button>
                        <button class="profiler-btn profiler-toggle-physics" data-debug="physics-dynamic"
                                data-i18n-key="profiler.debug.physicsDynamic" data-i18n-prefix="⬤ "
                                data-i18n-title-key="profiler.debug.physicsDynamicTip"
                                title="${t('profiler.debug.physicsDynamicTip','Dynamic 刚体 (Mode 1)：纯物理驱动，受重力和碰撞影响')}"
                                style="border-color:#E040FB;color:#E040FB;">⬤ ${t('profiler.debug.physicsDynamic','物理刚体')}</button>
                        <button class="profiler-btn profiler-toggle-physics" data-debug="physics-mixed"
                                data-i18n-key="profiler.debug.physicsMixed" data-i18n-prefix="⬤ "
                                data-i18n-title-key="profiler.debug.physicsMixedTip"
                                title="${t('profiler.debug.physicsMixedTip','Mixed 刚体 (Mode 2)：物理+骨骼混合，位置锚定到骨骼')}"
                                style="border-color:#448AFF;color:#448AFF;">⬤ ${t('profiler.debug.physicsMixed','混合刚体')}</button>
                    </div>
                    <div class="profiler-controls" style="margin-top:4px;">
                        <button class="profiler-btn" data-debug="physics-solid"
                                data-i18n-key="profiler.debug.physicsSolid" data-i18n-prefix="🔲 "
                                data-i18n-title-key="profiler.debug.physicsSolidTip"
                                title="${t('profiler.debug.physicsSolidTip','切换线框/半透明面：半透明面模式可观察刚体堆叠关系和重叠程度')}"
                                style="border-color:#999;color:#999;">🔲 ${t('profiler.debug.physicsSolid','半透明面')}</button>
                        <button class="profiler-btn profiler-toggle-ik" data-debug="ik"
                                data-i18n-key="profiler.debug.ik" data-i18n-prefix="🦴 "
                                data-i18n-title-key="profiler.debug.ikTip"
                                title="${t('profiler.debug.ikTip','IK 求解器：显示 IK 目标、效应器和链节点')}">🦴 ${t('profiler.debug.ik','IK 链')}</button>
                        <button class="profiler-btn profiler-toggle-skeleton" data-debug="skeleton"
                                data-i18n-key="profiler.debug.skeleton" data-i18n-prefix="🩻 "
                                data-i18n-title-key="profiler.debug.skeletonTip"
                                title="${t('profiler.debug.skeletonTip','骨骼线框：显示完整骨骼层级结构')}">🩻 ${t('profiler.debug.skeleton','骨骼')}</button>
                    </div>
                </div>

                <!-- FPS 实时数据 -->
                <div class="profiler-section">
                    <div class="profiler-section-title" data-i18n-key="profiler.fps.recent60s">${t('profiler.fps.recent60s','帧率 (FPS) — 最近 60s')}</div>
                    <div class="profiler-fps-grid">
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.current">${t('profiler.fps.current','当前')}</span>
                            <span class="profiler-stat-value" data-stat="fps-current">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.avg">${t('profiler.fps.avg','平均')}</span>
                            <span class="profiler-stat-value" data-stat="fps-avg">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.min">${t('profiler.fps.min','最低')}</span>
                            <span class="profiler-stat-value profiler-stat-warn" data-stat="fps-min">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.max">${t('profiler.fps.max','最高')}</span>
                            <span class="profiler-stat-value" data-stat="fps-max">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.p1">${t('profiler.fps.p1','1% Low')}</span>
                            <span class="profiler-stat-value profiler-stat-warn" data-stat="fps-p1">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.jitter">${t('profiler.fps.jitter','抖动σ')}</span>
                            <span class="profiler-stat-value" data-stat="fps-jitter">--</span>
                        </div>
                    </div>
                    <div class="profiler-section-title profiler-alltime-title" data-i18n-key="profiler.fps.alltime">${t('profiler.fps.alltime','全程统计')}</div>
                    <div class="profiler-fps-grid">
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.avg">${t('profiler.fps.avg','平均')}</span>
                            <span class="profiler-stat-value" data-stat="at-fps-avg">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.min">${t('profiler.fps.min','最低')}</span>
                            <span class="profiler-stat-value profiler-stat-warn" data-stat="at-fps-min">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.max">${t('profiler.fps.max','最高')}</span>
                            <span class="profiler-stat-value" data-stat="at-fps-max">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.p1">${t('profiler.fps.p1','1% Low')}</span>
                            <span class="profiler-stat-value profiler-stat-warn" data-stat="at-fps-p1">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.fps.samples">${t('profiler.fps.samples','采样数')}</span>
                            <span class="profiler-stat-value" data-stat="at-fps-samples">--</span>
                        </div>
                    </div>
                </div>

                <!-- FPS 图表 -->
                <div class="profiler-section">
                    <div class="profiler-section-title" data-i18n-key="profiler.fps.chartTitle">${t('profiler.fps.chartTitle','帧率曲线')}</div>
                    <div class="profiler-chart-container">
                        <canvas class="profiler-chart" width="400" height="120"></canvas>
                    </div>
                </div>

                <!-- 帧时间 -->
                <div class="profiler-section">
                    <div class="profiler-section-title" data-i18n-key="profiler.frametime.recent60s">${t('profiler.frametime.recent60s','帧时间 (ms) — 最近 60s')}</div>
                    <div class="profiler-fps-grid">
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.frametime.avg">${t('profiler.frametime.avg','平均')}</span>
                            <span class="profiler-stat-value" data-stat="ft-avg">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.frametime.p95">${t('profiler.frametime.p95','P95')}</span>
                            <span class="profiler-stat-value" data-stat="ft-p95">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.frametime.p99">${t('profiler.frametime.p99','P99')}</span>
                            <span class="profiler-stat-value profiler-stat-warn" data-stat="ft-p99">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.frametime.max">${t('profiler.frametime.max','最大')}</span>
                            <span class="profiler-stat-value profiler-stat-warn" data-stat="ft-max">--</span>
                        </div>
                    </div>
                    <div class="profiler-section-title profiler-alltime-title" data-i18n-key="profiler.fps.alltime">${t('profiler.fps.alltime','全程统计')}</div>
                    <div class="profiler-fps-grid">
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.frametime.avg">${t('profiler.frametime.avg','平均')}</span>
                            <span class="profiler-stat-value" data-stat="at-ft-avg">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.frametime.min">${t('profiler.frametime.min','最小')}</span>
                            <span class="profiler-stat-value" data-stat="at-ft-min">--</span>
                        </div>
                        <div class="profiler-stat">
                            <span class="profiler-stat-label" data-i18n-key="profiler.frametime.max">${t('profiler.frametime.max','最大')}</span>
                            <span class="profiler-stat-value profiler-stat-warn" data-stat="at-ft-max">--</span>
                        </div>
                    </div>
                </div>

                <!-- 模型属性 -->
                <div class="profiler-section">
                    <div class="profiler-section-title" data-i18n-key="profiler.section.modelInfo">${t('profiler.section.modelInfo','模型属性')}</div>
                    <div class="profiler-model-info" data-section="model-info">
                        <div class="profiler-placeholder" data-i18n-key="profiler.placeholder.snapshotForInfo">${t('profiler.placeholder.snapshotForInfo','点击「快照」获取模型信息')}</div>
                    </div>
                </div>

                <!-- 性能评级 -->
                <div class="profiler-section">
                    <div class="profiler-section-title" data-i18n-key="profiler.section.assessment">${t('profiler.section.assessment','性能评级')}</div>
                    <div class="profiler-assessment" data-section="assessment">
                        <div class="profiler-placeholder" data-i18n-key="profiler.placeholder.snapshotForRating">${t('profiler.placeholder.snapshotForRating','点击「快照」获取评级')}</div>
                    </div>
                </div>

                <!-- GPU 信息 -->
                <div class="profiler-section">
                    <div class="profiler-section-title" data-i18n-key="profiler.section.gpuInfo">${t('profiler.section.gpuInfo','GPU 信息')}</div>
                    <div class="profiler-gpu-info" data-section="gpu-info">
                        <div class="profiler-placeholder" data-i18n-key="profiler.placeholder.snapshotForGpu">${t('profiler.placeholder.snapshotForGpu','点击「快照」获取 GPU 信息')}</div>
                    </div>
                </div>
            </div>
        `;

        this.container.appendChild(panel);
        this._elements.panel = panel;
        this._elements.badge = panel.querySelector('.profiler-badge');
        this._elements.toggleBtn = panel.querySelector('.profiler-toggle-btn');
        this._chartCanvas = panel.querySelector('.profiler-chart');
        this._chartCtx = this._chartCanvas?.getContext('2d');

        // 缓存 stat 元素
        this._elements.stats = {};
        panel.querySelectorAll('[data-stat]').forEach(el => {
            this._elements.stats[el.dataset.stat] = el;
        });
        this._elements.modelInfo = panel.querySelector('[data-section="model-info"]');
        this._elements.assessment = panel.querySelector('[data-section="assessment"]');
        this._elements.gpuInfo = panel.querySelector('[data-section="gpu-info"]');
    }

    // 语言切换：重设所有静态文本节点 + 重渲染快照面板
    _refreshI18n() {
        const panel = this._elements?.panel;
        if (!panel) return;
        panel.querySelectorAll('[data-i18n-key]').forEach(el => {
            const key = el.dataset.i18nKey;
            const prefix = el.dataset.i18nPrefix || '';
            el.textContent = prefix + this._t(key, el.textContent.replace(prefix, ''));
        });
        panel.querySelectorAll('[data-i18n-title-key]').forEach(el => {
            el.title = this._t(el.dataset.i18nTitleKey, el.title);
        });
        panel.querySelectorAll('[data-i18n-aria-key]').forEach(el => {
            el.setAttribute('aria-label', this._t(el.dataset.i18nAriaKey, el.getAttribute('aria-label') || ''));
        });
        // badge 当前状态文本（不在 [data-i18n-key] 范围内的 fallback）
        if (this._elements.badge && this._badgeKey) {
            this._elements.badge.textContent = this._t(this._badgeKey, this._elements.badge.textContent);
            this._elements.badge.dataset.i18nKey = this._badgeKey;
        }
        // 快照面板：如果有上次的数据则重渲染，否则重设 placeholder
        if (this._lastSnapshot) {
            this._renderModelInfo(this._lastSnapshot);
            this._renderGPUInfo(this._lastSnapshot);
        }
        if (this._lastAssessment) {
            this._renderAssessment(this._lastAssessment);
        }
    }

    _setBadge(key, fallback, cls) {
        this._badgeKey = key;
        if (this._elements.badge) {
            this._elements.badge.textContent = this._t(key, fallback);
            this._elements.badge.className = 'profiler-badge ' + cls;
            this._elements.badge.dataset.i18nKey = key;
        }
    }

    _bindEvents() {
        const panel = this._elements.panel;

        // 折叠/展开
        panel.querySelector('.profiler-header').addEventListener('click', () => {
            this.collapsed = !this.collapsed;
            panel.classList.toggle('collapsed', this.collapsed);
            this._elements.toggleBtn.textContent = this.collapsed ? '▼' : '▲';
            if (!this.collapsed) this._resizeChart();
        });

        // 开始
        panel.querySelector('.profiler-start-btn').addEventListener('click', () => this._onStart());
        // 停止
        panel.querySelector('.profiler-stop-btn').addEventListener('click', () => this._onStop());
        // 快照
        panel.querySelector('.profiler-snapshot-btn').addEventListener('click', () => this._onSnapshot());
        // 重置
        panel.querySelector('.profiler-reset-btn').addEventListener('click', () => this._onReset());

        // 图表 canvas 自适应
        this._resizeObserver = new ResizeObserver(() => this._resizeChart());
        const chartContainer = panel.querySelector('.profiler-chart-container');
        if (chartContainer) this._resizeObserver.observe(chartContainer);

        // 调试可视化开关
        this._debugHelpers = { 'physics-kinematic': null, 'physics-dynamic': null, 'physics-mixed': null, ik: null, skeleton: null };
        this._physicsSolidMode = false;
        panel.querySelectorAll('[data-debug]').forEach(btn => {
            btn.addEventListener('click', () => {
                if (btn.dataset.debug === 'physics-solid') {
                    this._togglePhysicsSolidMode(btn);
                } else {
                    this._toggleDebugHelper(btn.dataset.debug, btn);
                }
            });
        });
    }

    // ═══════════════════ 调试可视化 ═══════════════════

    _toggleDebugHelper(type, btn) {
        const manager = this.getManager?.();
        if (!manager) return;
        const currentModel = manager.currentModel;
        const mmd = currentModel?.mesh ? currentModel : null;
        const vrm = currentModel?.vrm || (currentModel?.scene && !currentModel?.mesh ? currentModel : null);
        const scene = manager.scene;
        if (!scene) return;

        // 已有 → 移除
        if (this._debugHelpers[type]) {
            if (type.startsWith('physics-')) {
                this._removePhysicsWireframes(type);
            } else {
                scene.remove(this._debugHelpers[type]);
                if (this._debugHelpers[type].dispose) this._debugHelpers[type].dispose();
            }
            this._debugHelpers[type] = null;
            btn.classList.remove('profiler-debug-active');
            return;
        }

        // 创建 helper
        let helper = null;
        try {
            if (type.startsWith('physics-')) {
                if (!mmd || !mmd.mesh) {
                    console.warn('[Profiler] 当前模型不支持物理刚体可视化');
                    return;
                }
                const modeMap = { 'physics-kinematic': 0, 'physics-dynamic': 1, 'physics-mixed': 2 };
                const colorMap = { 'physics-kinematic': 0xFF6B35, 'physics-dynamic': 0xE040FB, 'physics-mixed': 0x448AFF };
                helper = this._createPhysicsWireframes(mmd, modeMap[type], colorMap[type]);
            } else if (type === 'ik') {
                const anim = manager.animationModule;
                if (anim?.ikSolver?.createHelper) {
                    helper = anim.ikSolver.createHelper();
                } else {
                    console.warn('[Profiler] IK 求解器不可用');
                }
            } else if (type === 'skeleton') {
                const THREE = window.THREE;
                if (THREE?.SkeletonHelper) {
                    const skeletonTarget = mmd?.mesh || vrm?.scene || null;
                    if (!skeletonTarget) {
                        console.warn('[Profiler] 当前模型未找到可用骨骼节点');
                        return;
                    }
                    helper = new THREE.SkeletonHelper(skeletonTarget);
                    helper.visible = true;
                } else {
                    console.warn('[Profiler] SkeletonHelper 不可用');
                }
            }
        } catch (e) {
            console.warn(`[Profiler] 创建 ${type} helper 失败:`, e);
        }

        if (helper) {
            if (!type.startsWith('physics-')) scene.add(helper);
            this._debugHelpers[type] = helper;
            btn.classList.add('profiler-debug-active');
        }
    }

    /**
     * 骨骼挂载式物理可视化：把线框直接挂到骨骼上，自动跟随缩放/位移/旋转
     */
    _createPhysicsWireframes(mmd, filterMode, color) {
        const THREE = window.THREE;
        if (!THREE) return null;

        const physics = mmd.physics?.getPhysics?.();
        if (!physics || !physics.bodies) {
            console.warn('[Profiler] 物理数据不可用');
            return null;
        }

        const material = new THREE.MeshBasicMaterial({
            color: color,
            wireframe: !this._physicsSolidMode,
            transparent: true,
            opacity: this._physicsSolidMode ? 0.15 : 0.3,
            depthTest: false,
            depthWrite: false
        });

        const wireframes = [];
        for (let i = 0; i < physics.bodies.length; i++) {
            const body = physics.bodies[i];
            const params = body.params;
            if (params.physicsMode !== filterMode) continue;
            const bone = body.bone;
            if (!bone) continue;

            const [w, h, d] = params.shapeSize;
            let geo;
            switch (params.shapeType) {
                case 0: geo = new THREE.SphereGeometry(w, 8, 6); break;           // Sphere
                case 1: geo = new THREE.BoxGeometry(w * 2, h * 2, d * 2, 4, 4, 4); break; // Box: half-extent → full
                case 2: geo = new THREE.CapsuleGeometry(w, h, 4, 8); break;       // Capsule
                default: continue;
            }

            const wireframe = new THREE.Mesh(geo, material);

            const offsetForm = body.boneOffsetForm;
            if (offsetForm) {
                const o = offsetForm.getOrigin();
                wireframe.position.set(o.x(), o.y(), o.z());
                const r = offsetForm.getRotation();
                wireframe.quaternion.set(r.x(), r.y(), r.z(), r.w());
            }

            wireframe.userData._physicsWireframe = true;
            bone.add(wireframe);
            wireframes.push({ bone, wireframe });
        }

        return { wireframes, material };
    }

    _removePhysicsWireframes(type) {
        const data = this._debugHelpers[type];
        if (!data) return;
        for (const { bone, wireframe } of data.wireframes) {
            bone.remove(wireframe);
            wireframe.geometry.dispose();
        }
        data.material.dispose();
    }

    _togglePhysicsSolidMode(btn) {
        this._physicsSolidMode = !this._physicsSolidMode;
        btn.classList.toggle('profiler-debug-active', this._physicsSolidMode);

        for (const type of ['physics-kinematic', 'physics-dynamic', 'physics-mixed']) {
            const data = this._debugHelpers[type];
            if (!data) continue;
            data.material.wireframe = !this._physicsSolidMode;
            data.material.opacity = this._physicsSolidMode ? 0.15 : 0.3;
            data.material.needsUpdate = true;
        }
    }

    _cleanupDebugHelpers() {
        const manager = this.getManager?.();
        const scene = manager?.scene;
        for (const type of Object.keys(this._debugHelpers)) {
            if (this._debugHelpers[type]) {
                if (type.startsWith('physics-')) {
                    this._removePhysicsWireframes(type);
                } else {
                    if (scene) scene.remove(this._debugHelpers[type]);
                    if (this._debugHelpers[type].dispose) this._debugHelpers[type].dispose();
                }
                this._debugHelpers[type] = null;
            }
        }
        // 重置按钮状态
        this._elements?.panel?.querySelectorAll('[data-debug]').forEach(btn => {
            btn.classList.remove('profiler-debug-active');
        });
    }

    // ═══════════════════ 操作 ═══════════════════

    _onStart() {
        const renderer = this.getRenderer?.();
        this.profiler.start(renderer);
        this._setBadge('profiler.badge.warmup', '预热中...', 'profiler-badge-warmup');

        const startBtn = this._elements.panel.querySelector('.profiler-start-btn');
        const stopBtn = this._elements.panel.querySelector('.profiler-stop-btn');
        startBtn.disabled = true;
        stopBtn.disabled = false;

        // 定时刷新 UI
        this._updateTimer = setInterval(() => {
            // warmup 结束后切换 badge
            if (this.profiler._warmupDone && this._badgeKey !== 'profiler.badge.running') {
                this._setBadge('profiler.badge.running', '采集中', 'profiler-badge-running');
            }
            this._updateDisplay();
        }, 500);
    }

    _onStop() {
        this.profiler.stop();
        this._setBadge('profiler.badge.stopped', '已停止', 'profiler-badge-stopped');

        const startBtn = this._elements.panel.querySelector('.profiler-start-btn');
        const stopBtn = this._elements.panel.querySelector('.profiler-stop-btn');
        startBtn.disabled = false;
        stopBtn.disabled = true;

        if (this._updateTimer) {
            clearInterval(this._updateTimer);
            this._updateTimer = null;
        }
        // 最后刷新一次
        this._updateDisplay();
    }

    _onSnapshot() {
        this._lastSnapshot = null;
        this._lastAssessment = null;
        const reason = this.getUnsupportedReason?.();
        if (reason && reason.key) {
            const text = this._t(reason.key, reason.fallback || reason.key);
            this._fillPlaceholder(this._elements.modelInfo, text, reason.key);
            this._fillPlaceholder(this._elements.assessment, text, reason.key);
            this._fillPlaceholder(this._elements.gpuInfo, text, reason.key);
            return;
        }
        const manager = this.getManager?.();
        if (!manager) {
            this._fillPlaceholder(
                this._elements.modelInfo,
                this._t('profiler.placeholder.noManager', '未检测到模型管理器'),
                'profiler.placeholder.noManager'
            );
            return;
        }
        const snap = this.profiler.snapshot(manager);
        this._lastSnapshot = snap;
        this._lastAssessment = this.profiler.assess(snap);
        this._renderModelInfo(snap);
        this._renderAssessment(this._lastAssessment);
        this._renderGPUInfo(snap);
    }

    _fillPlaceholder(el, text, key) {
        if (!el) return;
        el.innerHTML = '';
        const div = document.createElement('div');
        div.className = 'profiler-placeholder';
        div.textContent = text;
        if (key) div.dataset.i18nKey = key;
        el.appendChild(div);
    }

    _onReset() {
        this.profiler.reset();
        this._lastSnapshot = null;
        this._lastAssessment = null;

        // 如果正在采集，badge 切回预热状态
        if (this.profiler.isRunning) {
            this._setBadge('profiler.badge.warmup', '预热中...', 'profiler-badge-warmup');
        } else {
            this._setBadge('profiler.badge.idle', '待机', 'profiler-badge-idle');
        }

        // 重置所有 stat 显示
        for (const el of Object.values(this._elements.stats)) {
            el.textContent = '--';
        }
        this._fillPlaceholder(this._elements.modelInfo, this._t('profiler.placeholder.snapshotForInfo', '点击「快照」获取模型信息'), 'profiler.placeholder.snapshotForInfo');
        this._fillPlaceholder(this._elements.assessment, this._t('profiler.placeholder.snapshotForRating', '点击「快照」获取评级'), 'profiler.placeholder.snapshotForRating');
        this._fillPlaceholder(this._elements.gpuInfo, this._t('profiler.placeholder.snapshotForGpu', '点击「快照」获取 GPU 信息'), 'profiler.placeholder.snapshotForGpu');
        this._clearChart();
    }

    // ═══════════════════ 显示更新 ═══════════════════

    _updateDisplay() {
        const fps = this.profiler.getFPSStats();
        const ft = this.profiler.getFrameTimeStats();
        const atFps = this.profiler.getAllTimeFPSStats();
        const atFt = this.profiler.getAllTimeFrameTimeStats();
        const stats = this._elements.stats;

        // 最近 60s
        stats['fps-current'].textContent = fps.current || '--';
        stats['fps-avg'].textContent = fps.avg || '--';
        stats['fps-min'].textContent = fps.min || '--';
        stats['fps-max'].textContent = fps.max || '--';
        stats['fps-p1'].textContent = fps.p1 || '--';
        stats['fps-jitter'].textContent = fps.jitter ? fps.jitter + ' ms' : '--';

        stats['ft-avg'].textContent = ft.avg || '--';
        stats['ft-p95'].textContent = ft.p95 || '--';
        stats['ft-p99'].textContent = ft.p99 || '--';
        stats['ft-max'].textContent = ft.max || '--';

        // 全程
        stats['at-fps-avg'].textContent = atFps.avg || '--';
        stats['at-fps-min'].textContent = atFps.min || '--';
        stats['at-fps-max'].textContent = atFps.max || '--';
        stats['at-fps-p1'].textContent = atFps.p1 || '--';
        stats['at-fps-samples'].textContent = atFps.samples || '--';

        stats['at-ft-avg'].textContent = atFt.avg || '--';
        stats['at-ft-min'].textContent = atFt.min || '--';
        stats['at-ft-max'].textContent = atFt.max || '--';

        // FPS 颜色编码
        this._colorCodeFPS(stats['fps-current'], fps.current);
        this._colorCodeFPS(stats['fps-avg'], fps.avg);
        this._colorCodeFPS(stats['fps-min'], fps.min);
        this._colorCodeFPS(stats['fps-p1'], fps.p1);
        this._colorCodeFPS(stats['at-fps-avg'], atFps.avg);
        this._colorCodeFPS(stats['at-fps-min'], atFps.min);
        this._colorCodeFPS(stats['at-fps-p1'], atFps.p1);

        // 绘制图表
        this._drawChart();
    }

    _colorCodeFPS(el, value) {
        if (!el || !value) return;
        el.classList.remove('profiler-fps-good', 'profiler-fps-ok', 'profiler-fps-bad', 'profiler-fps-critical');
        if (value >= 55) el.classList.add('profiler-fps-good');
        else if (value >= 40) el.classList.add('profiler-fps-ok');
        else if (value >= 25) el.classList.add('profiler-fps-bad');
        else el.classList.add('profiler-fps-critical');
    }

    // ═══════════════════ 图表绘制 ═══════════════════

    _drawChart() {
        const ctx = this._chartCtx;
        const canvas = this._chartCanvas;
        if (!ctx || !canvas) return;

        const history = this.profiler.fpsHistory;
        if (history.length < 2) {
            this._clearChart();
            return;
        }

        // 使用 CSS 逻辑尺寸（ctx 已通过 setTransform 缩放到 dpr）
        const W = parseInt(canvas.style.width) || canvas.width;
        const H = parseInt(canvas.style.height) || canvas.height;
        const padding = { top: 10, right: 10, bottom: 20, left: 35 };
        const plotW = W - padding.left - padding.right;
        const plotH = H - padding.top - padding.bottom;

        ctx.clearRect(0, 0, W, H);

        // 计算 Y 轴范围
        const fpsValues = history.map(h => h.fps);
        const maxFPS = Math.max(Math.ceil(Math.max(...fpsValues) / 10) * 10, 60);
        const minFPS = 0;

        // 背景区域
        ctx.fillStyle = 'rgba(0, 0, 0, 0.02)';
        ctx.fillRect(padding.left, padding.top, plotW, plotH);

        // 参考线
        const refLines = [15, 30, 45, 60];
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.08)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        for (const ref of refLines) {
            if (ref > maxFPS) continue;
            const y = padding.top + plotH - (ref / maxFPS) * plotH;
            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(padding.left + plotW, y);
            ctx.stroke();

            ctx.fillStyle = 'rgba(0, 0, 0, 0.35)';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'right';
            ctx.fillText(ref.toString(), padding.left - 4, y + 3);
        }
        ctx.setLineDash([]);

        // 绘制三条线：当前 FPS、滑动平均、最低
        const timeRange = history[history.length - 1].time - history[0].time;

        // 当前 FPS 线
        this._drawLine(ctx, history, padding, plotW, plotH, maxFPS, timeRange, 'rgba(64, 197, 241, 0.9)', 2);

        // 滑动平均线（窗口 = 10）
        if (history.length > 10) {
            const smoothed = this._movingAverage(history, 10);
            this._drawLine(ctx, smoothed, padding, plotW, plotH, maxFPS, timeRange, 'rgba(76, 175, 80, 0.7)', 1.5);
        }

        // X 轴标签
        ctx.fillStyle = 'rgba(0, 0, 0, 0.35)';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        const totalSec = Math.round(timeRange / 1000);
        ctx.fillText('0s', padding.left, H - 4);
        ctx.fillText(totalSec + 's', padding.left + plotW, H - 4);
    }

    _drawLine(ctx, data, padding, plotW, plotH, maxFPS, timeRange, color, lineWidth) {
        if (data.length < 2) return;
        const startTime = data[0].time;

        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.lineJoin = 'round';
        ctx.beginPath();

        for (let i = 0; i < data.length; i++) {
            const x = padding.left + ((data[i].time - startTime) / timeRange) * plotW;
            const y = padding.top + plotH - (Math.min(data[i].fps, maxFPS) / maxFPS) * plotH;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
    }

    _movingAverage(history, window) {
        const result = [];
        for (let i = 0; i < history.length; i++) {
            const start = Math.max(0, i - window + 1);
            const slice = history.slice(start, i + 1);
            const avg = slice.reduce((s, h) => s + h.fps, 0) / slice.length;
            result.push({ time: history[i].time, fps: avg });
        }
        return result;
    }

    _clearChart() {
        if (this._chartCtx && this._chartCanvas) {
            const W = parseInt(this._chartCanvas.style.width) || this._chartCanvas.width;
            const H = parseInt(this._chartCanvas.style.height) || this._chartCanvas.height;
            this._chartCtx.clearRect(0, 0, W, H);
        }
    }

    _resizeChart() {
        if (!this._chartCanvas) return;
        const container = this._chartCanvas.parentElement;
        if (!container) return;
        const rect = container.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const w = Math.floor(rect.width - 8); // 减去 padding
        const h = 120;
        if (w > 0) {
            this._chartCanvas.width = w * dpr;
            this._chartCanvas.height = h * dpr;
            this._chartCanvas.style.width = w + 'px';
            this._chartCanvas.style.height = h + 'px';
            if (this._chartCtx) {
                this._chartCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
            }
        }
    }

    // ═══════════════════ 模型信息渲染 ═══════════════════

    _renderModelInfo(snap) {
        const el = this._elements.modelInfo;
        const t = (k, fb) => this._t(k, fb);
        if (!snap || !snap.loaded) {
            this._fillPlaceholder(el, t('profiler.placeholder.noModel', '未加载模型'), 'profiler.placeholder.noModel');
            return;
        }

        let html = `<div class="profiler-info-grid">`;
        html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.type','类型')}</span><span class="profiler-info-val">${snap.type?.toUpperCase()}</span></div>`;

        if (snap.name) {
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.name','名称')}</span><span class="profiler-info-val profiler-info-name">${this._escapeHtml(snap.name)}</span></div>`;
        }

        if (snap.geometry) {
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.vertices','顶点数')}</span><span class="profiler-info-val">${this._formatNum(snap.geometry.vertices)}</span></div>`;
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.faces','面数')}</span><span class="profiler-info-val">${this._formatNum(snap.geometry.faces)}</span></div>`;
            if (snap.geometry.morphTargetCount) {
                html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.morphTargets','Morph Targets')}</span><span class="profiler-info-val">${snap.geometry.morphTargetCount}</span></div>`;
            }
        }

        if (snap.materials) {
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.materials','材质数')}</span><span class="profiler-info-val">${snap.materials.count}</span></div>`;
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.textures','纹理数')}</span><span class="profiler-info-val">${snap.materials.textureCount}</span></div>`;
        }

        if (snap.skeleton) {
            const boneLabel = snap.type === 'vrm'
                ? t('profiler.info.bonesVRM', 'Humanoid 骨骼')
                : t('profiler.info.bonesMMD', '骨骼数');
            const boneCount = snap.skeleton.boneCount || snap.skeleton.humanBoneCount || 0;
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${boneLabel}</span><span class="profiler-info-val">${boneCount}</span></div>`;
        }

        if (snap.physics) {
            const enabledTxt = snap.physics.enabled ? t('profiler.info.enabled','启用') : t('profiler.info.disabled','禁用');
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.physicsEngine','物理引擎')}</span><span class="profiler-info-val">${enabledTxt}</span></div>`;
            if (snap.physics.bodyCount !== undefined) {
                html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.physicsBodies','物理刚体')}</span><span class="profiler-info-val">${snap.physics.bodyCount} (K:${snap.physics.kinematicBodies || 0} / D:${snap.physics.dynamicBodies || 0})</span></div>`;
            }
            if (snap.physics.constraintCount !== undefined) {
                html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.physicsConstraints','物理约束')}</span><span class="profiler-info-val">${snap.physics.constraintCount}</span></div>`;
            }
        }

        if (snap.springBones) {
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.springJoints','Spring Joints')}</span><span class="profiler-info-val">${snap.springBones.jointCount}</span></div>`;
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.colliders','Colliders')}</span><span class="profiler-info-val">${snap.springBones.colliderCount}</span></div>`;
        }

        if (snap.animation) {
            const animTxt = snap.animation.isPlaying
                ? t('profiler.info.animPlaying','播放中')
                : (snap.animation.isPaused ? t('profiler.info.animPaused','暂停') : t('profiler.info.animStopped','停止'));
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.animation','动画')}</span><span class="profiler-info-val">${animTxt}</span></div>`;
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.ikGrant','IK / Grant')}</span><span class="profiler-info-val">${snap.animation.hasIK ? '✓' : '✗'} / ${snap.animation.hasGrant ? '✓' : '✗'}</span></div>`;
        }

        if (snap.outlineEffect !== undefined) {
            const outlineTxt = snap.outlineEffect ? t('profiler.info.enabled','启用') : t('profiler.info.disabled','禁用');
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.outline','描边效果')}</span><span class="profiler-info-val">${outlineTxt}</span></div>`;
        }

        if (snap.renderer) {
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.drawCalls','Draw Calls')}</span><span class="profiler-info-val">${snap.renderer.drawCalls}</span></div>`;
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.triangles','三角形')}</span><span class="profiler-info-val">${this._formatNum(snap.renderer.triangles)}</span></div>`;
            html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.pixelRatio','Pixel Ratio')}</span><span class="profiler-info-val">${snap.renderer.pixelRatio}</span></div>`;
            if (snap.renderer.size) {
                html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.renderSize','渲染尺寸')}</span><span class="profiler-info-val">${snap.renderer.size.width}×${snap.renderer.size.height}</span></div>`;
            }
        }

        html += `</div>`;
        el.innerHTML = html;
    }

    _renderAssessment(assessment) {
        const el = this._elements.assessment;
        const t = (k, fb) => this._t(k, fb);
        if (!assessment || assessment.overall === 'N/A') {
            this._fillPlaceholder(el, t('profiler.placeholder.cannotRate', '无法评级'), 'profiler.placeholder.cannotRate');
            return;
        }

        const ratingLabels = {
            'low':     { text: t('profiler.rating.low','轻量'),     cls: 'profiler-rating-low' },
            'medium':  { text: t('profiler.rating.medium','中等'),   cls: 'profiler-rating-medium' },
            'high':    { text: t('profiler.rating.high','较重'),     cls: 'profiler-rating-high' },
            'extreme': { text: t('profiler.rating.extreme','极重'),  cls: 'profiler-rating-extreme' }
        };

        const overall = ratingLabels[assessment.overall] || { text: assessment.overall, cls: '' };

        let html = `<div class="profiler-overall-rating ${overall.cls}">${t('profiler.rating.overall','总评：')}${overall.text}</div>`;
        html += `<div class="profiler-rating-grid">`;

        for (const [key, detail] of Object.entries(assessment.details)) {
            const r = ratingLabels[detail.rating] || { text: detail.rating, cls: '' };
            const label = detail.labelKey ? t(detail.labelKey, detail.label) : detail.label;
            html += `<div class="profiler-rating-item ${r.cls}">
                <span class="profiler-rating-label">${label}</span>
                <span class="profiler-rating-value">${this._formatNum(detail.value)}</span>
                <span class="profiler-rating-tag">${r.text}</span>
            </div>`;
        }

        html += `</div>`;
        html += `<div class="profiler-rating-note">${t('profiler.rating.noteHtml','评级参考 <a href="https://wiki.vrchat.com/wiki/Guides:Avatar_Performance_Ranking" target="_blank" rel="noopener noreferrer" style="color:#40C5F1;">VRChat Avatar Performance Ranking</a> (PC 端)，因 N.E.K.O 为单模型场景已适当放宽。')}</div>`;
        el.innerHTML = html;
    }

    _renderGPUInfo(snap) {
        const el = this._elements.gpuInfo;
        const t = (k, fb) => this._t(k, fb);
        if (!snap?.gpu) {
            this._fillPlaceholder(el, t('profiler.placeholder.cannotGetGpu', '无法获取 GPU 信息'), 'profiler.placeholder.cannotGetGpu');
            return;
        }

        let html = `<div class="profiler-info-grid">`;
        html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.gpu','GPU')}</span><span class="profiler-info-val profiler-info-name">${this._escapeHtml(snap.gpu.renderer)}</span></div>`;
        html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.vendor','厂商')}</span><span class="profiler-info-val">${this._escapeHtml(snap.gpu.vendor)}</span></div>`;
        html += `<div class="profiler-info-row"><span class="profiler-info-key">${t('profiler.info.maxTextureSize','最大纹理尺寸')}</span><span class="profiler-info-val">${snap.gpu.maxTextureSize}</span></div>`;
        html += `</div>`;
        el.innerHTML = html;
    }

    // ═══════════════════ 工具方法 ═══════════════════

    _formatNum(n) {
        if (n === undefined || n === null) return '--';
        if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
        return n.toString();
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}

// 导出为全局变量
if (typeof window !== 'undefined') {
    window.ModelProfilerUI = ModelProfilerUI;
}
