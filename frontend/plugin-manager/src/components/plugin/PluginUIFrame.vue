<template>
  <div class="plugin-ui-frame" :class="{ loading, error: !!error }">
    <div v-if="loading" class="loading-overlay">
      <el-icon class="is-loading" :size="32">
        <Loading />
      </el-icon>
      <span>{{ t('plugins.ui.loading') }}</span>
    </div>
    
    <div v-else-if="error" class="error-overlay">
      <el-icon :size="48" color="var(--el-color-danger)">
        <WarningFilled />
      </el-icon>
      <p class="error-message">{{ error }}</p>
      <el-button type="primary" @click="reload">
        {{ t('common.retry') }}
      </el-button>
    </div>
    
    <div v-else-if="!hasUI" class="no-ui-overlay">
      <el-icon :size="48" color="var(--el-color-info)">
        <InfoFilled />
      </el-icon>
      <p>{{ t('plugins.ui.noUI') }}</p>
    </div>
    
    <!-- Legacy static plugins may rely on standard browser modal dialogs. -->
    <iframe
      v-show="!loading && !error && hasUI"
      :key="iframeKey"
      ref="iframeRef"
      :src="uiUrl"
      :title="pluginId"
      :data-load-generation="iframeGeneration"
      class="plugin-iframe"
      sandbox="allow-scripts allow-forms allow-popups allow-same-origin allow-modals"
      @load="onIframeLoad"
      @error="onIframeError"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Loading, WarningFilled, InfoFilled } from '@element-plus/icons-vue'
import { get } from '@/api'

const props = defineProps<{
  pluginId: string
  height?: string
}>()

const emit = defineEmits<{
  (e: 'load'): void
  (e: 'error', error: string): void
  (e: 'message', data: any): void
  (e: 'openSurface', payload: { pluginId?: string; surfaceId: string; kind?: string; activationRevision?: number }): void
}>()

const { t } = useI18n()

const iframeRef = ref<HTMLIFrameElement | null>(null)
const iframeKey = ref(0)
const uiCacheBust = ref(Date.now())
const loading = ref(true)
const error = ref<string | null>(null)
const hasUI = ref(false)
const iframeReady = ref(false)
const iframeGeneration = ref(0)
const pendingSurfaceMessages: unknown[] = []
const maxPendingSurfaceMessages = 100
let currentRequestId = 0
const expectedOrigin = window.location.origin

const uiUrl = computed(() => {
  if (!props.pluginId) return ''
  // LEGACY_STATIC_UI_COMPAT:
  // This component is the original static UI iframe path. New entry points
  // should consume PluginUiSurface and render static panels through the
  // surface container, while this remains as compatibility renderer.
  return `/plugin/${encodeURIComponent(props.pluginId)}/ui/?_ui=${uiCacheBust.value}`
})

async function checkUIAvailability() {
  startIframeLoadGeneration()
  if (!props.pluginId) {
    currentRequestId += 1
    hasUI.value = false
    loading.value = false
    error.value = null
    return
  }
  const requestId = ++currentRequestId
  
  loading.value = true
  error.value = null
  try {
    const info = await get(`/plugin/${encodeURIComponent(props.pluginId)}/ui-info`)
    if (requestId !== currentRequestId) return
    hasUI.value = info?.has_ui ?? false
    
    if (!hasUI.value) {
      loading.value = false
    }
  } catch (e: any) {
    if (requestId !== currentRequestId) return
    error.value = e?.message || t('plugins.ui.loadError')
    hasUI.value = false
    loading.value = false
  }
}

function startIframeLoadGeneration() {
  iframeGeneration.value++
  iframeReady.value = false
  iframeKey.value++
}

function isCurrentIframeEvent(event: Event) {
  const target = event.currentTarget
  return target instanceof HTMLIFrameElement
    && target.dataset.loadGeneration === String(iframeGeneration.value)
}

function onIframeLoad(event: Event) {
  if (!isCurrentIframeEvent(event)) return
  loading.value = false
  error.value = null
  iframeReady.value = true
  flushSurfaceMessages()
  emit('load')
}

function onIframeError(event: Event) {
  if (!isCurrentIframeEvent(event)) return
  loading.value = false
  error.value = t('plugins.ui.loadError')
  iframeReady.value = false
  emit('error', error.value)
}

async function reload() {
  if (hasUI.value) {
    // UI availability already confirmed (iframe load failed); skip network call
    error.value = null
    loading.value = true
    startIframeLoadGeneration()
    uiCacheBust.value = Date.now()
  } else {
    uiCacheBust.value = Date.now()
    await checkUIAvailability()
  }
}

function handleMessage(event: MessageEvent) {
  if (!iframeRef.value) return
  
  // 验证消息来源（source + origin）
  if (event.source !== iframeRef.value.contentWindow) return
  if (event.origin !== expectedOrigin) return
  
  // 处理来自插件 UI 的消息
  const data = event.data
  if (data && typeof data === 'object' && data.type === 'plugin-ui-message') {
    emit('message', data.payload)
  } else if (data && typeof data === 'object' && data.type === 'neko-study-open-surface') {
    const payload = data.payload && typeof data.payload === 'object' ? data.payload : {}
    const surfaceId = typeof payload.surfaceId === 'string' ? payload.surfaceId.trim() : ''
    if (surfaceId) {
      const pluginId = typeof payload.pluginId === 'string' ? payload.pluginId.trim() : ''
      const kind = typeof payload.kind === 'string' ? payload.kind.trim() : ''
      const activationRevision = typeof payload.activationRevision === 'number'
        && Number.isSafeInteger(payload.activationRevision)
        && payload.activationRevision >= 0
        ? payload.activationRevision
        : undefined
      emit('openSurface', {
        pluginId: pluginId || undefined,
        surfaceId,
        kind: kind || undefined,
        ...(activationRevision === undefined ? {} : { activationRevision }),
      })
    }
  }
}

function sendMessage(payload: any) {
  if (!iframeRef.value?.contentWindow) return
  
  iframeRef.value.contentWindow.postMessage({
    type: 'neko-host-message',
    payload
  }, expectedOrigin)
}

function flushSurfaceMessages() {
  const target = iframeRef.value?.contentWindow
  if (!target || !iframeReady.value) return
  for (const message of pendingSurfaceMessages.splice(0)) {
    target.postMessage(message, expectedOrigin)
  }
}

function sendSurfaceMessage(message: unknown) {
  const target = iframeRef.value?.contentWindow
  if (target && iframeReady.value) {
    target.postMessage(message, expectedOrigin)
    return
  }
  // A hidden compatibility iframe may still be checking /ui-info while the
  // hosted panel emits its initial state. Keep the newest bounded set until
  // the iframe load event makes a target available.
  if (pendingSurfaceMessages.length >= maxPendingSurfaceMessages) {
    pendingSurfaceMessages.shift()
  }
  pendingSurfaceMessages.push(message)
}

defineExpose({
  reload,
  sendMessage,
  sendSurfaceMessage,
  hasUI
})

onMounted(() => {
  checkUIAvailability()
  window.addEventListener('message', handleMessage)
})

onUnmounted(() => {
  window.removeEventListener('message', handleMessage)
})

watch(() => props.pluginId, () => {
  pendingSurfaceMessages.length = 0
  iframeReady.value = false
  uiCacheBust.value = Date.now()
  checkUIAvailability()
})
</script>

<style scoped>
.plugin-ui-frame {
  position: relative;
  width: 100%;
  height: v-bind('props.height || "400px"');
  min-height: 200px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--el-border-radius-base);
  background: var(--el-bg-color);
  overflow: hidden;
}

.plugin-iframe {
  width: 100%;
  height: 100%;
  border: none;
}

.loading-overlay,
.error-overlay,
.no-ui-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: var(--el-bg-color);
  color: var(--el-text-color-secondary);
}

.error-message {
  margin: 0;
  color: var(--el-color-danger);
  text-align: center;
  max-width: 80%;
}

.loading-overlay .el-icon {
  color: var(--el-color-primary);
}
</style>
