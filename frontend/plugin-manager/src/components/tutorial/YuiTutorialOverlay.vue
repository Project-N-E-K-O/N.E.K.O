<template>
  <Teleport to="body">
    <Transition name="yui-tutorial-fade">
      <div v-if="isActive" class="yui-tutorial-overlay">
        <div class="yui-tutorial-card">
          <div class="yui-tutorial-avatar">🐱</div>
          <div class="yui-tutorial-content">
            <h3 class="yui-tutorial-title">{{ t('yuiTutorial.title') }}</h3>
            <p class="yui-tutorial-text">{{ t('yuiTutorial.welcome') }}</p>
            <p class="yui-tutorial-text">{{ t('yuiTutorial.hint') }}</p>
          </div>
          <div class="yui-tutorial-actions">
            <el-button type="primary" @click="handleComplete">
              {{ t('yuiTutorial.complete') }}
            </el-button>
            <el-button @click="handleDismiss">
              {{ t('yuiTutorial.dismiss') }}
            </el-button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useYuiTutorialBridge } from '@/composables/useYuiTutorialBridge'

const { t } = useI18n()
const { isActive, complete, dismiss } = useYuiTutorialBridge()

function handleComplete() {
  complete({ action: 'explored' })
}

function handleDismiss() {
  dismiss()
}
</script>

<style scoped>
.yui-tutorial-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(2px);
}

.yui-tutorial-card {
  max-width: 420px;
  width: 90%;
  background: var(--el-bg-color);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.yui-tutorial-avatar {
  font-size: 48px;
  text-align: center;
  margin-bottom: 12px;
}

.yui-tutorial-content {
  text-align: center;
  margin-bottom: 20px;
}

.yui-tutorial-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 12px 0;
  color: var(--el-text-color-primary);
}

.yui-tutorial-text {
  font-size: 14px;
  color: var(--el-text-color-regular);
  margin: 0 0 8px 0;
  line-height: 1.6;
}

.yui-tutorial-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
}

.yui-tutorial-fade-enter-active,
.yui-tutorial-fade-leave-active {
  transition: opacity 0.3s ease;
}

.yui-tutorial-fade-enter-from,
.yui-tutorial-fade-leave-to {
  opacity: 0;
}
</style>
