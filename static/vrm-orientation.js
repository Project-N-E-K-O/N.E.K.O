/**
 * VRM 模型朝向检测和处理模块
 * 在模型加载后、渲染前检测并修正模型朝向
 */
const THREE = (typeof window !== 'undefined' && window.THREE) || (typeof globalThis !== 'undefined' && globalThis.THREE) || null;
class VRMOrientationDetector {
    /**
     * 检测VRM模型是否需要旋转（是否背对屏幕）
     * @param {Object} vrm - VRM模型实例
     * @returns {boolean} 如果需要旋转180度返回true，否则返回false
     */
    static detectNeedsRotation(vrm) {
        if (!THREE) {
            console.warn('THREE.js 未加载，无法检测模型朝向');
            return false;
        }
        if (!vrm || !vrm.humanoid || !vrm.humanoid.humanBones) {
            return false;
        }

        const headBone = vrm.humanoid.humanBones.head?.node;
        const chestBone = vrm.humanoid.humanBones.chest?.node ||
                         vrm.humanoid.humanBones.spine?.node;

        if (!headBone || !chestBone) {
            return false;
        }

        // 确保骨骼的世界矩阵已更新
        if (vrm.scene) {
            vrm.scene.updateMatrixWorld(true);
        }

        const headWorldPos = new THREE.Vector3();
        const chestWorldPos = new THREE.Vector3();
        headBone.getWorldPosition(headWorldPos);
        chestBone.getWorldPosition(chestWorldPos);

        const spineVec = new THREE.Vector3().subVectors(headWorldPos, chestWorldPos);
        spineVec.normalize();

        let needsRotation = false;
        
        if (spineVec.z > 0.05) {
            needsRotation = true;
        } else if (spineVec.z < -0.05) {
            needsRotation = false;
        } else {
            if (headWorldPos.z > 0.1) {
                needsRotation = true;
            } else {
                needsRotation = false;
            }
        }
        
        return needsRotation;
    }

    /**
     * 检测并处理模型朝向
     * @param {Object} vrm - VRM模型实例
     * @param {Object} savedRotation - 已保存的旋转信息（如果有）
     * @returns {Object} 返回处理后的旋转信息 {x, y, z}
     */
    static detectAndFixOrientation(vrm, savedRotation = null) {
        if (savedRotation && 
            Number.isFinite(savedRotation.x) && 
            Number.isFinite(savedRotation.y) && 
            Number.isFinite(savedRotation.z)) {
            return {
                x: savedRotation.x,
                y: savedRotation.y,
                z: savedRotation.z
            };
        }

        const needsRotation = this.detectNeedsRotation(vrm);

        return {
            x: 0,
            y: needsRotation ? Math.PI : 0,
            z: 0
        };
    }

    /**
     * 应用旋转到模型场景
     * @param {Object} vrm - VRM模型实例
     * @param {Object} rotation - 旋转信息 {x, y, z}
     */
    static applyRotation(vrm, rotation) {
        if (!vrm || !vrm.scene) {
            return;
        }

        if (Number.isFinite(rotation.x) && 
            Number.isFinite(rotation.y) && 
            Number.isFinite(rotation.z)) {
            vrm.scene.rotation.set(rotation.x, rotation.y, rotation.z);
            vrm.scene.updateMatrixWorld(true);
        }
    }
}

window.VRMOrientationDetector = VRMOrientationDetector;
