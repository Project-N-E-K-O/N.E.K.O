<template>
  <div
    class="loading-spinner"
    v-if="loading"
    role="status"
    aria-live="polite"
    :aria-busy="loading"
    :aria-label="text || t('common.loading')"
  >
    <el-icon class="is-loading">
      <Loading />
    </el-icon>
    <span v-if="text" class="loading-text">{{ text }}</span>
    <span v-else class="sr-only">{{ t('common.loading') }}</span>
  </div>
</template>

<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

interface Props {
  loading: boolean
  text?: string
}

defineProps<Props>()
const { t } = useI18n()
</script>

<style scoped>
.loading-spinner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 20px;
  color: var(--el-color-primary);
}

.loading-text {
  font-size: 14px;
}

.is-loading {
  animation: rotating 2s linear infinite;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@keyframes rotating {
  0% {
    transform: rotate(0deg);
  }
  100% {
    transform: rotate(360deg);
  }
}
</style>

