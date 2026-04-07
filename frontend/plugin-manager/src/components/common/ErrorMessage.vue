<template>
  <el-alert
    :title="props.title ?? t('common.error')"
    :type="props.type"
    :description="props.message"
    :closable="props.closable"
    show-icon
  />
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

interface Props {
  title?: string
  message: string
  type?: 'success' | 'warning' | 'info' | 'error'
  closable?: boolean
}

const { t } = useI18n()

const props = withDefaults(defineProps<Props>(), {
  type: 'error',
  closable: true
})
</script>

<style scoped>
:deep(.el-alert) {
  margin: 20px 0;
}
</style>

