(function () {
    'use strict';

    if (window.VrmPhysicsProfile) return;

    const DEFAULT_REDUCTION = 0.5;
    const DEFAULT_TIMEOUT_MS = 4000;
    const JOINT_SETTING_KEYS = [
        { settings: 'stiffness', profile: 'stiffness' },
        { settings: 'dragForce', profile: 'drag_force' },
        { settings: 'gravityPower', profile: 'gravity_power' },
        { settings: 'hitRadius', profile: 'hit_radius' }
    ];

    class VrmPhysicsProfile {
        constructor() {
            this.profile = null;
            this.source = 'default';
            this.appliedColliders = 0;
            this.appliedJoints = 0;
            this.lastModel = '';
            this._requestSeq = 0;
        }

        get hasProfile() {
            return this.profile !== null;
        }

        reset() {
            this.profile = null;
            this.source = 'default';
            this.appliedColliders = 0;
            this.appliedJoints = 0;
            this.lastModel = '';
        }

        async load(modelName, timeoutMs) {
            const seq = ++this._requestSeq;
            this.reset();
            if (!modelName || typeof modelName !== 'string') return null;

            const limit = typeof timeoutMs === 'number' && timeoutMs > 0
                ? timeoutMs
                : DEFAULT_TIMEOUT_MS;

            let payload = null;
            let controller = null;
            let timer = null;
            try {
                const url = '/api/model/vrm/physics_profile/' + encodeURIComponent(modelName);
                const options = { cache: 'no-store' };
                if (typeof AbortController === 'function') {
                    controller = new AbortController();
                    options.signal = controller.signal;
                    timer = setTimeout(() => controller.abort(), limit);
                }
                const response = await fetch(url, options);
                if (!response.ok) return null;
                payload = await response.json();
            } catch (error) {
                payload = null;
            } finally {
                if (timer !== null) clearTimeout(timer);
            }

            if (seq !== this._requestSeq) return null;
            if (!payload || payload.success !== true || !this._isUsableConfig(payload.config)) return null;

            this.profile = payload.config;
            this.source = 'file';
            this.lastModel = modelName;
            return this.profile;
        }

        _isUsableConfig(config) {
            if (!config || typeof config !== 'object' || Array.isArray(config)) return false;
            return Array.isArray(config.colliders) || Array.isArray(config.chains);
        }

        _boneOfCollider(collider) {
            const parent = collider && collider.parent;
            if (parent && typeof parent.name === 'string' && parent.name) return parent;
            const bone = collider && collider.bone;
            if (bone && typeof bone.name === 'string' && bone.name) return bone;
            return null;
        }

        // GLTFLoader sanitizes node.name and appends "_1", "_2"... when two names sanitize to the
        // same string ("hair.001" and "hair001"), but keeps the glTF name in userData.name. The
        // profile is written with those original names, so match them first and only fall back
        // to the sanitized name when the loader did not record the original.
        _boneKey(node) {
            if (!node) return '';
            const raw = node.userData && node.userData.name;
            if (typeof raw === 'string' && raw) return 'raw:' + raw;
            const name = this._sanitizeNodeName(typeof node.name === 'string' ? node.name : '');
            return name ? 'name:' + name : '';
        }

        _addToIndex(index, boneName, item) {
            if (typeof boneName !== 'string' || !boneName) return;
            const keys = ['raw:' + boneName];
            const sanitized = this._sanitizeNodeName(boneName);
            if (sanitized) keys.push('name:' + sanitized);
            for (let i = 0; i < keys.length; i++) {
                if (!index.has(keys[i])) index.set(keys[i], []);
                index.get(keys[i]).push(item);
            }
        }

        _sanitizeNodeName(name) {
            if (typeof name !== 'string' || !name) return '';
            const binding = window.THREE && window.THREE.PropertyBinding;
            if (binding && typeof binding.sanitizeNodeName === 'function') {
                try {
                    return binding.sanitizeNodeName(name);
                } catch (error) {
                    return '';
                }
            }
            return name.replace(/\s/g, '_').replace(/[\[\]\.:\/]/g, '');
        }

        _vecKey(v) {
            if (!v) return '';
            let x;
            let y;
            let z;
            if (Array.isArray(v)) {
                x = v[0];
                y = v[1];
                z = v[2];
            } else {
                x = v.x;
                y = v.y;
                z = v.z;
            }
            if (typeof x !== 'number' || typeof y !== 'number' || typeof z !== 'number') return '';
            const round = (n) => Math.round(n * 1000) / 1000;
            return round(x) + ',' + round(y) + ',' + round(z);
        }

        _offsetKey(offset) {
            return this._vecKey(offset) || '0,0,0';
        }

        // The tuner always writes offset [0,0,0] (a placeholder, not a position), so a zero or
        // missing offset carries no positional information and must not block pairing.
        _hasPositionInfo(entry) {
            const key = this._vecKey(entry && entry.offset);
            return key !== '' && key !== '0,0,0';
        }

        _shapeCompatible(entry, runtimeType) {
            const shape = entry && typeof entry.shape === 'string' ? entry.shape : '';
            return !shape || !runtimeType || shape === runtimeType;
        }

        _colliderIndex() {
            const index = new Map();
            const list = this.profile && Array.isArray(this.profile.colliders)
                ? this.profile.colliders
                : [];
            for (let i = 0; i < list.length; i++) {
                const entry = list[i];
                if (!entry) continue;
                this._addToIndex(index, entry.bone, entry);
            }
            return index;
        }

        _chainIndex() {
            const index = new Map();
            const list = this.profile && Array.isArray(this.profile.chains)
                ? this.profile.chains
                : [];
            for (let i = 0; i < list.length; i++) {
                const chain = list[i];
                if (!chain || !Array.isArray(chain.bones)) continue;
                for (let b = 0; b < chain.bones.length; b++) {
                    this._addToIndex(index, chain.bones[b], chain);
                }
            }
            return index;
        }

        applyColliders(springBoneManager, sceneScale) {
            const scale = typeof sceneScale === 'number' && Number.isFinite(sceneScale) && sceneScale > 0
                ? sceneScale
                : 1;
            const colliders = springBoneManager && springBoneManager.colliders
                ? Array.from(springBoneManager.colliders)
                : [];
            const index = this._colliderIndex();
            const matched = new Array(colliders.length).fill(null);
            const claimed = new Set();
            const runtime = colliders.map((collider) => {
                if (!collider || !collider.shape || collider.shape.radius === undefined) return null;
                const boneKey = this._boneKey(this._boneOfCollider(collider));
                return {
                    entries: boneKey ? index.get(boneKey) : null,
                    type: typeof collider.shape.type === 'string' ? collider.shape.type : '',
                    offsetKey: this._offsetKey(collider.shape.offset),
                    tailKey: this._vecKey(collider.shape.tail)
                };
            });

            const claimFirst = (accepts) => {
                for (let i = 0; i < colliders.length; i++) {
                    const info = runtime[i];
                    if (!info || !info.entries || matched[i]) continue;
                    for (let k = 0; k < info.entries.length; k++) {
                        const entry = info.entries[k];
                        if (claimed.has(entry)) continue;
                        if (!this._shapeCompatible(entry, info.type)) continue;
                        if (!accepts(entry, info)) continue;
                        matched[i] = entry;
                        claimed.add(entry);
                        break;
                    }
                }
            };
            const sameOffset = (entry, info) => this._offsetKey(entry.offset) === info.offsetKey;

            // Pass 1a: bone + shape + offset + capsule tail, so capsules sharing an offset keep
            // their own radii. The tuner writes tails in Unity space, so this pass is a preference.
            claimFirst((entry, info) => sameOffset(entry, info)
                && info.tailKey !== ''
                && this._vecKey(entry.tail) === info.tailKey);
            // Pass 1b: bone + shape + offset (profiles that copy the model's own colliders).
            claimFirst(sameOffset);

            // Pass 2: same bone + same shape, in order, only for entries without a real position.
            // A shape mismatch or a known offset mismatch stays unmatched.
            claimFirst((entry) => !this._hasPositionInfo(entry));

            let applied = 0;
            for (let i = 0; i < colliders.length; i++) {
                const collider = colliders[i];
                if (!runtime[i]) continue;

                if (collider._rawOriginalRadius === undefined) {
                    collider._rawOriginalRadius = collider.shape.radius;
                }

                const entry = matched[i];
                if (entry && typeof entry.radius === 'number' && Number.isFinite(entry.radius) && entry.radius >= 0) {
                    collider._originalRadius = entry.radius;
                    collider.shape.radius = entry.radius * scale;
                    applied++;
                } else {
                    // Same as the no-profile path, so a profile only changes what it matched.
                    const reduced = collider._rawOriginalRadius * DEFAULT_REDUCTION;
                    collider._originalRadius = reduced;
                    collider.shape.radius = reduced;
                }
            }

            this.appliedColliders = applied;
            return applied;
        }

        applyJoints(springBoneManager, sceneScale) {
            const scale = typeof sceneScale === 'number' && Number.isFinite(sceneScale) && sceneScale > 0
                ? sceneScale
                : 1;
            const joints = springBoneManager && springBoneManager.joints
                ? Array.from(springBoneManager.joints)
                : [];
            const index = this._chainIndex();
            const claimed = new Map();
            let applied = 0;

            for (let i = 0; i < joints.length; i++) {
                const joint = joints[i];
                if (!joint || !joint.settings) continue;
                const boneKey = this._boneKey(joint.bone);
                if (!boneKey) continue;
                const chains = index.get(boneKey);
                if (!chains || !chains.length) continue;

                const cursor = claimed.get(boneKey) || 0;
                const chain = chains[cursor < chains.length ? cursor : chains.length - 1];
                claimed.set(boneKey, cursor + 1);
                if (!chain) continue;

                const settings = joint.settings;
                for (let k = 0; k < JOINT_SETTING_KEYS.length; k++) {
                    const map = JOINT_SETTING_KEYS[k];
                    if (!Number.isFinite(chain[map.profile])) continue;
                    if (map.settings === 'hitRadius') {
                        // Unscaled base for setModelScale, like collider._originalRadius.
                        joint._originalHitRadius = chain[map.profile];
                        settings[map.settings] = chain[map.profile] * scale;
                    } else {
                        settings[map.settings] = chain[map.profile];
                    }
                }

                const dir = chain.gravity_dir;
                if (Array.isArray(dir) && dir.length === 3 && dir.every(Number.isFinite)
                    && settings.gravityDir && typeof settings.gravityDir.set === 'function') {
                    settings.gravityDir.set(dir[0], dir[1], dir[2]);
                }

                applied++;
            }

            this.appliedJoints = applied;
            return applied;
        }

        apply(springBoneManager, sceneScale) {
            if (!springBoneManager) {
                return { colliders: 0, joints: 0, source: this.source, reset: false };
            }
            const colliders = this.applyColliders(springBoneManager, sceneScale);
            const joints = this.applyJoints(springBoneManager, sceneScale);
            const reset = this.resetSpringState(springBoneManager);
            return { colliders: colliders, joints: joints, source: this.source, reset: reset };
        }

        resetSpringState(springBoneManager) {
            if (!springBoneManager || typeof springBoneManager.reset !== 'function') return false;
            try {
                springBoneManager.reset();
                return true;
            } catch (error) {
                return false;
            }
        }

        applyDefaultReduction(springBoneManager) {
            const colliders = springBoneManager && springBoneManager.colliders
                ? Array.from(springBoneManager.colliders)
                : [];
            let applied = 0;
            for (let i = 0; i < colliders.length; i++) {
                const collider = colliders[i];
                if (!collider || !collider.shape || collider.shape.radius === undefined) continue;
                if (collider._rawOriginalRadius === undefined) {
                    collider._rawOriginalRadius = collider.shape.radius;
                }
                const reduced = collider._rawOriginalRadius * DEFAULT_REDUCTION;
                collider.shape.radius = reduced;
                collider._originalRadius = reduced;
                applied++;
            }
            this.source = 'default';
            this.appliedColliders = applied;
            this.appliedJoints = 0;
            return applied;
        }
    }

    VrmPhysicsProfile.DEFAULT_REDUCTION = DEFAULT_REDUCTION;
    VrmPhysicsProfile.DEFAULT_TIMEOUT_MS = DEFAULT_TIMEOUT_MS;

    window.VrmPhysicsProfile = VrmPhysicsProfile;
})();