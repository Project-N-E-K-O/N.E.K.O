<template>
  <section class="mirror-source-panel" data-yui-guide-id="github-mirror-source-panel">
    <header class="mirror-source-panel__header">
      <div>
        <div class="mirror-source-panel__title">
          <el-icon><Connection /></el-icon>
          <span>镜像源</span>
        </div>
        <p>选择插件市场下载 GitHub Release 包时使用的连接方式。</p>
      </div>
      <el-button text circle aria-label="关闭镜像源" @click="$emit('close')">
        <el-icon><Close /></el-icon>
      </el-button>
    </header>

    <div class="mirror-source-panel__content">
      <el-form label-position="top">
        <el-form-item label="GitHub Proxy（可选）">
          <el-select :model-value="mode" @update:model-value="setModeFromSelect">
            <el-option label="GitHub 直连" value="direct" />
            <el-option label="自动测速选择" value="auto" />
            <el-option label="指定代理" value="specified" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="mode !== 'direct'" label="镜像源">
          <el-select
            :model-value="selectedSourceId"
            :disabled="mode === 'auto'"
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
            <span v-if="activeSource">当前最快：{{ activeSource.label }}</span>
            <span v-else-if="measuredOnce">最近测速没有可用节点，已回退 GitHub 直连。</span>
            <span v-else>尚未测速；将暂时使用 GitHub 直连。</span>
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
          自动测速并选择最快节点
        </el-button>
      </el-form>

      <div v-if="measuredOnce" class="mirror-source-panel__test-results">
        <span class="mirror-source-panel__test-results-title">最近测速结果</span>
        <div v-for="item in measurements" :key="item.id" class="mirror-source-panel__test-result">
          <span>{{ sourceLabel(item.id) }}</span>
          <span :class="item.available ? 'is-ready' : 'is-unavailable'">
            {{ item.available ? `${Math.round(item.latency_ms ?? 0)} ms` : measurementFailureLabel(item) }}
          </span>
        </div>
      </div>

      <el-alert type="info" :closable="false" show-icon>
        <template #title>
          仅代理 github.com 的插件下载链接；包仍会按 Market 提供的 SHA-256 校验。
        </template>
      </el-alert>

      <p v-if="activeSource" class="mirror-source-panel__active">
        当前镜像：{{ activeSource.baseUrl }}
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, Connection } from '@element-plus/icons-vue'
import {
  useGithubMirrorSource,
  type GithubMirrorMode,
  type GithubProxySourceId,
} from '@/composables/useGithubMirrorSource'

defineEmits<{ close: [] }>()

const {
  mode,
  specifiedSourceId,
  activeSource,
  sources,
  setMode,
  setSpecifiedSourceId,
  setAutoSourceId,
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

interface ProxyMeasurement {
  id: GithubProxySourceId
  latency_ms: number | null
  available: boolean
  status_code?: number | null
}

function sourceLabel(sourceId: GithubProxySourceId): string {
  return sources.find((source) => source.id === sourceId)?.label ?? sourceId
}

function measurementFailureLabel(item: ProxyMeasurement): string {
  return item.status_code ? `HTTP ${item.status_code}` : '连接失败或超时'
}

async function measureAndSelectFastest(options: { silent?: boolean } = {}) {
  measuring.value = true
  try {
    const response = await fetch('/market/github-proxy/measure')
    if (!response.ok) throw new Error('测速服务不可用')
    const data = await response.json() as { sources?: ProxyMeasurement[] }
    measurements.value = (data.sources ?? []).filter((item) => sources.some((source) => source.id === item.id))
    measuredOnce.value = true
    const fastest = (data.sources ?? [])
      .filter((item) => (
        item.available
        && typeof item.latency_ms === 'number'
        && sources.some((source) => source.id === item.id)
      ))
      .sort((left, right) => Number(left.latency_ms) - Number(right.latency_ms))[0]
    if (!fastest) {
      if (!options.silent) ElMessage.warning('没有可用的 GitHub Proxy 节点，已回退 GitHub 直连。')
      return
    }
    setAutoSourceId(fastest.id)
    autoLatencyMs.value = Math.round(fastest.latency_ms ?? 0)
    ElMessage.success(`已选择最快节点：${sources.find((source) => source.id === fastest.id)?.label ?? fastest.id}`)
  } catch (error) {
    measuredOnce.value = true
    if (!options.silent) ElMessage.error(error instanceof Error ? error.message : '镜像源测速失败')
  } finally {
    measuring.value = false
  }
}

watch(mode, (next) => {
  if (next === 'auto' && !activeSource.value && !measuring.value) {
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
