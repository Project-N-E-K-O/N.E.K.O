<template>
  <Transition
    :css="false"
    @before-enter="onBeforeEnter"
    @enter="onEnter"
    @after-enter="onAfterEnter"
    @before-leave="onBeforeLeave"
    @leave="onLeave"
    @after-leave="onAfterLeave"
  >
    <div v-if="shouldShow" class="metrics-bar">
      <div class="metrics-bar__cells">
        <div class="metrics-cell">
          <el-icon class="metrics-cell__icon" :size="13"><Lightning /></el-icon>
          <span class="metrics-cell__value">{{ cpuDisplay }}</span>
          <span class="metrics-cell__label">CPU</span>
        </div>
        <div class="metrics-cell">
          <el-icon class="metrics-cell__icon" :size="13"><Coin /></el-icon>
          <span class="metrics-cell__value">{{ memDisplay }}</span>
          <span class="metrics-cell__label">{{ t('metrics.memory') }}</span>
        </div>
        <div class="metrics-cell">
          <el-icon class="metrics-cell__icon" :size="13"><Connection /></el-icon>
          <span class="metrics-cell__value">{{ metrics!.num_threads }}</span>
          <span class="metrics-cell__label">{{ t('metrics.threads') }}</span>
        </div>
        <div v-if="metrics!.pending_requests != null" class="metrics-cell">
          <el-icon class="metrics-cell__icon" :size="13"><Message /></el-icon>
          <span class="metrics-cell__value">{{ metrics!.pending_requests }}</span>
          <span class="metrics-cell__label">{{ t('metrics.pendingRequests') }}</span>
        </div>
        <div v-if="metrics!.total_executions != null" class="metrics-cell">
          <el-icon class="metrics-cell__icon" :size="13"><DataAnalysis /></el-icon>
          <span class="metrics-cell__value">{{ metrics!.total_executions }}</span>
          <span class="metrics-cell__label">{{ t('metrics.totalExecutions') }}</span>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { Lightning, Coin, Connection, Message, DataAnalysis } from '@element-plus/icons-vue'
import { useMetricsStore } from '@/stores/metrics'
import type { PluginMetrics } from '@/types/api'

interface Props {
  pluginId: string
  pluginStatus?: string
}

const props = withDefaults(defineProps<Props>(), {
  pluginStatus: 'stopped',
})

const { t } = useI18n()
const metricsStore = useMetricsStore()

const metrics = computed<PluginMetrics | null>(() => {
  return metricsStore.getCurrentMetrics(props.pluginId)
})

const isRunning = computed(() => props.pluginStatus === 'running')
const shouldShow = computed(() => isRunning.value && !!metrics.value)

const cpuDisplay = computed(() => {
  if (!metrics.value) return '—'
  return metrics.value.cpu_percent.toFixed(1) + '%'
})

const memDisplay = computed(() => {
  if (!metrics.value) return '—'
  const mb = metrics.value.memory_mb
  if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB'
  return mb.toFixed(1) + ' MB'
})

// ── JS-driven height transition hooks ─────────────────────────────────

const ENTER_DURATION = 380  // ms — must be >= longest enter transition
const LEAVE_DURATION = 320  // ms — must be >= longest leave transition

function onBeforeEnter(el: Element) {
  const s = (el as HTMLElement).style
  s.overflow = 'hidden'
  s.height = '0'
  s.opacity = '0'
  s.marginTop = '0'
  s.paddingTop = '0'
  s.paddingBottom = '0'
}

function onEnter(el: Element, done: () => void) {
  const s = (el as HTMLElement).style
  void (el as HTMLElement).offsetHeight // force reflow
  const h = (el as HTMLElement).scrollHeight
  s.transition = [
    'height 0.36s cubic-bezier(0.22, 1, 0.36, 1)',
    'opacity 0.3s ease 0.06s',
    'margin-top 0.36s cubic-bezier(0.22, 1, 0.36, 1)',
  ].join(',')
  s.height = h + 'px'
  s.opacity = '1'
  s.marginTop = '10px'
  setTimeout(done, ENTER_DURATION)
}

function onAfterEnter(el: Element) {
  const s = (el as HTMLElement).style
  s.height = ''
  s.overflow = ''
  s.transition = ''
  s.paddingTop = ''
  s.paddingBottom = ''
}

function onBeforeLeave(el: Element) {
  const s = (el as HTMLElement).style
  s.height = (el as HTMLElement).scrollHeight + 'px'
  s.overflow = 'hidden'
  void (el as HTMLElement).offsetHeight // force reflow
}

function onLeave(el: Element, done: () => void) {
  const s = (el as HTMLElement).style
  // Kick off the transition on the next frame so the browser
  // has committed the pinned height from onBeforeLeave.
  requestAnimationFrame(() => {
    s.transition = [
      'height 0.3s cubic-bezier(0.4, 0, 1, 1)',
      'opacity 0.24s ease',
      'margin-top 0.3s cubic-bezier(0.4, 0, 1, 1)',
    ].join(',')
    s.height = '0'
    s.opacity = '0'
    s.marginTop = '0'
  })
  setTimeout(done, LEAVE_DURATION)
}

function onAfterLeave(el: Element) {
  const s = (el as HTMLElement).style
  s.height = ''
  s.overflow = ''
  s.transition = ''
}

// ── Data loading ──────────────────────────────────────────────────────

async function loadMetrics(id: string) {
  if (!id || metricsStore.getCurrentMetrics(id)) return
  try {
    await metricsStore.fetchPluginMetrics(id)
  } catch {
    // silently ignore
  }
}

onMounted(() => {
  if (isRunning.value) {
    void loadMetrics(props.pluginId)
  }
})

watch(() => props.pluginId, (newId) => {
  if (isRunning.value) {
    void loadMetrics(newId)
  }
})
</script>

<style scoped>
.metrics-bar {
  will-change: height, opacity, margin-top;
}

.metrics-bar__cells {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 1px 0; /* prevent margin collapse */
}

.metrics-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--el-fill-color-light) 70%, transparent);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 30%, transparent);
  font-size: 12px;
  line-height: 1;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    transform 0.2s ease;
}

.metrics-cell:hover {
  background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color));
  border-color: color-mix(in srgb, var(--el-color-primary) 20%, var(--el-border-color));
  transform: translateY(-1px);
}

.metrics-cell__icon {
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
}

.metrics-cell__value {
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

.metrics-cell__label {
  color: var(--el-text-color-secondary);
  font-size: 11px;
  max-width: 60px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .metrics-cell__label {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .metrics-bar {
    transition: none !important;
  }
}
</style>
