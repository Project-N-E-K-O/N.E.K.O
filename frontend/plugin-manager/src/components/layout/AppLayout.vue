<template>
  <div class="app-root">
    <div class="window-titlebar">
      <div class="titlebar-left">
        <img src="@/assets/paw.png" alt="" class="titlebar-paw" draggable="false" />
        <span class="titlebar-text">{{ t('app.titleSuffix') }}</span>
      </div>
      <div class="titlebar-controls">
        <button
          v-if="canPinWindow"
          class="titlebar-control"
          :class="{ 'is-pinned': isPinned }"
          type="button"
          :title="pinLabel"
          :aria-label="pinLabel"
          :aria-pressed="isPinned"
          @click="toggleAlwaysOnTop"
        >
          <span class="titlebar-pin-icon" :class="{ 'is-pinned': isPinned }" aria-hidden="true"></span>
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

    <div class="app-shell">
      <aside class="app-sidebar">
        <Sidebar />
      </aside>

      <div class="app-body">
        <div v-if="connectionStore.disconnected" class="connection-banner">
          <div class="connection-banner__inner">
            {{ t('common.disconnected') }}
          </div>
        </div>

        <header class="app-header">
          <Header />
        </header>

        <main class="app-main" data-yui-guide-id="plugin-main">
          <router-view v-slot="{ Component, route: currentRoute }">
            <Transition name="page" mode="out-in">
              <component :is="Component" :key="currentRoute.path" />
            </Transition>
          </router-view>
        </main>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Sidebar from './Sidebar.vue'
import Header from './Header.vue'
import { useI18n } from 'vue-i18n'
import { useConnectionStore } from '@/stores/connection'

const { t } = useI18n()
const connectionStore = useConnectionStore()
const isMaximized = ref(false)
const canPinWindow = ref(false)
const isPinned = ref(false)
const maximizeLabel = computed(() => isMaximized.value ? t('common.restore') : t('common.maximize'))
const pinLabel = computed(() => isPinned.value ? t('common.unpinWindow') : t('common.pinWindow'))

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

async function refreshAlwaysOnTopState() {
  const api = getWindowControlApi()
  if (!api || typeof api.getAlwaysOnTopState !== 'function') return
  try {
    const state = await api.getAlwaysOnTopState()
    canPinWindow.value = !!(state && state.allowed)
    isPinned.value = canPinWindow.value && !!state.pinned
  } catch {
    canPinWindow.value = false
    isPinned.value = false
  }
}

async function toggleAlwaysOnTop() {
  const api = getWindowControlApi()
  if (!api || typeof api.toggleAlwaysOnTop !== 'function') return
  try {
    const state = await api.toggleAlwaysOnTop()
    canPinWindow.value = !!(state && state.allowed)
    isPinned.value = canPinWindow.value && !!state.pinned
  } catch {
    await refreshAlwaysOnTopState()
  }
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
  void refreshMaximizeState()
  void refreshAlwaysOnTopState()
}

function closeWindow() {
  window.close()
}

onMounted(() => {
  void refreshMaximizeState()
  void refreshAlwaysOnTopState()
  window.addEventListener('resize', handleWindowResize)
  window.addEventListener('focus', handleWindowFocus)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
  window.removeEventListener('focus', handleWindowFocus)
})
</script>

<style scoped>
.app-root {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 标题栏玻璃效果 */
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
  background:
    linear-gradient(135deg,
      rgba(75, 212, 253, 0.82) 0%,
      rgba(23, 167, 255, 0.78) 50%,
      rgba(91, 141, 239, 0.80) 100%
    );
  backdrop-filter: blur(48px) saturate(180%);
  -webkit-backdrop-filter: blur(48px) saturate(180%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.25);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.3),
    inset 0 -0.5px 0 rgba(255, 255, 255, 0.12),
    0 1px 6px rgba(23, 120, 200, 0.12);
}

.titlebar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.titlebar-paw {
  width: 20px;
  height: 16px;
  object-fit: contain;
  filter: brightness(0) invert(1);
  opacity: 0.9;
}

.titlebar-text {
  font-size: 12.5px;
  font-weight: 650;
  color: #fff;
  letter-spacing: 0.5px;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}

.titlebar-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  -webkit-app-region: no-drag;
}

.titlebar-control {
  -webkit-app-region: no-drag;
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.75);
  cursor: pointer;
  width: 30px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: background 0.18s, color 0.18s;
}

.titlebar-control:hover {
  background: rgba(255, 255, 255, 0.18);
  color: #fff;
}

.titlebar-control:active {
  background: rgba(0, 0, 0, 0.08);
}

.titlebar-control.is-pinned {
  color: #fff;
  background: rgba(255, 255, 255, 0.22);
}

.titlebar-pin-icon {
  position: relative;
  width: 14px;
  height: 14px;
  transform: rotate(-42deg);
  transform-origin: 50% 55%;
  transition: transform 0.18s ease;
}

.titlebar-pin-icon::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0;
  width: 10px;
  height: 7px;
  border: 1.5px solid currentColor;
  border-radius: 5px 5px 2px 2px;
  box-sizing: border-box;
}

.titlebar-pin-icon::after {
  content: '';
  position: absolute;
  left: 6px;
  top: 7px;
  width: 1.5px;
  height: 7px;
  border-radius: 2px;
  background: currentColor;
}

.titlebar-pin-icon.is-pinned {
  transform: rotate(0deg);
}

.titlebar-control--close:hover {
  background: rgba(255, 255, 255, 0.22);
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
  overflow: hidden;
}

.app-sidebar {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(32px) saturate(160%);
  -webkit-backdrop-filter: blur(32px) saturate(160%);
  overflow-y: auto;
}

.app-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

.app-header {
  height: 54px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding: 0 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(32px) saturate(160%);
  -webkit-backdrop-filter: blur(32px) saturate(160%);
  box-shadow:
    inset 0 -0.5px 0 rgba(255, 255, 255, 0.15),
    0 1px 4px rgba(100, 120, 160, 0.04);
}

.app-main {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: var(--el-bg-color-page);
}

/* 连接状态提示 */
.connection-banner {
  padding: 8px 20px 0;
}

.connection-banner__inner {
  padding: 8px 14px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--el-color-danger) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--el-color-danger) 20%, var(--el-border-color));
  color: var(--el-color-danger);
  font-size: 13px;
  font-weight: 500;
}

/* 页面切换动画 */
.page-enter-active {
  transition:
    opacity 0.3s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    filter 0.3s ease;
}

.page-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s ease,
    filter 0.18s ease;
}

.page-enter-from {
  opacity: 0;
  transform: scale(0.98) translateY(8px);
  filter: blur(4px);
}

.page-leave-to {
  opacity: 0;
  transform: scale(0.99) translateY(-4px);
  filter: blur(2px);
}

/* 深色模式覆盖 */
html.dark .window-titlebar {
  background:
    linear-gradient(135deg,
      rgba(50, 50, 72, 0.75) 0%,
      rgba(38, 38, 58, 0.70) 50%,
      rgba(45, 42, 68, 0.72) 100%
    );
  border-bottom-color: rgba(255, 255, 255, 0.08);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.08),
    inset 0 -0.5px 0 rgba(255, 255, 255, 0.04),
    0 1px 4px rgba(0, 0, 0, 0.2);
}

html.dark .app-sidebar {
  background: rgba(28, 28, 46, 0.78);
  border-right-color: rgba(255, 255, 255, 0.06);
}

html.dark .app-header {
  background: rgba(28, 28, 46, 0.72);
  border-bottom-color: rgba(255, 255, 255, 0.06);
  box-shadow:
    inset 0 -0.5px 0 rgba(255, 255, 255, 0.04),
    0 1px 4px rgba(0, 0, 0, 0.12);
}

@media (prefers-reduced-motion: reduce) {
  .page-enter-active,
  .page-leave-active {
    transition: opacity 0.15s ease;
  }

  .page-enter-from,
  .page-leave-to {
    transform: none;
    filter: none;
  }
}
</style>
