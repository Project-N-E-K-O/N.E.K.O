<template>
  <div class="app-root">
    <div class="window-titlebar">
      <span class="titlebar-text">{{ t('app.titleSuffix') }}</span>
      <button class="titlebar-close" @click="closeWindow" :title="t('common.close')">
        <el-icon :size="18"><Close /></el-icon>
      </button>
    </div>
    <el-container class="app-layout">
      <el-aside width="240px" class="sidebar-container">
        <Sidebar />
      </el-aside>
      <el-container>
        <div v-if="connectionStore.disconnected" class="connection-banner">
          <el-alert
            :title="t('common.disconnected')"
            type="error"
            :closable="false"
            show-icon
          />
        </div>
        <el-header height="60px" class="header-container">
          <Header />
        </el-header>
        <el-main class="main-container">
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import Sidebar from './Sidebar.vue'
import Header from './Header.vue'
import { Close } from '@element-plus/icons-vue'
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

.window-titlebar {
  background: linear-gradient(to right, #4BD4FD, #17A7FF);
  padding: 0 8px 0 16px;
  height: 38px;
  min-height: 38px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  -webkit-app-region: drag;
  user-select: none;
  z-index: 9999;
}

.titlebar-text {
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 0.5px;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

.titlebar-close {
  -webkit-app-region: no-drag;
  background: transparent;
  border: none;
  color: #fff;
  cursor: pointer;
  width: 32px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: background 0.15s;
}

.titlebar-close:hover {
  background: rgba(255, 255, 255, 0.25);
}

.titlebar-close:active {
  background: rgba(0, 0, 0, 0.1);
}

.app-layout {
  flex: 1;
  overflow: hidden;
}

.sidebar-container {
  background-color: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-light);
}

.header-container {
  background-color: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  display: flex;
  align-items: center;
  padding: 0 20px;
}

.main-container {
  background-color: var(--el-bg-color-page);
  padding: 20px;
  overflow-y: auto;
}

.connection-banner {
  padding: 8px 20px 0 20px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

