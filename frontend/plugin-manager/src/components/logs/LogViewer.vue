<template>
  <div class="log-viewer">
    <div class="toolbar">
      <el-select v-model="levelFilter" class="toolbar-item level-select" :placeholder="$t('logs.allLevels')" clearable>
        <el-option :label="$t('logs.allLevels')" value="" />
        <el-option v-for="level in levels" :key="level" :label="$t(`logLevel.${level}`)" :value="level" />
      </el-select>

      <el-input
        v-model="search"
        class="toolbar-item search-input"
        clearable
        :placeholder="$t('logs.search')"
        @keyup.enter="refreshLogs"
      />

      <el-input-number v-model="lines" class="toolbar-item lines-input" :min="50" :max="5000" :step="50" />

      <el-button :loading="loading" @click="refreshLogs">{{ $t('common.refresh') }}</el-button>

      <el-switch v-model="autoScroll" :active-text="$t('logs.autoScroll')" />
    </div>

    <div class="meta-row">
      <el-space wrap>
        <el-tag size="small" :type="isConnected ? 'success' : 'warning'">
          {{ isConnected ? $t('logs.connected') : $t('logs.disconnected') }}
        </el-tag>
        <span class="meta-text">{{ $t('logs.totalLogs', { count: filteredLogs.length }) }}</span>
        <span v-if="logFileInfo?.log_file" class="meta-text">{{ $t('logs.logFile') }}: {{ logFileInfo.log_file }}</span>
        <span v-if="typeof logFileInfo?.total_lines === 'number'" class="meta-text">{{ $t('logs.totalLines') }}: {{ logFileInfo.total_lines }}</span>
        <span v-if="typeof logFileInfo?.returned_lines === 'number'" class="meta-text">{{ $t('logs.returnedLines') }}: {{ logFileInfo.returned_lines }}</span>
      </el-space>
    </div>

    <el-alert
      v-if="effectiveError"
      class="error-alert"
      type="warning"
      show-icon
      :closable="false"
      :title="$t('logs.loadError', { error: effectiveError })"
    />

    <div ref="logContainerRef" class="log-list">
      <template v-if="filteredLogs.length > 0">
        <div v-for="(log, index) in filteredLogs" :key="`${log.timestamp}-${index}`" class="log-item">
          <span class="log-time">{{ formatTimestamp(log.timestamp) }}</span>
          <el-tag size="small" :type="levelTagType(log.level)" class="log-level">{{ log.level || 'UNKNOWN' }}</el-tag>
          <span class="log-source">{{ log.file }}:{{ log.line }}</span>
          <span class="log-message">{{ log.message }}</span>
        </div>
      </template>
      <el-empty v-else :description="emptyText" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLogsStore } from '@/stores/logs'
import { useLogStream } from '@/composables/useLogStream'

const props = defineProps<{
  pluginId: string
}>()

const { t } = useI18n()
const logsStore = useLogsStore()
const pluginIdRef = toRef(props, 'pluginId')
const { isConnected } = useLogStream(pluginIdRef)

const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const levelFilter = ref('')
const search = ref('')
const lines = ref(500)
const autoScroll = ref(true)
const logContainerRef = ref<HTMLElement | null>(null)

const loading = computed(() => logsStore.loading)
const rawLogs = computed(() => logsStore.getLogs(props.pluginId))
const logFileInfo = computed(() => logsStore.getLogFileInfo(props.pluginId))
const effectiveError = computed(() => logFileInfo.value?.error || logsStore.error || '')

const filteredLogs = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return rawLogs.value.filter((log) => {
    if (levelFilter.value && String(log.level || '').toUpperCase() !== levelFilter.value) {
      return false
    }
    if (!keyword) return true
    const source = `${log.file}:${log.line}`.toLowerCase()
    return (
      String(log.message || '').toLowerCase().includes(keyword) ||
      String(log.level || '').toLowerCase().includes(keyword) ||
      String(log.timestamp || '').toLowerCase().includes(keyword) ||
      source.includes(keyword)
    )
  })
})

const emptyText = computed(() => {
  if (rawLogs.value.length === 0) return t('logs.noLogs')
  return t('logs.noMatches')
})

function formatTimestamp(value: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function levelTagType(level: string) {
  const normalized = String(level || '').toUpperCase()
  if (normalized === 'ERROR' || normalized === 'CRITICAL') return 'danger'
  if (normalized === 'WARNING') return 'warning'
  if (normalized === 'DEBUG') return 'info'
  return 'success'
}

async function refreshLogs() {
  if (!props.pluginId) return
  await logsStore.fetchLogs(props.pluginId, {
    lines: lines.value,
    level: levelFilter.value || undefined,
    search: search.value.trim() || undefined
  })
}

async function scrollToBottom() {
  if (!autoScroll.value) return
  await nextTick()
  if (logContainerRef.value) {
    logContainerRef.value.scrollTop = logContainerRef.value.scrollHeight
  }
}

watch(
  () => props.pluginId,
  async (newId) => {
    if (!newId) return
    await refreshLogs()
  },
  { immediate: true }
)

watch(
  () => filteredLogs.value.length,
  async () => {
    await scrollToBottom()
  }
)

onMounted(async () => {
  await scrollToBottom()
})
</script>

<style scoped>
.log-viewer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.toolbar-item {
  min-width: 140px;
}

.level-select {
  width: 160px;
}

.search-input {
  width: 280px;
}

.lines-input {
  width: 140px;
}

.meta-row {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.meta-text {
  line-height: 20px;
}

.error-alert {
  margin-bottom: 4px;
}

.log-list {
  height: 420px;
  overflow: auto;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  padding: 8px;
}

.log-item {
  display: grid;
  grid-template-columns: 170px 90px 220px 1fr;
  gap: 8px;
  align-items: center;
  padding: 4px 8px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
}

.log-item:hover {
  background: var(--el-fill-color-light);
}

.log-time,
.log-source {
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.log-level {
  justify-self: start;
}

.log-message {
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 900px) {
  .log-item {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
