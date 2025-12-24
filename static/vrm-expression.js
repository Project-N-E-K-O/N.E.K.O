/**
 * VRM 表情模块 - 负责表情管理和设置
 */

class VRMExpression {
    constructor(manager) {
        this.manager = manager;
    }

    /**
     * 设置表情
     */
    setExpression(expressionName, weight) {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) {
            return false;
        }

        const clampedWeight = Math.max(0, Math.min(1, weight));
        const expression = this.manager.currentModel.vrm.expressionManager.expressions[expressionName];
        if (!expression) {
            console.warn(`表情 "${expressionName}" 不存在`);
            return false;
        }
        
        expression.weight = clampedWeight;
        return true;
    }

    /**
     * 获取所有可用表情
     */
    getAvailableExpressions() {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) {
            return [];
        }
        return Object.keys(this.manager.currentModel.vrm.expressionManager.expressions);
    }

    /**
     * 重置所有表情
     */
    resetExpressions() {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) return;
        
        Object.keys(this.manager.currentModel.vrm.expressionManager.expressions).forEach(name => {
            this.setExpression(name, 0);
        });
    }
}

// 导出到全局
window.VRMExpression = VRMExpression;

