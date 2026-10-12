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
            if (!payload || payload.success !== true || !payload.config) return null;

            this.profile = payload.config;
            this.source = 'file';
            this.lastModel = modelName;
            return this.profile;
        }

        _boneNameOfCollider(collider) {
            const parent = collider && collider.parent;
            if (parent && typeof parent.name === 'string' && parent.name) return parent.name;
            const bone = collider && collider.bone;
            if (bone && typeof bone.name === 'string' && bone.name) return bone.name;
            return '';
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

        _geometryKey(shapeName, offset, tail) {
            return (shapeName || '') + '|' + this._vecKey(offset) + '|' + this._vecKey(tail);
        }

        _colliderIndex() {
            const index = new Map();
            const list = this.profile && Array.isArray(this.profile.colliders)
                ? this.profile.colliders
                : [];
            for (let i = 0; i < list.length; i++) {
                const entry = list[i];
                if (!entry || !entry.bone) continue;
                if (!index.has(entry.bone)) index.set(entry.bone, []);
                index.get(entry.bone).push(entry);
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
                    const boneName = chain.bones[b];
                    if (boneName && !index.has(boneName)) index.set(boneName, chain);
                }
            }
            return index;
        }

        applyColliders(springBoneManager) {
            const colliders = springBoneManager && springBoneManager.colliders
                ? Array.from(springBoneManager.colliders)
                : [];
            const index = this._colliderIndex();
            const used = new Map();
            let applied = 0;

            const take = (boneName, entry) => {
                if (!used.has(boneName)) used.set(boneName, new Set());
                used.get(boneName).add(entry);
            };
            const isUsed = (boneName, entry) => {
                const set = used.get(boneName);
                return !!set && set.has(entry);
            };

            for (let i = 0; i < colliders.length; i++) {
                const collider = colliders[i];
                if (!collider || !collider.shape || collider.shape.radius === undefined) continue;

                if (collider._rawOriginalRadius === undefined) {
                    collider._rawOriginalRadius = collider.shape.radius;
                }

                const boneName = this._boneNameOfCollider(collider);
                const entries = boneName ? index.get(boneName) : null;
                let entry = null;

                if (entries) {
                    const shapeName = collider.shape.type || '';
                    const runtimeKey = this._geometryKey(
                        shapeName,
                        collider.shape.offset,
                        collider.shape.tail
                    );
                    for (let k = 0; k < entries.length; k++) {
                        const candidate = entries[k];
                        if (isUsed(boneName, candidate)) continue;
                        if (this._geometryKey(candidate.shape, candidate.offset, candidate.tail) === runtimeKey) {
                            entry = candidate;
                            break;
                        }
                    }
                    if (!entry) {
                        for (let k = 0; k < entries.length; k++) {
                            if (!isUsed(boneName, entries[k])) {
                                entry = entries[k];
                                break;
                            }
                        }
                    }
                    if (entry) take(boneName, entry);
                }

                let target;
                if (entry && typeof entry.radius === 'number' && Number.isFinite(entry.radius) && entry.radius >= 0) {
                    target = entry.radius;
                    applied++;
                } else {
                    target = collider._rawOriginalRadius * DEFAULT_REDUCTION;
                }

                collider.shape.radius = target;
                collider._originalRadius = target;
            }

            this.appliedColliders = applied;
            return applied;
        }

        applyJoints(springBoneManager) {
            const joints = springBoneManager && springBoneManager.joints
                ? Array.from(springBoneManager.joints)
                : [];
            const index = this._chainIndex();
            let applied = 0;

            for (let i = 0; i < joints.length; i++) {
                const joint = joints[i];
                if (!joint || !joint.settings) continue;
                const bone = joint.bone;
                const boneName = bone && bone.name ? bone.name : '';
                const chain = index.get(boneName);
                if (!chain) continue;

                const settings = joint.settings;
                for (let k = 0; k < JOINT_SETTING_KEYS.length; k++) {
                    const map = JOINT_SETTING_KEYS[k];
                    if (typeof chain[map.profile] === 'number') {
                        settings[map.settings] = chain[map.profile];
                    }
                }

                const dir = chain.gravity_dir;
                if (Array.isArray(dir) && dir.length === 3
                    && settings.gravityDir && typeof settings.gravityDir.set === 'function') {
                    settings.gravityDir.set(dir[0], dir[1], dir[2]);
                }

                applied++;
            }

            this.appliedJoints = applied;
            return applied;
        }

        apply(springBoneManager) {
            if (!springBoneManager) {
                return { colliders: 0, joints: 0, source: this.source, reset: false };
            }
            const colliders = this.applyColliders(springBoneManager);
            const joints = this.applyJoints(springBoneManager);
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