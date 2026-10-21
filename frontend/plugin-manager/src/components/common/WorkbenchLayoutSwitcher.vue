<template>
  <div class="wb-layout-switcher" :data-yui-guide-id="guideId">
    <el-icon class="wb-layout-switcher__icon"><Grid /></el-icon>
    <el-radio-group
      :model-value="layoutMode"
      size="small"
      :aria-label="$t('plugins.layoutLabel')"
      @update:model-value="(v: LayoutMode) => $emit('update:layoutMode', v)"
    >
      <el-radio-button
        v-for="choice in choices"
        :key="choice.value"
        :value="choice.value"
      >
        {{ choice.label }}
      </el-radio-button>
    </el-radio-group>
  </div>
</template>

<script setup lang="ts">
import { Grid } from '@element-plus/icons-vue'
import type { LayoutMode } from '@/composables/useGridWorkbench'
import type { LayoutChoiceDescriptor } from '@/composables/workbenchDescriptors'

defineProps<{
  layoutMode: LayoutMode
  choices: LayoutChoiceDescriptor[]
  guideId?: string
}>()

defineEmits<{
  'update:layoutMode': [value: LayoutMode]
}>()
</script>

<style scoped>
.wb-layout-switcher {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.wb-layout-switcher__icon {
  font-size: 15px;
  color: var(--el-text-color-secondary);
}

.wb-layout-switcher :deep(.el-radio-button__inner) {
  border-radius: var(--radius-chip);
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 500;
  transition:
    background-color var(--motion-duration-normal) var(--motion-ease-standard),
    color var(--motion-duration-normal) var(--motion-ease-standard),
    box-shadow var(--motion-duration-normal) var(--motion-ease-standard);
}

@media (prefers-reduced-motion: reduce) {
  .wb-layout-switcher :deep(.el-radio-button__inner) { transition: none; }
}
</style>
