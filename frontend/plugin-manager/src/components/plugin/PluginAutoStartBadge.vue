<template>
  <span class="plugin-start-mode">
    <SwitchButton class="plugin-start-mode__icon" aria-hidden="true" />
    <span>{{ t('plugins.autoStart') }}</span>
  </span>
</template>

<script setup lang="ts">
import { SwitchButton } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<style scoped>
.plugin-start-mode {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  min-height: var(--plugin-card-control-height, 22px);
  padding: 1px 6px;
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 35%, var(--surface-border));
  border-radius: var(--radius-chip);
  background: color-mix(in srgb, var(--el-color-primary) 5%, var(--surface-card));
  color: color-mix(in srgb, var(--el-color-primary) 50%, var(--el-text-color-primary));
  font-size: 0.75rem;
  line-height: 1.4;
}

.plugin-start-mode__icon {
  width: 1em;
  height: 1em;
  flex-shrink: 0;
}

.plugin-start-mode > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
