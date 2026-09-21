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

    <div v-if="localeChangePending" class="hosted-surface-frame__locale-notice" data-testid="surface-locale-pending">
      {{ t('common.surfaceLanguagePending') }}
      <el-button data-testid="surface-apply-locale" @click="applyDocumentLocale">{{ t('common.surfaceApplyLanguage') }}</el-button>
    </div>

    <!-- Preserve standard alert/confirm/prompt behavior authored by static plugins. -->
    <iframe
      v-if="surface.mode === 'static' && surfaceUrl"
      ref="iframeRef"
      :key="iframeKey"
      :src="surfaceUrl"
      :title="surfaceTitle"
      class="hosted-surface-frame__iframe"
      sandbox="allow-scripts allow-forms allow-popups allow-same-origin allow-modals"
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
      <div v-if="error && !loading && surface.available !== false && (surface.mode === 'hosted-tsx' || surface.mode === 'markdown')">
        <el-button v-if="!rendererReloadRequired" data-testid="surface-retry" @click="loadHostedTsx">{{ t('market.retry') }}</el-button>
        <el-button data-testid="surface-reload-page" @click="reloadPage">{{ t('common.languageReload') }}</el-button>
      </div>
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
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Document, Loading, WarningFilled } from '@element-plus/icons-vue'
import { callPluginHostedSurfaceAction, getPluginHostedSurfaceContext, getPluginHostedSurfaceSource, parseHostedDocument } from '@/api/plugins'
import { loadTsxRenderer, loadMarkdownRenderer } from '@/components/plugin/hosted/rendererModules'
import { openExternalUrl, openLocalPath } from '@/utils/openExternal'
import type { PluginUiSurface } from '@/types/api'
import { OptionalModuleError } from '@/utils/retryableModule'
import { ElMessageBox } from 'element-plus'

const props = withDefaults(defineProps<{
  pluginId: string
  surface: PluginUiSurface
  height?: string
  active?: boolean
  activationRevision?: number
}>(), {
  height: 'clamp(520px, calc(100vh - 220px), 1200px)',
  active: false,
  activationRevision: 0,
})

const emit = defineEmits<{
  load: []
  error: [error: string]
  openLogs: []
  message: [data: unknown]
}>()

const { locale, t } = useI18n()
const iframeRef = ref<HTMLIFrameElement | null>(null)
const iframeKey = ref(0)
const staticSurfaceReady = ref(false)
const pendingStaticSurfaceMessages: unknown[] = []
const maxPendingStaticSurfaceMessages = 100
const hostedDocument = ref('')
const loading = ref(false)
const error = ref('')
const rendererReloadRequired = ref(false)
// A live plugin document owns its language. An app-language update must not
// discard drafts or cancel a surviving static document's pending mutations.
const documentLocale = ref<string | null>(null)
const localeChangePending = computed(() => !!hostedDocument.value && documentLocale.value !== String(locale.value))
const runtimeError = ref('')
const runtimeErrorFatal = ref(false)
let currentLoadId = 0
let sourceController: AbortController | null = null
let hostedRequestGeneration = 0
let componentMounted = false
const hostedDocumentControllers = new Map<string, AbortController>()
const hostedActionControllers = new Map<string, AbortController>()
const contextControllers = new Set<AbortController>()
let contextQueue: Promise<unknown> = Promise.resolve()

function readContextInOrder() {
  const generation = hostedRequestGeneration
  const pluginId = props.pluginId
  const params = { kind: props.surface.kind, id: props.surface.id, locale: documentLocale.value ?? String(locale.value) }
  const task = contextQueue.then(async () => {
    if (!componentMounted || generation !== hostedRequestGeneration) throw new Error('Surface changed')
    const controller = new AbortController()
    contextControllers.add(controller)
    try {
      return await getPluginHostedSurfaceContext(pluginId, params, { signal: controller.signal, suppressErrorMessage: true })
    } finally { contextControllers.delete(controller) }
  })
  contextQueue = task.catch(() => {})
  return task
}

function abortAllHostedRequests(reason: string) {
  for (const controller of contextControllers) controller.abort(reason)
  contextControllers.clear()
  contextQueue = Promise.resolve()
  for (const controller of hostedDocumentControllers.values()) controller.abort(reason)
  hostedDocumentControllers.clear()
  for (const controller of hostedActionControllers.values()) controller.abort(reason)
  hostedActionControllers.clear()
}

const HOST_STARTUP_RETRY_DELAYS_MS = [100, 200, 400, 800, 1600] as const
const MAX_HOSTED_DOCUMENT_BYTES = 16 * 1024 * 1024
const HOSTED_DOCUMENT_MIME_BY_EXTENSION = {
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
} as const

type HostedBridgeError = {
  message: string
  code?: string
  details?: unknown
  status?: number
}

const frameStyle = computed(() => ({
  height: props.height,
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

// PR #1480 review-fix 1.30: trust boundary for postMessage between this
// component and the embedded iframe. Two iframe modes coexist:
//
//   - ``surface.mode === 'static'``: iframe loads ``surfaceUrl`` (an http(s)
//     URL or a same-origin path). The trusted origin is parsed from that URL
//     and resolved against ``window.location.origin`` for relative paths.
//
//   - ``hosted-tsx`` / ``markdown``: iframe is loaded via ``srcdoc=...``. The
//     spec mandates these iframes report ``event.origin === 'null'`` (an opaque
//     origin), so we accept the literal string ``'null'`` as the trusted
//     origin sentinel for srcdoc iframes.
//
// ``handleMessage`` rejects any message whose ``event.origin`` does not match
// this value, and ``handleHostedRequest`` posts responses with this origin
// rather than ``'*'``. The fallback to ``'*'`` is intentional and only used
// for the srcdoc case where the standard requires ``'*'`` because the child
// is in an opaque origin and cannot be addressed by name; in that branch the
// inbound origin check (combined with ``event.source ===
// iframeRef.value.contentWindow``) is what enforces the trust boundary.
const trustedIframeOrigin = computed(() => {
  if (props.surface.mode === 'static') {
    const url = surfaceUrl.value
    if (!url) return window.location.origin
    try {
      return new URL(url, window.location.origin).origin
    } catch {
      return window.location.origin
    }
  }
  // srcdoc iframes (hosted-tsx / markdown). Per HTML spec the resulting origin
  // is opaque and is reported as the literal string 'null'.
  return 'null'
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

function readResponseHeader(headers: Record<string, any> | undefined, name: string) {
  if (!headers || typeof headers !== 'object') return ''
  if (typeof headers.get === 'function') {
    const got = headers.get(name)
    if (got !== undefined && got !== null) return String(got)
  }
  const value = headers[name] ?? headers[name.toLowerCase()]
  if (Array.isArray(value)) return value.length > 0 ? String(value[0] || '') : ''
  return typeof value === 'string' ? value : ''
}

function normalizeHostedBridgeError(caught: any): HostedBridgeError {
  const data = caught?.response?.data
  const detail = data?.detail
  const status = typeof caught?.response?.status === 'number' ? caught.response.status : undefined
  let code = readResponseHeader(caught?.response?.headers, 'X-Error-Code')
  let details: unknown
  let message = ''

  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const record = detail as Record<string, any>
    if (!code && typeof record.code === 'string') code = record.code
    if (record.details !== undefined) details = record.details
    if (typeof record.message === 'string') message = record.message
    else if (typeof record.detail === 'string') message = record.detail
  } else if (typeof detail === 'string') {
    message = detail
  }

  if (!code && typeof data?.code === 'string') code = data.code
  if (details === undefined && data?.details !== undefined) details = data.details
  if (!message && typeof data?.message === 'string') message = data.message
  if (!message) message = caught?.message || String(caught)

  return { message, code: code || undefined, details, status }
}

function handleLoad() {
  if (props.surface.mode === 'static') {
    staticSurfaceReady.value = true
    flushStaticSurfaceMessages()
  }
  postActivation()
  emit('load')
}

function postActivation() {
  if (!props.active || !Number.isSafeInteger(props.activationRevision) || props.activationRevision < 0) return
  const targetOrigin = trustedIframeOrigin.value === 'null' ? '*' : trustedIframeOrigin.value
  iframeRef.value?.contentWindow?.postMessage({
    type: 'neko-hosted-surface-activated',
    payload: {
      surfaceId: props.surface.id,
      revision: props.activationRevision,
    },
  }, targetOrigin)
}

function handleError() {
  loading.value = false
  staticSurfaceReady.value = false
  error.value = t('plugins.ui.loadError')
  emit('error', t('plugins.ui.loadError'))
}

function flushStaticSurfaceMessages() {
  const target = iframeRef.value?.contentWindow
  if (!target || !staticSurfaceReady.value) return
  for (const message of pendingStaticSurfaceMessages.splice(0)) {
    target.postMessage(message, trustedIframeOrigin.value)
  }
}

function sendSurfaceMessage(message: unknown) {
  if (props.surface.mode !== 'static') return
  const target = iframeRef.value?.contentWindow
  if (target && staticSurfaceReady.value) {
    target.postMessage(message, trustedIframeOrigin.value)
    return
  }
  if (pendingStaticSurfaceMessages.length >= maxPendingStaticSurfaceMessages) {
    pendingStaticSurfaceMessages.shift()
  }
  pendingStaticSurfaceMessages.push(message)
}

// Some browsers cache module evaluation failures until document reload. Do not
// auto-reload (it would destroy user work); expose an explicit escape hatch.
function reloadPage() { window.location.reload() }

function invalidateSurfaceLoad() {
  sourceController?.abort('surface-replaced')
  sourceController = null
  currentLoadId += 1
  hostedRequestGeneration += 1
  abortAllHostedRequests('surface-changed')
  hostedDocument.value = ''
}

async function loadHostedTsx() {
  // Invalidate even for static/unavailable transitions: an earlier source or
  // chunk may still resolve. Never let it publish into the replacement frame.
  invalidateSurfaceLoad()
  const loadId = currentLoadId
  const pluginId = props.pluginId
  const surface = { ...props.surface }
  const requestLocale = String(locale.value)
  const title = surfaceTitle.value
  rendererReloadRequired.value = false
  const isCurrent = () => componentMounted && loadId === currentLoadId
  error.value = ''
  runtimeError.value = ''
  runtimeErrorFatal.value = false
  loading.value = false
  if (!componentMounted || !['hosted-tsx', 'markdown'].includes(surface.mode) || surface.available === false) return
  loading.value = true
  const controller = new AbortController()
  sourceController = controller
  const requestConfig = { signal: controller.signal, suppressErrorMessage: true }
  try {
    // Observe import failure immediately; don't leave a rejected import promise
    // unhandled while source/context is pending. Compilation remains synchronous
    // inside the selected module, not secretly described as worker-based.
    const renderer = surface.mode === 'markdown' ? loadMarkdownRenderer() : loadTsxRenderer()
    const [response, module] = await Promise.all([
      getPluginHostedSurfaceSource(pluginId, { kind: surface.kind, id: surface.id, locale: requestLocale }, requestConfig),
      renderer,
    ])
    if (!isCurrent()) return
    let document: string
    if (surface.mode === 'markdown') {
      document = (module as Awaited<ReturnType<typeof loadMarkdownRenderer>>).buildMarkdownDocument(response.source, title, requestLocale)
    } else {
      const context = await getPluginHostedSurfaceContext(pluginId, { kind: surface.kind, id: surface.id, locale: requestLocale }, requestConfig)
      if (!isCurrent()) return
      document = (module as Awaited<ReturnType<typeof loadTsxRenderer>>).buildHostedTsxDocument({
        source: response.source, dependencies: response.dependencies, pluginId, surface, context, locale: requestLocale,
      })
    }
    if (!isCurrent()) return
    documentLocale.value = requestLocale
    hostedDocument.value = document
    iframeKey.value += 1
  } catch (caught: any) {
    if (!isCurrent()) return
    error.value = normalizeHostedBridgeError(caught).message
    rendererReloadRequired.value = caught instanceof OptionalModuleError && caught.reloadRequired
    emit('error', error.value)
  } finally {
    controller.abort('surface-load-finished')
    if (sourceController === controller) sourceController = null
    if (isCurrent()) loading.value = false
  }
}

function handleMessage(event: MessageEvent) {
  // PR #1480 review-fix 1.30: enforce the trust boundary on inbound messages.
  // Both checks are required:
  //   - ``event.source`` ensures the message comes from THIS iframe (not from
  //     some other iframe that happens to share an origin).
  //   - ``event.origin`` ensures the iframe has not been redirected to a
  //     third-party origin since it was loaded; without this, a malicious
  //     navigation inside the iframe could let attacker code act as the
  //     plugin.
  if (event.source !== iframeRef.value?.contentWindow) return
  if (event.origin !== trustedIframeOrigin.value) return
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
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-console') {
    const level = typeof data.payload?.level === 'string' ? data.payload.level : 'log'
    const args = Array.isArray(data.payload?.args) ? data.payload.args : []
    const consoleMethod: 'debug' | 'info' | 'warn' | 'error' | 'log' = level === 'debug' || level === 'info' || level === 'warn' || level === 'error' ? level : 'log'
    console[consoleMethod]('[HostedSurfaceFrame] plugin UI console', {
      pluginId: props.pluginId,
      surface: `${props.surface.kind}:${props.surface.id}`,
      args,
      timestamp: data.payload?.timestamp,
    })
    emit('message', data)
    return
  }
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-open-logs') {
    emit('openLogs')
    return
  }
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-open-external') {
    const url = typeof data.payload?.url === 'string' ? data.payload.url : ''
    if (url) openExternalUrl(url)
    return
  }
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-open-path') {
    const path = typeof data.payload?.path === 'string' ? data.payload.path : ''
    if (path) openLocalPath(path).catch(() => {}) // 宿主插件发起的打开请求，失败时静默处理
    return
  }
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-cancel') {
    const requestId = typeof data.requestId === 'string' ? data.requestId : ''
    hostedDocumentControllers.get(requestId)?.abort('client-cancelled')
    hostedActionControllers.get(requestId)?.abort('client-cancelled')
    return
  }
  if (data && typeof data === 'object' && data.type === 'neko-hosted-surface-request') {
    handleHostedRequest(data)
    return
  }
  if (data && typeof data === 'object' && typeof data.type === 'string') {
    emit('message', data)
  }
}

async function handleHostedRequest(data: any) {
  const requestId = typeof data.requestId === 'string' ? data.requestId : ''
  const method = typeof data.method === 'string' ? data.method : ''
  const actionId = method === 'call' ? String(data.payload?.actionId || '') : ''
  const userInitiated = (method === 'call' || method === 'parseDocument') && data.userInitiated === true
  const requestGeneration = hostedRequestGeneration
  let responded = false
  const isCurrentRequest = () => componentMounted && requestGeneration === hostedRequestGeneration
  const respond = (payload: Record<string, any>) => {
    if (responded || !isCurrentRequest()) return
    responded = true
    // PR #1480 review-fix 1.30: target the trusted origin instead of '*'.
    // For srcdoc iframes (opaque origin, reported as 'null'), the postMessage
    // spec rejects 'null' as a target; the standard idiom is to use '*' and
    // rely on the source/origin checks in handleMessage to enforce trust.
    const targetOrigin = trustedIframeOrigin.value === 'null' ? '*' : trustedIframeOrigin.value
    iframeRef.value?.contentWindow?.postMessage({
      type: 'neko-hosted-surface-response',
      requestId,
      ...payload,
    }, targetOrigin)
  }
  if (!requestId) return
  try {
    if (method === 'call') {
      const args = data.payload?.args && typeof data.payload.args === 'object' ? data.payload.args : {}
      const timeoutMs = Number(data.timeoutMs)
      const hasRequestDeadline = Number.isFinite(timeoutMs) && timeoutMs > 0
      const requestDeadline = hasRequestDeadline ? Date.now() + timeoutMs : undefined
      const pluginId = props.pluginId
      const surfaceKind = props.surface.kind
      const surfaceId = props.surface.id
      const requestLocale = documentLocale.value ?? String(locale.value)
      const controller = new AbortController()
      hostedActionControllers.set(requestId, controller)
      try {
        for (let attempt = 0; ; attempt += 1) {
          try {
            const remainingTimeoutMs = requestDeadline === undefined
              ? undefined
              : Math.max(1, Math.ceil(requestDeadline - Date.now()))
            const result = await callPluginHostedSurfaceAction(pluginId, actionId, args, {
              kind: surfaceKind,
              id: surfaceId,
              locale: requestLocale,
              timeoutMs: remainingTimeoutMs,
              signal: controller.signal,
              userInitiated,
            })
            if (controller.signal.aborted) return
            respond({ ok: true, result })
            return
          } catch (caught: any) {
            if (controller.signal.aborted) return
            const bridgeError = normalizeHostedBridgeError(caught)
            const retryDelayMs = HOST_STARTUP_RETRY_DELAYS_MS[attempt]
            if (userInitiated || bridgeError.code !== 'PLUGIN_NOT_RUNNING' || retryDelayMs === undefined) {
              throw caught
            }
            if (!isCurrentRequest()) return
            if (requestDeadline !== undefined && retryDelayMs >= requestDeadline - Date.now()) {
              throw caught
            }
            await new Promise<void>((resolve) => window.setTimeout(resolve, retryDelayMs))
            if (controller.signal.aborted || !isCurrentRequest()) return
          }
        }
      } finally {
        if (hostedActionControllers.get(requestId) === controller) {
          hostedActionControllers.delete(requestId)
        }
      }
    }
    if (method === 'refresh') {
      const context = await readContextInOrder()
      respond({ ok: true, result: context })
      return
    }
    if (method === 'parseDocument') {
      if (!props.surface.permissions?.includes('document:parse')) {
        respond({
          ok: false,
          error: 'This hosted surface is not allowed to parse documents.',
          code: 'document_parse_permission_denied',
        })
        return
      }
      if (!userInitiated) {
        respond({
          ok: false,
          error: 'Document parsing must be started by a user action.',
          code: 'document_parse_permission_denied',
        })
        return
      }
      const file = data.payload?.file
      if (typeof File === 'undefined' || !(file instanceof File)) {
        respond({ ok: false, error: 'parseDocument requires one File.', code: 'unsupported_document' })
        return
      }
      if (file.size > MAX_HOSTED_DOCUMENT_BYTES) {
        respond({ ok: false, error: 'Document exceeds the 16 MiB upload limit.', code: 'document_too_large' })
        return
      }
      const extension = file.name.split('.').pop()?.toLowerCase() || ''
      const expectedMime = HOSTED_DOCUMENT_MIME_BY_EXTENSION[extension as keyof typeof HOSTED_DOCUMENT_MIME_BY_EXTENSION]
      if (!expectedMime || (file.type && ![expectedMime, 'application/octet-stream'].includes(file.type))) {
        respond({ ok: false, error: 'Only PDF and DOCX documents are supported.', code: 'unsupported_document' })
        return
      }
      const requestedTimeoutMs = Number(data.timeoutMs)
      const timeoutMs = Number.isFinite(requestedTimeoutMs) && requestedTimeoutMs > 0 ? requestedTimeoutMs : 30000
      const controller = new AbortController()
      hostedDocumentControllers.set(requestId, controller)
      const timeoutId = window.setTimeout(() => controller.abort('timeout'), timeoutMs)
      try {
        const parsed = await parseHostedDocument(file, { timeoutMs, signal: controller.signal })
        if (!parsed?.document) throw new Error('Document parser returned an invalid response.')
        respond({ ok: true, result: parsed.document })
      } catch (caught: any) {
        if (controller.signal.aborted && controller.signal.reason === 'timeout') {
          respond({ ok: false, error: 'Document parsing timed out.', code: 'document_parse_timeout' })
          return
        }
        if (controller.signal.aborted) return
        throw caught
      } finally {
        window.clearTimeout(timeoutId)
        if (hostedDocumentControllers.get(requestId) === controller) {
          hostedDocumentControllers.delete(requestId)
        }
      }
      return
    }
    respond({ ok: false, error: `Unsupported hosted surface method: ${method}` })
  } catch (caught: any) {
    const bridgeError = normalizeHostedBridgeError(caught)
    respond({
      ok: false,
      error: bridgeError.message,
      code: bridgeError.code,
      details: {
        surface: `${props.surface.kind}:${props.surface.id}`,
        method,
        actionId: actionId || undefined,
        cause: bridgeError.details,
      },
      status: bridgeError.status,
    })
  }
}

async function refreshContext() {
  if (props.surface.mode !== 'hosted-tsx' || !componentMounted || !hostedDocument.value) return
  const requestGeneration = hostedRequestGeneration
  const context = await readContextInOrder()
  if (!componentMounted || requestGeneration !== hostedRequestGeneration) return
  const targetOrigin = trustedIframeOrigin.value === 'null' ? '*' : trustedIframeOrigin.value
  iframeRef.value?.contentWindow?.postMessage({
    type: 'neko-hosted-surface-context',
    context,
  }, targetOrigin)
}

onMounted(() => {
  componentMounted = true
  window.addEventListener('message', handleMessage)
  loadHostedTsx()
})

onBeforeUnmount(() => {
  componentMounted = false
  sourceController?.abort('surface-disposed')
  sourceController = null
  currentLoadId += 1
  hostedRequestGeneration += 1
  abortAllHostedRequests('surface-disposed')
  window.removeEventListener('message', handleMessage)
})

watch(
  // Static iframe messages must be queued again only when its document is
  // actually replaced. Locale changes leave a static iframe in place.
  () => [props.pluginId, props.surface.mode, surfaceUrl.value],
  () => {
    staticSurfaceReady.value = false
    pendingStaticSurfaceMessages.length = 0
  },
)

watch(
  () => [
    props.pluginId,
    props.surface.kind,
    props.surface.id,
    props.surface.mode,
    props.surface.entry,
    props.surface.available,
    surfaceUrl.value,
  ],
  (current, previous) => {
    // Metadata refresh replaces surface objects even when the document identity
    // is unchanged. A newly allocated watch tuple is not a document change.
    if (current.every((value, index) => Object.is(value, previous[index]))) return
    documentLocale.value = null
    void loadHostedTsx()
  },
  { flush: 'sync' },
)

watch(
  () => [locale.value, props.surface.mode, props.pluginId, props.surface.id, props.surface.entry, surfaceTitle.value],
  (current, previous) => {
    if (current.slice(1, 5).some((value, index) => !Object.is(value, previous[index + 1]))) return
    if (current.every((value, index) => Object.is(value, previous[index]))) return
    // Before a document exists a newer locale may replace pending source loads.
    // After publication, retain its input state until an explicit confirmed reload.
    if (props.surface.mode === 'static') return
    if (!hostedDocument.value || (props.surface.mode === 'markdown' && current[0] === previous[0] && documentLocale.value === String(locale.value))) void loadHostedTsx()
  },
  { flush: 'sync' },
)

async function applyDocumentLocale() {
  const generation = hostedRequestGeneration
  try {
    await ElMessageBox.confirm(t('plugins.unsavedChangesWarning'), t('common.warning'), { type: 'warning' })
  } catch { return }
  if (!componentMounted || generation !== hostedRequestGeneration) return
  void loadHostedTsx()
}

watch(
  () => [props.active, props.activationRevision] as const,
  () => postActivation(),
)

defineExpose({
  sendSurfaceMessage,
  refreshContext,
})
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

.hosted-surface-frame__locale-notice { position: absolute; bottom: 8px; right: 8px; z-index: 1; max-width: calc(100% - 16px); padding: 8px; border: 1px solid var(--el-border-color); border-radius: 8px; background: var(--el-bg-color-overlay); color: var(--el-text-color-primary); font-size: 12px; }

.hosted-surface-frame__runtime-alert {
  margin: 12px;
}

.hosted-surface-frame__iframe {
  width: 100%;
  height: 100%;
  min-height: inherit;
  border: none;
  display: block;
}

.hosted-surface-frame__placeholder {
  height: 100%;
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
