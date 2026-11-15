<template>
  <span
    class="run-status"
    :style="{ '--run-status-color': `var(--el-color-${tone})` }"
    :title="status"
  >
    {{ t(runStatusKey(status)) }}
  </span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RUN_STATUS_TONES, runStatusKey } from '@/utils/runDisplay'
const props = defineProps<{ status: string }>()
const { t } = useI18n()
const tone = computed(() =>
  Object.hasOwn(RUN_STATUS_TONES, props.status) ? RUN_STATUS_TONES[props.status] : 'info'
)
</script>
<style scoped>
.run-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: color-mix(in srgb, var(--run-status-color) 40%, var(--el-text-color-primary));
  font-size: 0.75rem;
  line-height: 1.4;
  overflow-wrap: anywhere;
  min-width: 0;
}
.run-status::before {
  content: '';
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: currentColor;
}
:global(html.dark .run-status) {
  color: var(--run-status-color);
}
</style>
