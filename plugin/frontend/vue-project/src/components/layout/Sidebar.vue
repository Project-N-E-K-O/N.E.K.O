<template>
  <div class="sidebar">
    <div class="sidebar-header">
      <h2 class="sidebar-title">N.E.K.O</h2>
    </div>
    <el-menu
      :default-active="activeRoute"
      router
      class="sidebar-menu"
      :collapse="false"
    >
      <el-menu-item index="/">
        <el-icon><Odometer /></el-icon>
        <span>{{ $t('nav.dashboard') }}</span>
      </el-menu-item>
      <el-menu-item index="/plugins">
        <el-icon><Box /></el-icon>
        <span>{{ $t('nav.plugins') }}</span>
      </el-menu-item>
      <el-menu-item index="/runs">
        <el-icon><DataAnalysis /></el-icon>
        <span>{{ $t('nav.runs') }}</span>
      </el-menu-item>
      <el-menu-item index="/logs/_server">
        <el-icon><Monitor /></el-icon>
        <span>{{ $t('nav.serverLogs') }}</span>
      </el-menu-item>

      <!-- 适配器分组 -->
      <el-sub-menu v-if="adapters.length > 0" index="adapters">
        <template #title>
          <el-icon><Connection /></el-icon>
          <span>{{ $t('nav.adapters') }}</span>
        </template>
        <el-menu-item
          v-for="adapter in adapters"
          :key="adapter.id"
          :index="`/adapter/${adapter.id}/ui`"
        >
          <el-icon><Link /></el-icon>
          <span>{{ adapter.name }}</span>
        </el-menu-item>
      </el-sub-menu>
    </el-menu>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Odometer, Box, Monitor, DataAnalysis, Connection, Link } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'

const route = useRoute()
const pluginStore = usePluginStore()

// 获取所有适配器
const adapters = computed(() => {
  return pluginStore.pluginsWithStatus.filter(p => p.type === 'adapter')
})

const activeRoute = computed(() => {
  if (route.path.startsWith('/adapter/')) {
    return route.path
  }
  if (route.path.startsWith('/plugins')) {
    return '/plugins'
  }
  if (route.path.startsWith('/runs')) {
    return '/runs'
  }
  if (route.path.startsWith('/logs')) {
    return '/logs/_server'
  }
  return route.path
})

onMounted(() => {
  // 确保插件列表已加载
  if (pluginStore.pluginsWithStatus.length === 0) {
    pluginStore.fetchPlugins()
  }
})
</script>

<style scoped>
.sidebar {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--el-bg-color);
}

.sidebar-header {
  padding: 20px;
  border-bottom: 1px solid var(--el-border-color-light);
}

.sidebar-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.sidebar-menu {
  flex: 1;
  border-right: none;
}
</style>

