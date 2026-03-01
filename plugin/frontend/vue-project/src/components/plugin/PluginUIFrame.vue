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
    
    <iframe
      v-show="!loading && !error && hasUI"
      :key="iframeKey"
      ref="iframeRef"
      :src="uiUrl"
      :title="pluginId"
      class="plugin-iframe"
      sandbox="allow-scripts allow-forms allow-popups allow-same-origin"
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
}>()

const { t } = useI18n()

const iframeRef = ref<HTMLIFrameElement | null>(null)
const iframeKey = ref(0)
const loading = ref(true)
const error = ref<string | null>(null)
const hasUI = ref(false)
let currentRequestId = 0
const expectedOrigin = window.location.origin

const uiUrl = computed(() => {
  if (!props.pluginId) return ''
  return `/plugin/${encodeURIComponent(props.pluginId)}/ui/`
})

async function checkUIAvailability() {
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

function onIframeLoad() {
  loading.value = false
  error.value = null
  emit('load')
}

function onIframeError() {
  loading.value = false
  error.value = t('plugins.ui.loadError')
  emit('error', error.value)
}

async function reload() {
  if (hasUI.value) {
    // UI availability already confirmed (iframe load failed); skip network call
    error.value = null
    loading.value = true
    iframeKey.value++
  } else {
    await checkUIAvailability()
    if (hasUI.value && !error.value) {
      iframeKey.value++
    }
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
  }
}

function sendMessage(payload: any) {
  if (!iframeRef.value?.contentWindow) return
  
  iframeRef.value.contentWindow.postMessage({
    type: 'neko-host-message',
    payload
  }, expectedOrigin)
}

defineExpose({
  reload,
  sendMessage,
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
