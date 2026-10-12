(function () {
    'use strict';

    if (window.VrmPhysicsProfile) return;

    const DEFAULT_REDUCTION = 0.5;
    const JOINT_SETTING_KEYS = ['stiffness', 'dragForce', 'gravityPower', 'hitRadius'];

    class VrmPhysicsProfile {
        constructor() {
            this.profile = null;
            this.source = 'default';
            this.appliedColliders = 0;
            this.appliedJoints = 0;
            this.lastModel = '';
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

        async load(modelName) {
            this.reset();
            if (!modelName || typeof modelName !== 'string') return null;
            this.lastModel = modelName;
            try {
                const url = '/api/model/vrm/physics_profile/' + encodeURIComponent(modelName);
                const response = await fetch(url, { cache: 'no-store' });
                if (!response.ok) return null;
                const payload = await response.json();
                if (!payload || payload.success !== true || !payload.config) return null;
                this.profile = payload.config;
                this.source = 'file';
                return this.profile;
            } catch (error) {
                this.profile = null;
                this.source = 'default';
                return null;
            }
        }

        _colliderIndex() {
            const index = new Map();
            const list = this.profile && Array.isArray(this.profile.colliders) ? this.profile.colliders : [];
            for (let i = 0; i < list.length; i++) {
                const entry = list[i];
                if (!entry || !entry.bone) continue;
                if (!index.has(entry.bone)) index.set(entry.bone, entry);
            }
            return index;
        }

        _chainIndex() {
            const index = new Map();
            const list = this.profile && Array.isArray(this.profile.chains) ? this.profile.chains : [];
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
            let applied = 0;

            for (let i = 0; i < colliders.length; i++) {
                const collider = colliders[i];
                if (!collider || !collider.shape || collider.shape.radius === undefined) continue;

                if (collider._rawOriginalRadius === undefined) {
                    collider._rawOriginalRadius = collider.shape.radius;
                }

                const boneName = collider.bone && collider.bone.name ? collider.bone.name : '';
                const entry = index.get(boneName);
                let target;
                if (entry && typeof entry.radius === 'number' && entry.radius > 0) {
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
                    const key = JOINT_SETTING_KEYS[k];
                    const profileKey = key === 'dragForce' ? 'drag_force'
                        : key === 'gravityPower' ? 'gravity_power'
                        : key === 'hitRadius' ? 'hit_radius'
                        : key;
                    if (typeof chain[profileKey] === 'number') settings[key] = chain[profileKey];
                }

                const dir = chain.gravity_dir;
                if (Array.isArray(dir) && dir.length === 3 && settings.gravityDir && typeof settings.gravityDir.set === 'function') {
                    settings.gravityDir.set(dir[0], dir[1], dir[2]);
                }

                applied++;
            }

            this.appliedJoints = applied;
            return applied;
        }

        apply(springBoneManager) {
            if (!springBoneManager) return { colliders: 0, joints: 0, source: this.source };
            const colliders = this.applyColliders(springBoneManager);
            const joints = this.applyJoints(springBoneManager);
            return { colliders: colliders, joints: joints, source: this.source };
        }
    }

    VrmPhysicsProfile.DEFAULT_REDUCTION = DEFAULT_REDUCTION;

    window.VrmPhysicsProfile = VrmPhysicsProfile;
})();
