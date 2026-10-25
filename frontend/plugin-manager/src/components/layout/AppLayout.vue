<template>
  <div class="app-root">
    <div class="window-titlebar">
      <div class="titlebar-left">
        <img src="@/assets/paw.png" alt="" class="titlebar-paw" draggable="false" />
        <span class="titlebar-text">{{ t('app.titleSuffix') }}</span>
      </div>
      <div class="titlebar-actions">
        <Header />
        <div class="titlebar-controls">
          <button
            v-if="pinAvailable"
            class="titlebar-control titlebar-control--pin"
            :class="{ 'is-pinned': isPinned }"
            type="button"
            :title="pinLabel"
            :aria-label="pinLabel"
            :aria-pressed="isPinned"
            :disabled="pinPending"
            @click="togglePin"
          >
            <span class="titlebar-pin-icon" aria-hidden="true"></span>
          </button>
          <button
            class="titlebar-control"
            type="button"
            :title="t('common.minimize')"
            :aria-label="t('common.minimize')"
            @click="minimizeWindow"
          >
            <span class="titlebar-minimize-icon" aria-hidden="true"></span>
          </button>
          <button
            class="titlebar-control"
            type="button"
            :title="maximizeLabel"
            :aria-label="maximizeLabel"
            @click="toggleMaximize"
          >
            <span class="titlebar-maximize-icon" :class="{ 'is-restored': isMaximized }" aria-hidden="true"></span>
          </button>
          <button
            class="titlebar-control titlebar-control--close"
            type="button"
            :title="t('common.close')"
            :aria-label="t('common.close')"
            @click="closeWindow"
          >
            <svg class="titlebar-close-icon" viewBox="0 0 10 10" fill="none" aria-hidden="true" focusable="false">
              <path d="M1 1L9 9M9 1L1 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
      </div>
    </div>

    <div class="app-shell">
      <aside class="app-sidebar" :class="{ 'app-sidebar--collapsed': sidebarCollapsed }">
        <Sidebar @collapse-change="sidebarCollapsed = $event" />
      </aside>

      <div class="app-body">
        <div v-if="connectionStore.disconnected" class="connection-banner">
          <div class="connection-banner__inner">
            {{ t('common.disconnected') }}
          </div>
        </div>

        <!-- A zero-height anchor keeps updates below the connection banner
             without reserving a separate row above the page. -->
        <div class="update-float-anchor">
          <PluginUpdateFloatWindow v-if="updateFloatWindowLoaded" />
        </div>

        <main class="app-main" data-yui-guide-id="plugin-main">
          <router-view v-slot="{ Component, route: currentRoute }">
            <MotionTransition preset="page">
              <component :is="Component" :key="currentRoute.path" />
            </MotionTransition>
          </router-view>
        </main>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch, type WatchStopHandle } from 'vue'
import MotionTransition from '@/motion/MotionTransition.vue'
import Sidebar from './Sidebar.vue'
import Header from './Header.vue'
import { useI18n } from 'vue-i18n'
import { useConnectionStore } from '@/stores/connection'

const { t } = useI18n()
const connectionStore = useConnectionStore()
const sidebarCollapsed = ref(false)

// The update check, its popup and the install-progress UI stay off the
// cold-start path (payload-budget e2e): the store is imported when the boot
// check fires, and the popup is fetched the first time it opens, then kept
// mounted so its close-time cleanup still runs on every later close.
const PluginUpdateFloatWindow = defineAsyncComponent(
  () => import('@/components/plugin/PluginUpdateFloatWindow.vue'),
)
const updateFloatWindowLoaded = ref(false)
let stopUpdatePopupWatch: WatchStopHandle | null = null
let updateCheckTimer: number | null = null
let updateCheckDisposed = false

async function startUpdateCheck(): Promise<void> {
  const { usePluginUpdatesStore } = await import('@/stores/pluginUpdates')
  if (updateCheckDisposed) return
  const pluginUpdatesStore = usePluginUpdatesStore()
  // `immediate` also catches a popup the plugin list's toolbar button opened
  // before this module finished loading.
  stopUpdatePopupWatch = watch(() => pluginUpdatesStore.popupOpen, (open) => {
    if (open) updateFloatWindowLoaded.value = true
  }, { immediate: true })
  await pluginUpdatesStore.checkOnBoot()
}
const PLUGIN_MANAGER_BOOT_SHELL_ID = 'plugin-manager-boot-shell'
/** Let the first paint and the initial plugin fetch win the bandwidth race. */
const UPDATE_CHECK_BOOT_DELAY_MS = 1200
const PIN_STATE_RETRY_DELAYS_MS = [50, 150, 350, 750]
const isMaximized = ref(false)
const pinAvailable = ref(false)
const isPinned = ref(false)
const pinPending = ref(false)
let pinRequestGeneration = 0
let pinRetryTimer: number | null = null
let pinDisposed = false
const pinLabel = computed(() => isPinned.value ? t('common.unpinWindow') : t('common.pinWindow'))
const maximizeLabel = computed(() => isMaximized.value ? t('common.restore') : t('common.maximize'))

function getWindowControlApi() {
  return window.nekoWindowControl
}

async function refreshMaximizeState() {
  const api = getWindowControlApi()
  if (!api || typeof api.isMaximized !== 'function') return
  try {
    isMaximized.value = !!(await api.isMaximized())
  } catch {
    // 非桌面窗口环境下忽略状态查询失败
  }
}

function applyPinState(state: NekoWindowControlResult | null | undefined) {
  if (!state) {
    pinAvailable.value = false
    isPinned.value = false
    return
  }
  pinAvailable.value = !!state.available
  isPinned.value = !!state.pinned
}

function clearPinStateRetry() {
  if (pinRetryTimer === null) return
  window.clearTimeout(pinRetryTimer)
  pinRetryTimer = null
}

function schedulePinStateRetry(generation: number, retryIndex: number) {
  if (
    pinDisposed
    || generation !== pinRequestGeneration
    || retryIndex >= PIN_STATE_RETRY_DELAYS_MS.length
  ) return
  clearPinStateRetry()
  pinRetryTimer = window.setTimeout(() => {
    pinRetryTimer = null
    if (pinDisposed || generation !== pinRequestGeneration) return
    void refreshPinState({ generation, retryIndex: retryIndex + 1 })
  }, PIN_STATE_RETRY_DELAYS_MS[retryIndex])
}

async function refreshPinState(retryContext?: { generation: number; retryIndex: number }) {
  if (pinPending.value) return
  const isRetry = !!(
    retryContext
    && retryContext.generation === pinRequestGeneration
    && Number.isInteger(retryContext.retryIndex)
  )
  if (!isRetry) clearPinStateRetry()
  const generation = isRetry ? retryContext.generation : ++pinRequestGeneration
  const retryIndex = isRetry ? retryContext.retryIndex : 0
  const api = getWindowControlApi()
  if (!api || typeof api.getPinState !== 'function') {
    if (generation === pinRequestGeneration) applyPinState(null)
    schedulePinStateRetry(generation, retryIndex)
    return
  }
  try {
    const state = await api.getPinState()
    if (pinDisposed || generation !== pinRequestGeneration) return
    applyPinState(state)
    if (!state || !state.available) schedulePinStateRetry(generation, retryIndex)
  } catch {
    if (pinDisposed || generation !== pinRequestGeneration) return
    applyPinState(null)
    schedulePinStateRetry(generation, retryIndex)
  }
}

async function togglePin() {
  const api = getWindowControlApi()
  if (!api || typeof api.togglePin !== 'function') return
  if (pinPending.value) return
  clearPinStateRetry()
  pinPending.value = true
  const generation = ++pinRequestGeneration
  let refreshAfterFailure = false
  try {
    const state = await api.togglePin()
    if (generation === pinRequestGeneration) applyPinState(state)
  } catch {
    refreshAfterFailure = true
  } finally {
    if (generation === pinRequestGeneration) pinPending.value = false
  }
  if (refreshAfterFailure && generation === pinRequestGeneration) await refreshPinState()
}

async function minimizeWindow() {
  const api = getWindowControlApi()
  if (!api || typeof api.minimize !== 'function') return
  try {
    await api.minimize()
  } catch {
    // 非桌面窗口环境下忽略最小化失败
  }
}

async function toggleMaximize() {
  const api = getWindowControlApi()
  if (!api || typeof api.maximize !== 'function') return
  try {
    const result = await api.maximize()
    if (result && result.ok && typeof result.isMaximized === 'boolean') {
      isMaximized.value = result.isMaximized
      return
    }
    await refreshMaximizeState()
  } catch {
    // 非桌面窗口环境下忽略最大化失败
  }
}

function handleWindowResize() {
  void refreshMaximizeState()
}

function handleWindowFocus() {
  void refreshPinState()
}

function closeWindow() {
  window.close()
}

onMounted(() => {
  document.getElementById(PLUGIN_MANAGER_BOOT_SHELL_ID)?.remove()
  void refreshPinState()
  void refreshMaximizeState()
  window.addEventListener('resize', handleWindowResize)
  window.addEventListener('focus', handleWindowFocus)
  // Once per panel window; silent on failure and hidden when nothing is
  // outdated, so it can never block the panel from being used.
  updateCheckTimer = window.setTimeout(() => {
    updateCheckTimer = null
    startUpdateCheck().catch((err: unknown) => {
      console.warn('[plugin-updates] boot check could not start', err)
    })
  }, UPDATE_CHECK_BOOT_DELAY_MS)
})

onBeforeUnmount(() => {
  pinDisposed = true
  pinRequestGeneration += 1
  clearPinStateRetry()
  pinPending.value = false
  window.removeEventListener('resize', handleWindowResize)
  window.removeEventListener('focus', handleWindowFocus)
  updateCheckDisposed = true
  if (updateCheckTimer !== null) window.clearTimeout(updateCheckTimer)
  stopUpdatePopupWatch?.()
})
</script>

<style scoped>
.app-root {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Shared surfaces keep the titlebar consistent with the panel theme. */
.window-titlebar {
  padding: 0 6px 0 12px;
  height: 38px;
  min-height: 38px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  -webkit-app-region: drag;
  user-select: none;
  z-index: 9999;
  background: linear-gradient(135deg,
    color-mix(in srgb, var(--surface-inset) 94%, var(--el-color-primary)),
    var(--surface-card)
  );
  border-bottom: 1px solid var(--surface-border);
  box-shadow: var(--surface-shadow);
}

.titlebar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-right: 12px;
}

.titlebar-paw {
  width: 20px;
  height: 16px;
  object-fit: contain;
  flex-shrink: 0;
  filter: brightness(0);
  opacity: 0.4;
}

.titlebar-text {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--el-text-color-primary);
  letter-spacing: 0.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.titlebar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  -webkit-app-region: no-drag;
}

.titlebar-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  padding-left: 12px;
  border-left: 1px solid var(--surface-border);
  -webkit-app-region: no-drag;
}

.titlebar-control {
  -webkit-app-region: no-drag;
  background: transparent;
  border: none;
  color: var(--el-text-color-regular);
  cursor: pointer;
  width: 30px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition:
    background 0.18s ease,
    color 0.18s ease,
    box-shadow 0.18s ease;
}

.titlebar-control:hover {
  background: color-mix(in srgb, var(--el-color-primary) 10%, var(--surface-card));
  color: var(--el-color-primary);
}

.titlebar-control:active {
  background: color-mix(in srgb, var(--el-color-primary) 16%, var(--surface-card));
}

.titlebar-control--close:hover {
  background: color-mix(in srgb, var(--el-color-danger) 10%, var(--surface-card));
  color: var(--el-color-danger);
}

.titlebar-control--pin {
  color: var(--el-text-color-regular);
}

.titlebar-control--pin.is-pinned {
  background: color-mix(in srgb, var(--el-color-primary) 12%, var(--surface-card));
  color: var(--el-color-primary);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--el-color-primary) 28%, var(--surface-border));
}

.titlebar-control--pin.is-pinned:hover {
  background: color-mix(in srgb, var(--el-color-primary) 18%, var(--surface-card));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--el-color-primary) 40%, var(--surface-border));
}

.titlebar-control:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.titlebar-pin-icon {
  position: relative;
  display: block;
  width: 16px;
  height: 16px;
  background-color: currentColor;
  -webkit-mask: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2024%2024'%20fill='none'%20stroke='%23000'%20stroke-width='1.8'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Cpath%20d='M8%203h8l-1%206%203%204H6l3-4-1-6Z'%2F%3E%3Cpath%20d='M12%2013v8'%2F%3E%3C%2Fsvg%3E") center / contain no-repeat;
  mask: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2024%2024'%20fill='none'%20stroke='%23000'%20stroke-width='1.8'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Cpath%20d='M8%203h8l-1%206%203%204H6l3-4-1-6Z'%2F%3E%3Cpath%20d='M12%2013v8'%2F%3E%3C%2Fsvg%3E") center / contain no-repeat;
  transform: rotate(-40deg);
  transform-origin: 50% 68%;
  filter: drop-shadow(0 1px 1px rgba(15, 58, 82, 0.2));
  transition:
    transform 0.24s cubic-bezier(0.22, 1, 0.36, 1),
    filter 0.2s ease;
}

@keyframes neko-plugin-pin-lock {
  0% {
    transform: rotate(-40deg) translateY(0) scale(0.96);
  }
  68% {
    transform: rotate(5deg) translateY(-1px) scale(1.06);
  }
  100% {
    transform: rotate(0deg) translateY(-1px) scale(1);
  }
}

.titlebar-control--pin.is-pinned .titlebar-pin-icon {
  transform: rotate(0deg) translateY(-1px);
  filter: drop-shadow(0 2px 2px rgba(15, 58, 82, 0.26));
  animation: neko-plugin-pin-lock 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

@media (prefers-reduced-motion: reduce) {
  .titlebar-pin-icon {
    transition: none;
  }

  .titlebar-control--pin.is-pinned .titlebar-pin-icon {
    animation: none;
  }
}

.titlebar-minimize-icon {
  width: 12px;
  height: 1.5px;
  border-radius: 999px;
  background: currentColor;
  transform: translateY(4px);
}

.titlebar-maximize-icon {
  position: relative;
  width: 11px;
  height: 11px;
  border: 1.5px solid currentColor;
  border-radius: 2px;
}

.titlebar-maximize-icon.is-restored {
  transform: translate(1.5px, 1.5px);
}

.titlebar-maximize-icon.is-restored::before {
  content: '';
  position: absolute;
  left: -4px;
  top: -4px;
  width: 11px;
  height: 11px;
  border: 1.5px solid currentColor;
  border-radius: 2px;
}

.titlebar-close-icon {
  width: 10px;
  height: 10px;
}

/* 主体布局 */
.app-shell {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
  gap: var(--shell-spacing);
  padding: var(--shell-spacing);
  background: var(--surface-page);
}

.app-sidebar {
  position: relative;
  z-index: 901;
  width: var(--sidebar-width);
  flex-shrink: 0;
  min-height: 0;
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-card);
  background: var(--surface-workspace);
  box-shadow: var(--surface-shadow);
  transition: width var(--motion-duration-normal) var(--motion-ease-standard);
}

.app-sidebar--collapsed { width: var(--sidebar-collapsed-width); }

.app-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-radius: var(--radius-card);
  background: var(--surface-workspace);
}

.update-float-anchor {
  position: relative;
  z-index: 900;
  flex-shrink: 0;
}

.app-main {
  position: relative;
  flex: 1;
  min-height: 0;
  /* Reading pages scroll here. Fill workbenches own their inner scrolling;
     their page-level minimum height keeps controls reachable in short windows. */
  overflow-y: auto;
}

/* 连接状态提示 */
.connection-banner {
  flex-shrink: 0;
  padding: var(--spacing-sm) var(--content-padding) 0;
}

.connection-banner__inner {
  padding: 8px 14px;
  border-radius: var(--radius-control);
  background: color-mix(in srgb, var(--el-color-danger) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--el-color-danger) 20%, var(--el-border-color));
  color: var(--el-color-danger);
  font-size: 13px;
  font-weight: 500;
}

:global(html.dark .titlebar-paw) {
  filter: brightness(0) invert(1);
  opacity: 0.65;
}

@media (max-width: 640px) {
  .app-sidebar { width: var(--sidebar-collapsed-width); }
  .connection-banner { padding-inline: 8px; }
}
@media (prefers-reduced-motion: reduce) {
  .app-sidebar { transition: none; }
}
</style>
