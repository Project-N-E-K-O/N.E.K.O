<template>
  <div class="sidebar">
    <div class="sidebar-header">
      <h2 class="sidebar-title">{{ $t('nav.plugins') }}</h2>
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
      <el-menu-item index="/logs/_server">
        <el-icon><Monitor /></el-icon>
        <span>{{ $t('nav.serverLogs') }}</span>
      </el-menu-item>
    </el-menu>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Odometer, Box, Monitor } from '@element-plus/icons-vue'

const route = useRoute()

const activeRoute = computed(() => {
  return route.path
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

