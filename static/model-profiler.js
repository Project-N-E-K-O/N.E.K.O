/**
 * Model Profiler - 模型性能分析器（核心模块）
 * 
 * 模块化设计：纯逻辑，不依赖 UI，可在任何页面/上下文中调用。
 * 支持 MMD 和 VRM 模型的性能数据采集与分析。
 * 
 * 用法：
 *   const profiler = new ModelProfiler();
 *   profiler.start(renderer);          // 开始采集 FPS
 *   profiler.stop();                   // 停止采集
 *   const snapshot = profiler.snapshot(manager); // 获取模型属性快照
 *   const report = profiler.getReport();         // 获取完整报告
 */

class ModelProfiler {
    /** @param {{ historySize?: number, sampleInterval?: number, warmupMs?: number }} opts */
    constructor(opts = {}) {
        // FPS 历史记录长度（默认 300 个采样点，配合 200ms 间隔 ≈ 60 秒窗口）
        this.historySize = opts.historySize || 300;
        // 采样间隔（ms），下限 200ms（见 _tick 中的 Math.max 保护）
        this.sampleInterval = opts.sampleInterval || 200;
        // Warmup 期（ms）：启动后丢弃前 N 毫秒的数据，避免冷启动低帧污染统计
        this.warmupMs = opts.warmupMs || 1500;

        // ── FPS 采集状态 ──
        this._running = false;
        this._rafId = null;
        this._lastTime = 0;
        this._lastSampleTime = 0;
        this._frameCount = 0;
        this._warmupDone = false;

        // FPS 数据
        this.fpsHistory = [];       // { time, fps } 时间序列
        this.frameTimes = [];       // 每帧耗时 (ms)
        this._sessionStart = 0;
        this._sessionFrames = 0;

        // 渲染器引用（用于读取 drawcall 等 GPU 指标）
        this._renderer = null;

        // 模型快照缓存
        this._lastSnapshot = null;
    }

    // ═══════════════════ FPS 采集 ═══════════════════

    /**
     * 开始 FPS 采集
     * @param {THREE.WebGLRenderer} [renderer] - 可选，传入后可采集 GPU 指标
     */
    start(renderer) {
        if (this._running) return;
        this._running = true;
        this._renderer = renderer || null;
        this._lastTime = performance.now();
        this._lastSampleTime = this._lastTime;
        this._sessionStart = this._lastTime;
        this._sessionFrames = 0;
        this._frameCount = 0;
        this._warmupDone = false;
        this.fpsHistory = [];
        this.frameTimes = [];
        this._tick();
    }

    /** 停止 FPS 采集 */
    stop() {
        this._running = false;
        if (this._rafId) {
            cancelAnimationFrame(this._rafId);
            this._rafId = null;
        }
    }

    /** @returns {boolean} 是否正在采集 */
    get isRunning() { return this._running; }

    _tick() {
        if (!this._running) return;
        this._rafId = requestAnimationFrame(() => this._tick());

        const now = performance.now();
        const dt = now - this._lastTime;
        this._lastTime = now;
        this._sessionFrames++;

        // Warmup 期：丢弃数据，只更新计时器
        if (!this._warmupDone) {
            if (now - this._sessionStart < this.warmupMs) {
                this._frameCount = 0;
                this._lastSampleTime = now;
                return;
            }
            this._warmupDone = true;
            // 重置起点，让图表从 warmup 结束后开始
            this._sessionStart = now;
            this._lastSampleTime = now;
            this._frameCount = 0;
            // 初始化全程统计
            this._allTimeMin = Infinity;
            this._allTimeMax = -Infinity;
            this._allTimeFpsSum = 0;
            this._allTimeFpsCount = 0;
            this._allTimeFtMin = Infinity;
            this._allTimeFtMax = -Infinity;
            this._allTimeFtSum = 0;
            this._allTimeFtCount = 0;
            this._allTimeFpsSorted = [];
        }

        // 记录帧时间
        if (dt > 0 && dt < 1000) {
            this.frameTimes.push(dt);
            if (this.frameTimes.length > this.historySize) {
                this.frameTimes.shift();
            }
            // 全程帧时间统计
            if (dt < this._allTimeFtMin) this._allTimeFtMin = dt;
            if (dt > this._allTimeFtMax) this._allTimeFtMax = dt;
            this._allTimeFtSum += dt;
            this._allTimeFtCount++;
        } else if (dt >= 1000) {
            // 后台标签页切回来，丢弃本次采样窗口，重置计数
            this._frameCount = 0;
            this._lastSampleTime = now;
            return;
        }

        // 按采样间隔记录 FPS
        this._frameCount++;
        const sinceSample = now - this._lastSampleTime;
        if (sinceSample >= Math.max(this.sampleInterval, 200)) {
            const fps = (this._frameCount / sinceSample) * 1000;
            const roundedFps = Math.round(fps * 10) / 10;
            this.fpsHistory.push({
                time: now - this._sessionStart,
                fps: roundedFps
            });
            if (this.fpsHistory.length > this.historySize) {
                this.fpsHistory.shift();
            }
            // 全程 FPS 统计
            if (roundedFps < this._allTimeMin) this._allTimeMin = roundedFps;
            if (roundedFps > this._allTimeMax) this._allTimeMax = roundedFps;
            this._allTimeFpsSum += roundedFps;
            this._allTimeFpsCount++;
            // 维护全程排序数组（用于百分位数）
            const arr = this._allTimeFpsSorted;
            let lo = 0, hi = arr.length;
            while (lo < hi) {
                const mid = (lo + hi) >> 1;
                if (arr[mid] < roundedFps) lo = mid + 1;
                else hi = mid;
            }
            arr.splice(lo, 0, roundedFps);

            this._frameCount = 0;
            this._lastSampleTime = now;
        }
    }

    // ═══════════════════ FPS 统计 ═══════════════════

    /** 获取 FPS 统计摘要 */
    getFPSStats() {
        if (this.fpsHistory.length === 0) {
            return { current: 0, avg: 0, min: 0, max: 0, p1: 0, p5: 0, jitter: 0, samples: 0 };
        }

        const values = this.fpsHistory.map(h => h.fps);
        const sorted = [...values].sort((a, b) => a - b);
        const len = sorted.length;

        const sum = values.reduce((a, b) => a + b, 0);
        const avg = sum / len;

        // 百分位数
        const p1 = sorted[Math.floor(len * 0.01)] || sorted[0];
        const p5 = sorted[Math.floor(len * 0.05)] || sorted[0];

        // 帧时间抖动（标准差）
        let jitter = 0;
        if (this.frameTimes.length > 1) {
            const ftAvg = this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length;
            const variance = this.frameTimes.reduce((sum, ft) => sum + (ft - ftAvg) ** 2, 0) / this.frameTimes.length;
            jitter = Math.sqrt(variance);
        }

        return {
            current: values[values.length - 1],
            avg: Math.round(avg * 10) / 10,
            min: sorted[0],
            max: sorted[len - 1],
            p1: Math.round(p1 * 10) / 10,
            p5: Math.round(p5 * 10) / 10,
            jitter: Math.round(jitter * 100) / 100,
            samples: len
        };
    }

    /** 获取帧时间统计 */
    getFrameTimeStats() {
        if (this.frameTimes.length === 0) {
            return { avg: 0, min: 0, max: 0, p95: 0, p99: 0 };
        }
        const sorted = [...this.frameTimes].sort((a, b) => a - b);
        const len = sorted.length;
        const avg = this.frameTimes.reduce((a, b) => a + b, 0) / len;

        return {
            avg: Math.round(avg * 100) / 100,
            min: Math.round(sorted[0] * 100) / 100,
            max: Math.round(sorted[len - 1] * 100) / 100,
            p95: Math.round((sorted[Math.floor(len * 0.95)] || sorted[len - 1]) * 100) / 100,
            p99: Math.round((sorted[Math.floor(len * 0.99)] || sorted[len - 1]) * 100) / 100
        };
    }

    /** 获取全程 FPS 统计（从 warmup 结束到现在的所有数据） */
    getAllTimeFPSStats() {
        if (!this._allTimeFpsCount) {
            return { avg: 0, min: 0, max: 0, p1: 0, p5: 0, samples: 0 };
        }
        const arr = this._allTimeFpsSorted;
        const len = arr.length;
        return {
            avg: Math.round((this._allTimeFpsSum / this._allTimeFpsCount) * 10) / 10,
            min: arr[0],
            max: arr[len - 1],
            p1: Math.round((arr[Math.floor(len * 0.01)] || arr[0]) * 10) / 10,
            p5: Math.round((arr[Math.floor(len * 0.05)] || arr[0]) * 10) / 10,
            samples: this._allTimeFpsCount
        };
    }

    /** 获取全程帧时间统计 */
    getAllTimeFrameTimeStats() {
        if (!this._allTimeFtCount) {
            return { avg: 0, min: 0, max: 0 };
        }
        return {
            avg: Math.round((this._allTimeFtSum / this._allTimeFtCount) * 100) / 100,
            min: Math.round(this._allTimeFtMin * 100) / 100,
            max: Math.round(this._allTimeFtMax * 100) / 100
        };
    }

    // ═══════════════════ 模型属性快照 ═══════════════════

    /**
     * 获取当前模型的属性快照
     * @param {MMDManager|VRMManager} manager - 模型管理器实例
     * @returns {object} 模型属性快照
     */
    snapshot(manager) {
        if (!manager) return null;

        // 检测模型类型
        const isMMD = manager.constructor?.name === 'MMDManager' || !!manager.core;
        const isVRM = manager.constructor?.name === 'VRMManager' || !!manager._cursorFollow;

        let snap;
        if (isMMD) {
            snap = this._snapshotMMD(manager);
        } else if (isVRM) {
            snap = this._snapshotVRM(manager);
        } else {
            snap = { type: 'unknown', error: '无法识别的管理器类型' };
        }

        // 通用渲染器信息
        const renderer = manager.renderer;
        if (renderer) {
            const info = renderer.info;
            snap.renderer = {
                drawCalls: info?.render?.calls || 0,
                triangles: info?.render?.triangles || 0,
                points: info?.render?.points || 0,
                lines: info?.render?.lines || 0,
                textures: info?.memory?.textures || 0,
                geometries: info?.memory?.geometries || 0,
                programs: info?.programs?.length || 0,
                pixelRatio: renderer.getPixelRatio?.() || 1,
                size: (() => {
                    const V2 = window.THREE?.Vector2;
                    if (!V2) return null;
                    const s = renderer.getSize?.(new V2());
                    return s ? { width: s.x || s.width, height: s.y || s.height } : null;
                })()
            };
        }

        // GPU 信息
        if (renderer) {
            const gl = renderer.getContext?.();
            if (gl) {
                const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                snap.gpu = {
                    vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
                    renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
                    maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
                    maxVertexAttribs: gl.getParameter(gl.MAX_VERTEX_ATTRIBS)
                };
            }
        }

        snap.timestamp = Date.now();
        this._lastSnapshot = snap;
        return snap;
    }

    _snapshotMMD(manager) {
        const snap = { type: 'mmd' };
        const model = manager.currentModel;
        if (!model) {
            snap.loaded = false;
            return snap;
        }
        snap.loaded = true;
        snap.name = model.name || model.url || '(unknown)';

        // Mesh 几何信息
        const mesh = model.mesh;
        if (mesh) {
            const geom = mesh.geometry;
            snap.geometry = {
                vertices: geom?.attributes?.position?.count || 0,
                faces: geom?.index
                    ? Math.floor(geom.index.count / 3)
                    : (geom?.attributes?.position ? Math.floor(geom.attributes.position.count / 3) : 0),
                hasNormals: !!geom?.attributes?.normal,
                hasUV: !!geom?.attributes?.uv,
                hasMorphTargets: !!(geom?.morphAttributes && Object.keys(geom.morphAttributes).length > 0),
                morphTargetCount: geom?.morphAttributes?.position?.length || 0
            };

            // 材质信息
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
            snap.materials = {
                count: mats.length,
                textureCount: 0,
                types: {}
            };
            let texCount = 0;
            const texProps = ['map', 'matcap', 'gradientMap', 'emissiveMap', 'specularMap', 'normalMap', 'bumpMap', 'alphaMap'];
            for (const mat of mats) {
                if (!mat) continue;
                const typeName = mat.type || mat.constructor?.name || 'unknown';
                snap.materials.types[typeName] = (snap.materials.types[typeName] || 0) + 1;
                for (const prop of texProps) {
                    if (mat[prop]) texCount++;
                }
            }
            snap.materials.textureCount = texCount;

            // 骨骼信息
            if (mesh.skeleton) {
                snap.skeleton = {
                    boneCount: mesh.skeleton.bones?.length || 0
                };
            }
        }

        // 物理信息
        if (model.physics) {
            try {
                const phys = typeof model.physics.getPhysics === 'function'
                    ? model.physics.getPhysics() : model.physics;
                snap.physics = {
                    enabled: manager.enablePhysics !== false,
                    bodyCount: phys?.bodies?.length || 0,
                    constraintCount: phys?.constraints?.length || 0,
                    kinematicBodies: 0,
                    dynamicBodies: 0
                };
                if (phys?.bodies) {
                    for (const b of phys.bodies) {
                        if (b.params?.physicsMode === 0) snap.physics.kinematicBodies++;
                        else snap.physics.dynamicBodies++;
                    }
                }
            } catch (e) {
                snap.physics = { enabled: manager.enablePhysics !== false, error: e.message };
            }
        } else {
            snap.physics = { enabled: false, bodyCount: 0 };
        }

        // 动画信息
        if (manager.animationModule) {
            snap.animation = {
                isPlaying: !!manager.animationModule.isPlaying,
                isPaused: !!manager.animationModule.isPaused,
                hasIK: !!manager.animationModule.ikSolver,
                hasGrant: !!manager.animationModule.grantSolver
            };
        }

        // 描边效果
        snap.outlineEffect = !!manager.useOutlineEffect;

        return snap;
    }

    _snapshotVRM(manager) {
        const snap = { type: 'vrm' };
        const model = manager.currentModel;
        if (!model) {
            snap.loaded = false;
            return snap;
        }
        snap.loaded = true;

        // VRM 模型信息
        const vrm = model;
        const scene = vrm.scene || vrm;

        // 遍历场景统计几何信息
        let totalVertices = 0;
        let totalFaces = 0;
        let materialSet = new Set();
        let textureCount = 0;
        const texProps = ['map', 'matcap', 'emissiveMap', 'normalMap', 'bumpMap', 'alphaMap', 'shadeMultiplyTexture'];

        scene.traverse?.((child) => {
            if (child.isMesh) {
                const geom = child.geometry;
                if (geom) {
                    totalVertices += geom.attributes?.position?.count || 0;
                    totalFaces += geom.index
                        ? Math.floor(geom.index.count / 3)
                        : (geom.attributes?.position ? Math.floor(geom.attributes.position.count / 3) : 0);
                }
                const mats = Array.isArray(child.material) ? child.material : [child.material];
                for (const mat of mats) {
                    if (!mat) continue;
                    if (!materialSet.has(mat.uuid)) {
                        materialSet.add(mat.uuid);
                        for (const prop of texProps) {
                            if (mat[prop]) textureCount++;
                        }
                    }
                }
            }
        });

        snap.geometry = { vertices: totalVertices, faces: totalFaces };
        snap.materials = { count: materialSet.size, textureCount };

        // Spring Bones（VRM 物理）
        if (vrm.springBoneManager) {
            const joints = vrm.springBoneManager.joints || [];
            const colliders = vrm.springBoneManager.colliders || [];
            snap.springBones = {
                jointCount: joints.length,
                colliderCount: colliders.length
            };
        }

        // Humanoid 骨骼
        if (vrm.humanoid) {
            const bones = vrm.humanoid.humanBones || vrm.humanoid._rawHumanBones;
            snap.skeleton = {
                humanBoneCount: bones ? Object.keys(bones).length : 0
            };
        }

        // 表情
        if (vrm.expressionManager) {
            snap.expressions = {
                count: vrm.expressionManager.expressions?.length ||
                       Object.keys(vrm.expressionManager._expressionMap || {}).length || 0
            };
        }

        return snap;
    }

    // ═══════════════════ 性能评级 ═══════════════════

    /**
     * 根据模型属性给出性能影响评级
     * 
     * 评级体系参考 VRChat Avatar Performance Ranking (PC 端阈值)：
     * https://docs.vrchat.com/docs/avatar-performance-ranking-system
     * 
     * VRChat 是多人场景（同时渲染数十个 avatar），阈值偏严格。
     * N.E.K.O 是单模型桌宠，性能压力远小于 VRChat，因此整体右移一档。
     * 
     * VRChat PC 参考值（Excellent / Good / Medium / Poor）：
     *   Triangles:          32K  / 70K  / 70K  / 70K
     *   Texture Memory:     40MB / 75MB / 110MB/ 150MB
     *   Material Slots:     4    / 8    / 16   / 32
     *   Bones:              75   / 150  / 256  / 400
     *   PhysBone Transforms:16   / 64   / 128  / 256
     *   PhysBone Components:4    / 8    / 16   / 32
     *   Constraint Count:   100  / 250  / 300  / 350
     *   Physics Rigidbodies:0    / 1    / 8    / 8
     * 
     * 转化规则：
     *   - 直接对应的指标（三角形、材质、骨骼）：右移一档（VRChat Good → 我们的轻量）
     *   - 概念映射的指标（物理刚体 → PhysBone Transforms）：找最接近的 VRChat 指标
     *   - 单位转换的指标（纹理数 vs Texture Memory）：按平均纹理大小估算
     *   - 详细转化对照表见：开发记录/001-模型性能分析器.md
     * 
     * @param {object} snapshot - snapshot() 返回的快照
     * @returns {object} 各维度的评级和总评
     */
    assess(snapshot) {
        if (!snapshot || !snapshot.loaded) {
            return { overall: 'N/A', details: {} };
        }

        const details = {};

        // 顶点数/三角形评级
        // VRChat: Excellent ≤32K, Good ≤70K, Very Poor >70K
        // N.E.K.O 单模型放宽约 1.5-2x
        const verts = snapshot.geometry?.vertices || 0;
        details.vertices = {
            value: verts,
            rating: verts <= 50000 ? 'low' : verts <= 100000 ? 'medium' : verts <= 200000 ? 'high' : 'extreme',
            label: '顶点数'
        };

        const faces = snapshot.geometry?.faces || 0;
        details.faces = {
            value: faces,
            rating: faces <= 32000 ? 'low' : faces <= 70000 ? 'medium' : faces <= 140000 ? 'high' : 'extreme',
            label: '面数(三角形)'
        };

        // 材质数评级
        // VRChat: Excellent ≤4, Good ≤8, Medium ≤16, Poor ≤32
        // N.E.K.O 单模型放宽
        const matCount = snapshot.materials?.count || 0;
        details.materials = {
            value: matCount,
            rating: matCount <= 8 ? 'low' : matCount <= 16 ? 'medium' : matCount <= 32 ? 'high' : 'extreme',
            label: '材质数'
        };

        // 纹理数评级（VRChat 用纹理内存衡量，这里用数量近似）
        const texCount = snapshot.materials?.textureCount || 0;
        details.textures = {
            value: texCount,
            rating: texCount <= 10 ? 'low' : texCount <= 25 ? 'medium' : texCount <= 50 ? 'high' : 'extreme',
            label: '纹理数'
        };

        // 骨骼数评级
        // VRChat: Excellent ≤75, Good ≤150, Medium ≤256, Poor ≤400
        // MMD 基础骨骼（センター、上半身、足、指等）约 70-100 个，是所有模型的固定开销
        // 因此在 VRChat 基础上额外加 ~100 的底数
        const boneCount = snapshot.skeleton?.boneCount || snapshot.skeleton?.humanBoneCount || 0;
        details.bones = {
            value: boneCount,
            rating: boneCount <= 200 ? 'low' : boneCount <= 350 ? 'medium' : boneCount <= 500 ? 'high' : 'extreme',
            label: '骨骼数'
        };

        // 物理刚体评级（MMD）
        // VRChat PhysBone Components: Excellent ≤4, Good ≤8, Medium ≤16, Poor ≤32
        // VRChat PhysBone Transforms: Excellent ≤16, Good ≤64, Medium ≤128, Poor ≤256
        // MMD 刚体数量通常远多于 VRChat PhysBone 组件数，更接近 Transforms 指标
        if (snapshot.physics) {
            const bodyCount = snapshot.physics.bodyCount || 0;
            details.physicsBodies = {
                value: bodyCount,
                rating: bodyCount <= 64 ? 'low' : bodyCount <= 128 ? 'medium' : bodyCount <= 256 ? 'high' : 'extreme',
                label: '物理刚体'
            };
            details.physicsConstraints = {
                value: snapshot.physics.constraintCount || 0,
                rating: (snapshot.physics.constraintCount || 0) <= 64 ? 'low' :
                        (snapshot.physics.constraintCount || 0) <= 128 ? 'medium' :
                        (snapshot.physics.constraintCount || 0) <= 256 ? 'high' : 'extreme',
                label: '物理约束'
            };
        }

        // Spring Bones 评级（VRM）
        if (snapshot.springBones) {
            details.springJoints = {
                value: snapshot.springBones.jointCount || 0,
                rating: (snapshot.springBones.jointCount || 0) <= 64 ? 'low' :
                        (snapshot.springBones.jointCount || 0) <= 128 ? 'medium' :
                        (snapshot.springBones.jointCount || 0) <= 256 ? 'high' : 'extreme',
                label: 'Spring Bone 关节'
            };
        }

        // Morph Targets 评级
        if (snapshot.geometry?.morphTargetCount) {
            details.morphTargets = {
                value: snapshot.geometry.morphTargetCount,
                rating: snapshot.geometry.morphTargetCount <= 20 ? 'low' :
                        snapshot.geometry.morphTargetCount <= 50 ? 'medium' : 'high',
                label: 'Morph Targets'
            };
        }

        // Draw Calls 评级
        // VRChat 基准: ~2ms per 1000 draw calls
        if (snapshot.renderer) {
            details.drawCalls = {
                value: snapshot.renderer.drawCalls,
                rating: snapshot.renderer.drawCalls <= 8 ? 'low' :
                        snapshot.renderer.drawCalls <= 16 ? 'medium' :
                        snapshot.renderer.drawCalls <= 32 ? 'high' : 'extreme',
                label: 'Draw Calls'
            };
        }

        // 总评：取最高的 rating
        const ratingOrder = { 'low': 0, 'medium': 1, 'high': 2, 'extreme': 3 };
        let maxRating = 'low';
        for (const d of Object.values(details)) {
            if (ratingOrder[d.rating] > ratingOrder[maxRating]) {
                maxRating = d.rating;
            }
        }

        return { overall: maxRating, details };
    }

    // ═══════════════════ 完整报告 ═══════════════════

    /**
     * 获取完整性能报告（FPS + 模型属性 + 评级）
     * @param {MMDManager|VRMManager} [manager] - 可选，传入则刷新快照
     * @returns {object}
     */
    getReport(manager) {
        const snap = manager ? this.snapshot(manager) : this._lastSnapshot;
        return {
            fps: this.getFPSStats(),
            frameTime: this.getFrameTimeStats(),
            model: snap,
            assessment: snap ? this.assess(snap) : null,
            fpsHistory: [...this.fpsHistory],
            sessionDuration: this._running ? (performance.now() - this._sessionStart) / 1000 : 0
        };
    }

    /** 重置所有数据（如果正在采集，重新触发 warmup） */
    reset() {
        this.fpsHistory = [];
        this.frameTimes = [];
        this._frameCount = 0;
        this._sessionFrames = 0;
        this._lastSnapshot = null;
        // 清理全程统计
        this._allTimeMin = Infinity;
        this._allTimeMax = -Infinity;
        this._allTimeFpsSum = 0;
        this._allTimeFpsCount = 0;
        this._allTimeFtMin = Infinity;
        this._allTimeFtMax = -Infinity;
        this._allTimeFtSum = 0;
        this._allTimeFtCount = 0;
        this._allTimeFpsSorted = [];
        // 重置 warmup，防止重置后冷启动低帧污染
        if (this._running) {
            this._warmupDone = false;
            this._sessionStart = performance.now();
            this._lastSampleTime = this._sessionStart;
            this._lastTime = this._sessionStart;
        }
    }

    /** 销毁 */
    dispose() {
        this.stop();
        this.reset();
        this._renderer = null;
    }
}

// 导出为全局变量（兼容非模块环境）
if (typeof window !== 'undefined') {
    window.ModelProfiler = ModelProfiler;
}
