<template>
  <div class="app-root">
    <div class="window-titlebar">
      <span class="titlebar-text">{{ t('app.titleSuffix') }}</span>
      <button class="titlebar-close" :title="t('common.close')" @click="closeWindow">✕</button>
    </div>

    <div class="app-shell">
      <aside class="app-sidebar">
        <Sidebar />
      </aside>

      <div class="app-body">
        <div v-if="connectionStore.disconnected" class="connection-banner">
          <div class="connection-banner__inner">
            ⚠️ {{ t('common.disconnected') }}
          </div>
        </div>

        <header class="app-header">
          <Header />
        </header>

        <main class="app-main">
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
import Sidebar from './Sidebar.vue'
import Header from './Header.vue'
import { useI18n } from 'vue-i18n'
import { useConnectionStore } from '@/stores/connection'

const { t } = useI18n()
const connectionStore = useConnectionStore()

function closeWindow() {
  window.close()
}
</script>

<style scoped>
.app-root {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Title bar ── */
.window-titlebar {
  background: linear-gradient(135deg, #4BD4FD, #17A7FF);
  padding: 0 10px 0 16px;
  height: 36px;
  min-height: 36px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  -webkit-app-region: drag;
  user-select: none;
  z-index: 9999;
}

.titlebar-text {
  font-size: 12.5px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 0.4px;
}

.titlebar-close {
  -webkit-app-region: no-drag;
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.85);
  cursor: pointer;
  width: 28px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  font-size: 13px;
  transition: background 0.15s, color 0.15s;
}

.titlebar-close:hover {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

/* ── Shell layout ── */
.app-shell {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.app-sidebar {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  background: var(--el-bg-color);
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
  border-bottom: 1px solid color-mix(in srgb, var(--el-border-color) 30%, transparent);
  background: var(--el-bg-color);
}

.app-main {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: var(--el-bg-color-page);
}

/* ── Connection banner ── */
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

/* ── Page transition ── */
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
