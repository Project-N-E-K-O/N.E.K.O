<template>
  <div class="hosted-surface-frame" :style="frameStyle">
    <iframe
      v-if="surface.mode === 'static' && surfaceUrl"
      ref="iframeRef"
      :key="iframeKey"
      :src="surfaceUrl"
      :title="surfaceTitle"
      class="hosted-surface-frame__iframe"
      sandbox="allow-scripts allow-forms allow-popups allow-same-origin"
      @load="handleLoad"
      @error="handleError"
    />

    <iframe
      v-else-if="surface.mode === 'hosted-tsx' && hostedDocument"
      ref="iframeRef"
      :key="iframeKey"
      :srcdoc="hostedDocument"
      :title="surfaceTitle"
      class="hosted-surface-frame__iframe"
      sandbox="allow-scripts"
      @load="handleLoad"
      @error="handleError"
    />

    <div v-else class="hosted-surface-frame__placeholder" :class="{ 'is-unavailable': surface.available === false }">
      <el-icon :size="42" class="hosted-surface-frame__icon">
        <Loading v-if="loading" class="is-loading" />
        <WarningFilled v-else-if="surface.available === false || error" />
        <Document v-else />
      </el-icon>
      <h3>{{ placeholderTitle }}</h3>
      <p>{{ placeholderText }}</p>
      <div class="hosted-surface-frame__meta">
        <el-tag size="small" effect="plain">{{ surface.kind }}</el-tag>
        <el-tag size="small" type="info" effect="plain">{{ surface.mode }}</el-tag>
        <el-tag v-if="surface.entry" size="small" type="success" effect="plain">
          {{ surface.entry }}
        </el-tag>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Document, Loading, WarningFilled } from '@element-plus/icons-vue'
import { callPluginHostedSurfaceAction, getPluginHostedSurfaceContext, getPluginHostedSurfaceSource } from '@/api/plugins'
import { buildHostedTsxDocument } from '@/components/plugin/hosted/tsxRuntime'
import type { PluginUiSurface } from '@/types/api'

const props = withDefaults(defineProps<{
  pluginId: string
  surface: PluginUiSurface
  height?: string
}>(), {
  height: '520px',
})

const emit = defineEmits<{
  load: []
  error: [error: string]
}>()

const { locale, t } = useI18n()
const iframeRef = ref<HTMLIFrameElement | null>(null)
const iframeKey = ref(0)
const hostedDocument = ref('')
const loading = ref(false)
const error = ref('')
let currentLoadId = 0

const frameStyle = computed(() => ({
  minHeight: props.height,
}))

const surfaceTitle = computed(() => {
  return props.surface.title || props.surface.id || props.pluginId
})

const surfaceUrl = computed(() => {
  const explicitUrl = props.surface.url || props.surface.ui_path
  if (explicitUrl) return explicitUrl
  if (props.surface.mode === 'static') {
    // LEGACY_STATIC_UI_COMPAT:
    // Static surfaces currently use the old /plugin/{id}/ui/ route.
    // Later this URL should come from the unified surface metadata.
    return `/plugin/${encodeURIComponent(props.pluginId)}/ui/`
  }
  return ''
})

const placeholderTitle = computed(() => {
  if (loading.value) return t('plugins.ui.loading')
  if (error.value) return t('plugins.ui.loadError')
  if (props.surface.available === false) return t('plugins.ui.surfaceUnavailable')
  if (props.surface.mode === 'hosted-tsx') return t('plugins.ui.hostedTsxPending')
  if (props.surface.mode === 'markdown') return t('plugins.ui.markdownPending')
  if (props.surface.mode === 'auto') return t('plugins.ui.autoPending')
  return t('plugins.ui.surfaceUnavailable')
})

const placeholderText = computed(() => {
  if (error.value) return error.value
  if (props.surface.available === false) return t('plugins.ui.surfaceEntryMissing')
  if (props.surface.mode === 'static') return t('plugins.ui.noUI')
  return t('plugins.ui.hostedRuntimePending')
})

function handleLoad() {
  emit('load')
}

function handleError() {
  emit('error', t('plugins.ui.loadError'))
}

async function loadHostedTsx() {
  if (props.surface.mode !== 'hosted-tsx' || props.surface.available === false) {
    hostedDocument.value = ''
    error.value = ''
    loading.value = false
    return
  }

  const loadId = ++currentLoadId
  loading.value = true
  error.value = ''
  hostedDocument.value = ''
  try {
    const response = await getPluginHostedSurfaceSource(props.pluginId, {
      kind: props.surface.kind,
      id: props.surface.id,
    })
    const context = await getPluginHostedSurfaceContext(props.pluginId, {
      kind: props.surface.kind,
      id: props.surface.id,
    })
    if (loadId !== currentLoadId) return
    hostedDocument.value = buildHostedTsxDocument({
      source: response.source,
      pluginId: props.pluginId,
      surface: props.surface,
      context,
      locale: String(locale.value),
    })
    iframeKey.value += 1
  } catch (caught: any) {
    if (loadId !== currentLoadId) return
    error.value = caught?.response?.data?.detail || caught?.message || String(caught)
    emit('error', error.value)
  } finally {
    if (loadId === currentLoadId) {
      loading.value = false
    }
  }
}

function handleMessage(event: MessageEvent) {
  if (event.source !== iframeRef.value?.contentWindow) return
  const data = event.data
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-error') {
    const message = typeof data.payload?.message === 'string' ? data.payload.message : t('plugins.ui.loadError')
    error.value = message
    emit('error', message)
    return
  }
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-request') {
    handleHostedRequest(data)
  }
}

async function handleHostedRequest(data: any) {
  const requestId = typeof data.requestId === 'string' ? data.requestId : ''
  const method = typeof data.method === 'string' ? data.method : ''
  const respond = (payload: Record<string, any>) => {
    iframeRef.value?.contentWindow?.postMessage({
      type: 'neko-hosted-surface-response',
      requestId,
      ...payload,
    }, '*')
  }
  if (!requestId) return
  try {
    if (method === 'call') {
      const actionId = String(data.payload?.actionId || '')
      const args = data.payload?.args && typeof data.payload.args === 'object' ? data.payload.args : {}
      const result = await callPluginHostedSurfaceAction(props.pluginId, actionId, args)
      respond({ ok: true, result })
      return
    }
    if (method === 'refresh') {
      const context = await getPluginHostedSurfaceContext(props.pluginId, {
        kind: props.surface.kind,
        id: props.surface.id,
      })
      respond({ ok: true, result: context })
      return
    }
    respond({ ok: false, error: `Unsupported hosted surface method: ${method}` })
  } catch (caught: any) {
    respond({
      ok: false,
      error: caught?.response?.data?.detail || caught?.message || String(caught),
    })
  }
}

onMounted(() => {
  window.addEventListener('message', handleMessage)
  loadHostedTsx()
})

onUnmounted(() => {
  window.removeEventListener('message', handleMessage)
})

watch(
  () => [props.pluginId, props.surface.kind, props.surface.id, props.surface.mode, props.surface.entry, props.surface.available, locale.value],
  () => {
    loadHostedTsx()
  },
)
</script>

<style scoped>
.hosted-surface-frame {
  position: relative;
  width: 100%;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 72%, transparent);
  border-radius: 16px;
  background: color-mix(in srgb, var(--el-bg-color) 92%, transparent);
  overflow: hidden;
}

.hosted-surface-frame__iframe {
  width: 100%;
  min-height: inherit;
  border: none;
  display: block;
}

.hosted-surface-frame__placeholder {
  min-height: inherit;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 32px;
  text-align: center;
  color: var(--el-text-color-secondary);
}

.hosted-surface-frame__placeholder h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 17px;
}

.hosted-surface-frame__placeholder p {
  max-width: 520px;
  margin: 0;
  line-height: 1.7;
}

.hosted-surface-frame__icon {
  color: var(--el-color-primary);
}

.hosted-surface-frame__placeholder.is-unavailable .hosted-surface-frame__icon {
  color: var(--el-color-warning);
}

.hosted-surface-frame__meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}
</style>
