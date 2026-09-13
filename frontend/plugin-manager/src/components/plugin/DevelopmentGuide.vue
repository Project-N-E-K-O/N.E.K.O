<template>
  <el-button class="development-guide-button" :icon="Reading" text @click="visible = true">{{ t('development.guideButton') }}</el-button>
  <el-dialog :model-value="visible" class="neko-development-dialog development-guide-dialog" :title="t('development.guideTitle')" width="min(680px, 92vw)" align-center :close-on-click-modal="false" @update:model-value="setVisible">
    <template #header="{ titleId, titleClass }">
      <div class="development-guide-heading">
        <span class="development-guide-icon"><el-icon><Reading /></el-icon></span>
        <h2 :id="titleId" :class="titleClass">{{ t('development.guideTitle') }}</h2>
      </div>
    </template>
    <div class="development-guide-body">
      <p class="development-guide-intro">{{ t('development.guidePurpose') }}</p>
      <p class="development-guide-intro">{{ t('development.guideIntro') }}</p>
      <ol class="development-guide-steps">
        <li v-for="(step, index) in steps" :key="step.title">
          <span class="development-guide-number">{{ index + 1 }}</span>
          <div><h3>{{ t(step.title) }}</h3><p>{{ t(step.body) }}</p></div>
        </li>
      </ol>
      <div class="development-guide-note"><el-icon><InfoFilled /></el-icon><p>{{ t('development.guideData') }}</p></div>
      <p class="development-guide-reopen">{{ t('development.guideReopen') }}</p>
    </div>
    <template #footer><el-button type="primary" size="large" @click="setVisible(false)">{{ t('development.guideDismiss') }}</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Reading, InfoFilled } from '@element-plus/icons-vue'

const { t } = useI18n()
const visible = ref(false)
const storageKey = 'neko.pluginDevelopment.guide.v1.dismissed'
const steps = [
  { title: 'development.guideLoadTitle', body: 'development.guideLoadBody' },
  { title: 'development.guideReloadTitle', body: 'development.guideReloadBody' },
  { title: 'development.guideBuildTitle', body: 'development.guideBuildBody' },
]

function setVisible(value: boolean) {
  visible.value = value
  if (!value) {
    try { localStorage.setItem(storageKey, '1') } catch { /* A storage restriction must not prevent closing help. */ }
  }
}

onMounted(() => {
  try { visible.value = localStorage.getItem(storageKey) !== '1' }
  catch { visible.value = true }
})
</script>

<style scoped>
.development-guide-button { margin-left: auto !important; }
.development-guide-heading { display: flex; align-items: center; gap: 14px; }
.development-guide-icon { display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: 13px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-size: 23px; flex-shrink: 0; }
.development-guide-heading h2 { margin: 0; font-size: 19px; line-height: 1.4; font-weight: 650; }
.development-guide-body { padding: 22px 28px; }
.development-guide-intro { margin: 0 0 22px; color: var(--el-text-color-regular); line-height: 1.75; }
.development-guide-steps { display: flex; flex-direction: column; gap: 20px; margin: 0; padding: 0; list-style: none; }
.development-guide-steps li { display: flex; align-items: flex-start; gap: 14px; }
.development-guide-number { display: flex; align-items: center; justify-content: center; flex: 0 0 28px; height: 28px; border-radius: 9px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-weight: 650; }
.development-guide-steps h3 { margin: 2px 0 5px; color: var(--el-text-color-primary); font-size: 14px; }
.development-guide-steps p { margin: 0; line-height: 1.7; color: var(--el-text-color-secondary); font-size: 13px; }
.development-guide-note { display: flex; align-items: flex-start; gap: 9px; padding: 13px 15px; margin-top: 22px; border-radius: 10px; background: var(--el-fill-color-light); color: var(--el-text-color-secondary); }
.development-guide-note .el-icon { margin-top: 4px; flex-shrink: 0; color: var(--el-color-primary); }
.development-guide-note p { margin: 0; font-size: 12px; line-height: 1.7; }
.development-guide-reopen { margin: 14px 0 0; font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
@media (max-width: 520px) { .development-guide-body { padding: 20px; } }
</style>
