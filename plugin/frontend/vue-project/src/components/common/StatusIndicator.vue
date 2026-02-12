<template>
  <div class="status-indicator">
    <el-tag
      :type="tagType"
      :effect="effect"
      size="small"
      :class="['status-tag', `status-${status}`]"
    >
      <span class="status-dot" :style="{ backgroundColor: statusColor }"></span>
      {{ statusText }}
    </el-tag>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { STATUS_COLORS, STATUS_TEXT_KEYS, PluginStatus } from '@/utils/constants'

interface Props {
  status: PluginStatus | string
  effect?: 'dark' | 'light' | 'plain'
}

const props = withDefaults(defineProps<Props>(), {
  effect: 'light'
})

const { t } = useI18n()

const statusColor = computed(() => {
  return STATUS_COLORS[props.status as PluginStatus] || STATUS_COLORS[PluginStatus.STOPPED]
})

const statusText = computed(() => {
  return t(STATUS_TEXT_KEYS[props.status as PluginStatus] || 'common.unknown')
})

const tagType = computed(() => {
  switch (props.status) {
    case PluginStatus.RUNNING:
    case PluginStatus.INJECTED:
      return 'success'
    case PluginStatus.STOPPED:
      return 'info'
    case PluginStatus.CRASHED:
      return 'danger'
    case PluginStatus.LOADING:
    case PluginStatus.PENDING:
      return 'warning'
    case PluginStatus.DISABLED:
      return 'info'
    default:
      return 'info'
  }
})
</script>

<style scoped>
.status-indicator {
  display: inline-flex;
  align-items: center;
}

.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
</style>

