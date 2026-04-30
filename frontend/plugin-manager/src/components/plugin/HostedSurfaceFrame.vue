<template>
  <div class="hosted-surface-frame" :style="frameStyle">
    <el-alert
      v-if="runtimeError"
      class="hosted-surface-frame__runtime-alert"
      :type="runtimeErrorFatal ? 'error' : 'warning'"
      show-icon
      :closable="true"
      :title="runtimeErrorTitle"
      :description="runtimeError"
      @close="runtimeError = ''"
    />

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
      v-else-if="(surface.mode === 'hosted-tsx' || surface.mode === 'markdown') && hostedDocument"
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
  openLogs: []
}>()

const { locale, t } = useI18n()
const iframeRef = ref<HTMLIFrameElement | null>(null)
const iframeKey = ref(0)
const hostedDocument = ref('')
const loading = ref(false)
const error = ref('')
const runtimeError = ref('')
const runtimeErrorFatal = ref(false)
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

const runtimeErrorTitle = computed(() => {
  return runtimeErrorFatal.value ? t('plugins.ui.loadError') : t('plugins.ui.controlError')
})

function escapeHtml(value: string) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function escapeAttribute(value: string) {
  return escapeHtml(value).replace(/'/g, '&#39;')
}

function renderInlineMarkdown(value: string) {
  const escaped = escapeHtml(value)
  return escaped
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_match, label, url) => {
      const safeUrl = escapeAttribute(String(url))
      return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${label}</a>`
    })
}

function renderMarkdownToHtml(markdown: string) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const html: string[] = []
  let inCode = false
  let codeLines: string[] = []
  let inList = false
  const closeList = () => {
    if (inList) {
      html.push('</ul>')
      inList = false
    }
  }

  for (const line of lines) {
    const fence = line.match(/^```/)
    if (fence) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
        inCode = false
      } else {
        closeList()
        inCode = true
      }
      continue
    }
    if (inCode) {
      codeLines.push(line)
      continue
    }
    if (!line.trim()) {
      closeList()
      continue
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/)
    if (heading) {
      closeList()
      const level = heading[1]?.length || 1
      html.push(`<h${level}>${renderInlineMarkdown(heading[2] || '')}</h${level}>`)
      continue
    }
    const listItem = line.match(/^\s*[-*]\s+(.+)$/)
    if (listItem) {
      if (!inList) {
        html.push('<ul>')
        inList = true
      }
      html.push(`<li>${renderInlineMarkdown(listItem[1] || '')}</li>`)
      continue
    }
    const quote = line.match(/^>\s?(.+)$/)
    if (quote) {
      closeList()
      html.push(`<blockquote>${renderInlineMarkdown(quote[1] || '')}</blockquote>`)
      continue
    }
    closeList()
    html.push(`<p>${renderInlineMarkdown(line)}</p>`)
  }
  closeList()
  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  }
  return html.join('\n')
}

function buildMarkdownDocument(source: string, title: string) {
  return `<!doctype html>
<html lang="${escapeAttribute(String(locale.value))}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { color-scheme: light dark; }
    body { margin: 0; padding: 24px; font: 14px/1.7 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2937; background: #fff; }
    main { max-width: 880px; margin: 0 auto; }
    h1, h2, h3 { line-height: 1.25; color: #111827; }
    h1 { font-size: 28px; margin: 0 0 20px; }
    h2 { font-size: 22px; margin: 28px 0 12px; }
    h3 { font-size: 17px; margin: 22px 0 10px; }
    p, ul, blockquote, pre { margin: 12px 0; }
    ul { padding-left: 22px; }
    code { padding: 2px 5px; border-radius: 5px; background: #f3f4f6; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    pre { overflow: auto; padding: 14px; border-radius: 10px; background: #111827; color: #f9fafb; }
    pre code { padding: 0; background: transparent; color: inherit; }
    blockquote { padding: 8px 14px; border-left: 4px solid #93c5fd; background: #eff6ff; color: #374151; }
    a { color: #2563eb; }
    @media (prefers-color-scheme: dark) {
      body { color: #e5e7eb; background: #111827; }
      h1, h2, h3 { color: #f9fafb; }
      code { background: #1f2937; }
      blockquote { background: #172554; color: #dbeafe; }
      a { color: #93c5fd; }
    }
  </style>
</head>
<body>
  <main>
    <h1>${escapeHtml(title)}</h1>
    ${renderMarkdownToHtml(source)}
  </main>
</body>
</html>`
}

function handleLoad() {
  emit('load')
}

function handleError() {
  loading.value = false
  error.value = t('plugins.ui.loadError')
  emit('error', t('plugins.ui.loadError'))
}

async function loadHostedTsx() {
  if (!['hosted-tsx', 'markdown'].includes(props.surface.mode) || props.surface.available === false) {
    hostedDocument.value = ''
    error.value = ''
    runtimeError.value = ''
    runtimeErrorFatal.value = false
    loading.value = false
    return
  }

  const loadId = ++currentLoadId
  loading.value = true
  error.value = ''
  runtimeError.value = ''
  runtimeErrorFatal.value = false
  hostedDocument.value = ''
  try {
    const response = await getPluginHostedSurfaceSource(props.pluginId, {
      kind: props.surface.kind,
      id: props.surface.id,
    })
    if (loadId !== currentLoadId) return
    if (props.surface.mode === 'markdown') {
      hostedDocument.value = buildMarkdownDocument(response.source, surfaceTitle.value)
    } else {
      const context = await getPluginHostedSurfaceContext(props.pluginId, {
        kind: props.surface.kind,
        id: props.surface.id,
        locale: String(locale.value),
      })
      if (loadId !== currentLoadId) return
      hostedDocument.value = buildHostedTsxDocument({
        source: response.source,
        pluginId: props.pluginId,
        surface: props.surface,
        context,
        locale: String(locale.value),
      })
    }
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
    const fatal = data.payload?.fatal !== false
    runtimeError.value = message
    runtimeErrorFatal.value = fatal
    console.error('[HostedSurfaceFrame] plugin UI error', {
      pluginId: props.pluginId,
      surface: `${props.surface.kind}:${props.surface.id}`,
      fatal,
      scope: data.payload?.scope,
      details: data.payload?.details,
      message,
    })
    if (fatal) error.value = message
    emit('error', message)
    return
  }
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-open-logs') {
    emit('openLogs')
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
      const result = await callPluginHostedSurfaceAction(props.pluginId, actionId, args, {
        kind: props.surface.kind,
        id: props.surface.id,
      })
      respond({ ok: true, result })
      return
    }
    if (method === 'refresh') {
      const context = await getPluginHostedSurfaceContext(props.pluginId, {
        kind: props.surface.kind,
        id: props.surface.id,
        locale: String(locale.value),
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

.hosted-surface-frame__runtime-alert {
  margin: 12px;
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
