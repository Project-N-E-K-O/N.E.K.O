<template>
  <AppLayout />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import AppLayout from '@/components/layout/AppLayout.vue'

// 调试：确认组件已加载
onMounted(() => {
  console.log('App component mounted')
})
</script>

<style>
/* 全局样式已在 main.css 中定义 */
</style>
