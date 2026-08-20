<template>
  <section class="mirror-source-panel" data-yui-guide-id="github-mirror-source-panel">
    <header class="mirror-source-panel__header">
      <div>
        <div class="mirror-source-panel__title">
          <el-icon><Connection /></el-icon>
          <span>{{ t('mirrorSource.title') }}</span>
        </div>
        <p>{{ t('mirrorSource.description') }}</p>
      </div>
      <el-button text circle :aria-label="t('mirrorSource.close')" @click="$emit('close')">
        <el-icon><Close /></el-icon>
      </el-button>
    </header>

    <div class="mirror-source-panel__content">
      <el-form label-position="top">
        <el-form-item :label="t('mirrorSource.proxyOptional')">
          <el-select :model-value="mode" @update:model-value="setModeFromSelect">
            <el-option :label="t('mirrorSource.direct')" value="direct" />
            <el-option :label="t('mirrorSource.auto')" value="auto" />
            <el-option :label="t('mirrorSource.specified')" value="specified" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="mode !== 'direct'" :label="t('mirrorSource.source')">
          <el-input
            v-if="mode === 'auto'"
            :model-value="activeSource ? sourceLabel(activeSource.id) : t('mirrorSource.testing')"
            readonly
          />
          <el-select
            v-else
            :model-value="selectedSourceId"
            @update:model-value="setSpecifiedSourceFromSelect"
          >
            <el-option
              v-for="item in sources"
              :key="item.id"
              :label="item.label"
              :value="item.id"
            />
          </el-select>
          <div v-if="mode === 'auto'" class="mirror-source-panel__auto-result">
            <span v-if="activeSource">{{ t('mirrorSource.autoFastest', { source: sourceLabel(activeSource.id) }) }}</span>
            <span v-else-if="measuredOnce">{{ t('mirrorSource.autoUnavailable') }}</span>
            <span v-else>{{ t('mirrorSource.autoPending') }}</span>
            <span v-if="autoLatencyMs !== null">{{ autoLatencyMs }} ms</span>
          </div>
        </el-form-item>

        <el-button
          v-if="mode === 'auto'"
          type="primary"
          plain
          :loading="measuring"
          @click="measureAndSelectFastest"
        >
          {{ t('mirrorSource.testAndSelect') }}
        </el-button>
      </el-form>

      <div v-if="measuredOnce" class="mirror-source-panel__test-results">
        <span class="mirror-source-panel__test-results-title">{{ t('mirrorSource.recentResults') }}</span>
        <div v-for="item in measurements" :key="item.id" class="mirror-source-panel__test-result">
          <span>{{ sourceLabel(item.id) }}</span>
          <span :class="item.available ? 'is-ready' : 'is-unavailable'">
            {{ item.available ? `${Math.round(item.latency_ms ?? 0)} ms` : measurementFailureLabel(item) }}
          </span>
        </div>
      </div>

      <el-alert type="info" :closable="false" show-icon>
        <template #title>
          {{ t('mirrorSource.notice') }}
        </template>
      </el-alert>

      <p v-if="activeSource" class="mirror-source-panel__active">
        {{ t('mirrorSource.activeSource', { source: activeSource.baseUrl }) }}
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { Close, Connection } from '@element-plus/icons-vue'
import {
  useGithubMirrorSource,
  type GithubMirrorMeasurement,
  type GithubMirrorMode,
  type GithubMirrorSourceId,
  type GithubProxySourceId,
} from '@/composables/useGithubMirrorSource'

defineEmits<{ close: [] }>()
const { t } = useI18n()

const {
  mode,
  specifiedSourceId,
  activeSource,
  sources,
  speedTestSources,
  setMode,
  setSpecifiedSourceId,
  refreshAutoSource,
} = useGithubMirrorSource()
const measuring = ref(false)
const autoLatencyMs = ref<number | null>(null)
const measuredOnce = ref(false)
const measurements = ref<ProxyMeasurement[]>([])

const selectedSourceId = computed(() => activeSource.value?.id ?? specifiedSourceId.value)

function setModeFromSelect(value: string | number | boolean | undefined) {
  if (value === 'direct' || value === 'auto' || value === 'specified') {
    setMode(value as GithubMirrorMode)
  }
}

function setSpecifiedSourceFromSelect(value: string | number | boolean | undefined) {
  if (typeof value === 'string' && sources.some((source) => source.id === value)) {
    setSpecifiedSourceId(value as GithubProxySourceId)
  }
}

type ProxyMeasurement = GithubMirrorMeasurement

function sourceLabel(sourceId: GithubMirrorSourceId): string {
  if (sourceId === 'github-direct') return t('mirrorSource.direct')
  const source = speedTestSources.find((item) => item.id === sourceId)
  return source && 'label' in source ? source.label : sourceId
}

function measurementFailureLabel(item: ProxyMeasurement): string {
  return item.status_code ? `HTTP ${item.status_code}` : t('mirrorSource.unavailable')
}

async function measureAndSelectFastest(options: { silent?: boolean } = {}) {
  measuring.value = true
  try {
    const { measurements: results, fastest } = await refreshAutoSource()
    measurements.value = results
    measuredOnce.value = true
    if (!fastest) {
      autoLatencyMs.value = null
      if (!options.silent) ElMessage.warning(t('mirrorSource.noAvailable'))
      return
    }
    autoLatencyMs.value = Math.round(fastest.latency_ms ?? 0)
    if (!options.silent) {
      ElMessage.success(t('mirrorSource.selectedFastest', { source: sourceLabel(fastest.id) }))
    }
  } catch {
    autoLatencyMs.value = null
    measuredOnce.value = true
    if (!options.silent) ElMessage.error(t('mirrorSource.testFailed'))
  } finally {
    measuring.value = false
  }
}

watch(mode, (next) => {
  if (next === 'auto' && !activeSource.value && !measuring.value) {
    void measureAndSelectFastest({ silent: true })
  }
})

onMounted(() => {
  if (mode.value === 'auto' && !activeSource.value) {
    void measureAndSelectFastest({ silent: true })
  }
})
</script>

<style scoped>
.mirror-source-panel { box-sizing: border-box; height: 100%; overflow: auto; padding: 18px; border: 1px solid var(--el-border-color-light); border-radius: var(--radius-card, 16px); background: var(--el-bg-color); }
.mirror-source-panel__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 16px; border-bottom: 1px solid var(--el-border-color-lighter); }
.mirror-source-panel__title { display: flex; align-items: center; gap: 8px; color: var(--el-text-color-primary); font-size: 18px; font-weight: 650; }
.mirror-source-panel__title .el-icon { color: var(--el-color-primary); }
.mirror-source-panel__header p { max-width: 410px; margin: 7px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.55; }
.mirror-source-panel__content { padding-top: 20px; }
.mirror-source-panel :deep(.el-select) { width: 100%; }
.mirror-source-panel__auto-result { display: flex; justify-content: space-between; gap: 10px; width: 100%; margin-top: 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.mirror-source-panel__test-results { display: grid; gap: 8px; margin: 18px 0 20px; padding: 13px; border-radius: 10px; background: var(--el-fill-color-light); font-size: 12px; }
.mirror-source-panel__test-results-title { color: var(--el-text-color-secondary); }
.mirror-source-panel__test-result { display: flex; justify-content: space-between; gap: 12px; }
.mirror-source-panel__test-result > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.is-ready { color: var(--el-color-success); }
.is-unavailable { color: var(--el-color-warning); }
.mirror-source-panel__active { overflow-wrap: anywhere; margin: 16px 0 0; color: var(--el-text-color-secondary); font-family: var(--el-font-family-monospace, monospace); font-size: 12px; }
</style>
