/**
 * Yui Tutorial Bridge — 插件面板与首页教程的跨域 handoff 桥
 *
 * 读取 URL 查询参数中的 handoff 信号（yui_guide=1），
 * 管理插件面板内的教程生命周期。
 *
 * 信号流：
 *   首页 → /api/agent/user_plugin/dashboard?yui_guide=1&... → /ui/?yui_guide=1&...
 *   插件面板完成教程 → postMessage('neko:yui-guide:plugin-dashboard-complete') → 首页
 */
import { ref, computed, readonly } from 'vue'

export interface YuiTutorialState {
  isActive: boolean
  flowId: string
  sourcePage: string
  resumeScene: string
  handoffToken: string
}

const QUERY_KEY_GUIDE = 'yui_guide'
const QUERY_KEY_FLOW_ID = 'flow_id'
const QUERY_KEY_SOURCE_PAGE = 'source_page'
const QUERY_KEY_RESUME_SCENE = 'resume_scene'
const QUERY_KEY_HANDOFF_TOKEN = 'handoff_token'

const COMPLETE_MESSAGE_TYPE = 'neko:yui-guide:plugin-dashboard-complete'
const DEFAULT_OPENER_ORIGIN = normalizeOrigin(import.meta.env.VITE_YUI_TUTORIAL_OPENER_ORIGIN || '')
const ALLOWED_OPENER_ORIGINS = new Set(
  [import.meta.env.VITE_YUI_TUTORIAL_ALLOWED_OPENER_ORIGINS || '', DEFAULT_OPENER_ORIGIN]
    .flatMap((value) => String(value || '').split(','))
    .map((value) => normalizeOrigin(value))
    .filter(Boolean),
)

const state = ref<YuiTutorialState>({
  isActive: false,
  flowId: '',
  sourcePage: '',
  resumeScene: '',
  handoffToken: ''
})

let _initialized = false

function normalizeOrigin(value: string) {
  const normalizedValue = String(value || '').trim()
  if (!normalizedValue) {
    return ''
  }

  try {
    return new URL(normalizedValue).origin
  } catch {
    return ''
  }
}

function resetState() {
  state.value.isActive = false
  state.value.flowId = ''
  state.value.sourcePage = ''
  state.value.resumeScene = ''
  state.value.handoffToken = ''
}

function parseQueryParams(): Partial<YuiTutorialState> {
  const params = new URLSearchParams(window.location.search)
  if (params.get(QUERY_KEY_GUIDE) !== '1') {
    return {}
  }

  return {
    isActive: true,
    flowId: params.get(QUERY_KEY_FLOW_ID) || '',
    sourcePage: params.get(QUERY_KEY_SOURCE_PAGE) || '',
    resumeScene: params.get(QUERY_KEY_RESUME_SCENE) || '',
    handoffToken: params.get(QUERY_KEY_HANDOFF_TOKEN) || ''
  }
}

function cleanUrl() {
  const url = new URL(window.location.href)
  url.searchParams.delete(QUERY_KEY_GUIDE)
  url.searchParams.delete(QUERY_KEY_FLOW_ID)
  url.searchParams.delete(QUERY_KEY_SOURCE_PAGE)
  url.searchParams.delete(QUERY_KEY_RESUME_SCENE)
  url.searchParams.delete(QUERY_KEY_HANDOFF_TOKEN)
  window.history.replaceState({}, '', url.toString())
}

export function getTrustedOpenerOrigin() {
  if (!window.opener || window.opener.closed) {
    return DEFAULT_OPENER_ORIGIN
  }

  try {
    const openerOrigin = window.opener.location.origin
    if (openerOrigin && (openerOrigin === window.location.origin || ALLOWED_OPENER_ORIGINS.has(openerOrigin))) {
      return openerOrigin
    }
  } catch {
    // Cross-origin opener access is expected here.
  }

  return DEFAULT_OPENER_ORIGIN
}

export function useYuiTutorialBridge() {
  function init() {
    if (_initialized) return
    _initialized = true

    const parsed = parseQueryParams()
    if (parsed.isActive) {
      Object.assign(state.value, parsed)
      cleanUrl()
    }
  }

  function complete(detail?: Record<string, unknown>) {
    if (!state.value.isActive) return

    const payload = {
      type: COMPLETE_MESSAGE_TYPE,
      detail: {
        handoff_token: state.value.handoffToken,
        flow_id: state.value.flowId,
        resume_scene: state.value.resumeScene,
        ...detail
      }
    }

    const openerOrigin = getTrustedOpenerOrigin()
    if (window.opener && !window.opener.closed && openerOrigin) {
      try {
        window.opener.postMessage(payload, openerOrigin)
      } catch {
        // cross-origin may block
      }
    }

    resetState()
  }

  function dismiss() {
    resetState()
  }

  const isActive = computed(() => state.value.isActive)
  const flowId = computed(() => state.value.flowId)
  const resumeScene = computed(() => state.value.resumeScene)

  return {
    init,
    complete,
    dismiss,
    isActive: readonly(isActive),
    flowId: readonly(flowId),
    resumeScene: readonly(resumeScene),
    state: readonly(state)
  }
}
