<template>
  <el-alert
    :title="title"
    :type="type"
    :description="message"
    :closable="closable"
    show-icon
  />
</template>

<script setup lang="ts">
interface Props {
  title?: string
  message: string
  type?: 'success' | 'warning' | 'info' | 'error'
  closable?: boolean
}

withDefaults(defineProps<Props>(), {
  title: '错误',
  type: 'error',
  closable: true
})
</script>

<style scoped>
:deep(.el-alert) {
  margin: 20px 0;
}
</style>

