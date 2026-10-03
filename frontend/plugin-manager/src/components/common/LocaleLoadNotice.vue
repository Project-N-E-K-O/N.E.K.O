<template>
  <div
    v-if="localeLoadState.pending || localeLoadState.error"
    class="locale-load-notice"
    role="status"
    data-testid="locale-load-notice"
  >
    <span v-if="localeLoadState.pending"
      >{{ t('common.languageLoading') }} ({{ localeLoadState.pending }})</span
    >
    <template v-else>
      <span>{{ t('common.languageLoadFailed') }}</span>
      <button
        v-if="!localeLoadState.reloadRequired"
        type="button"
        data-testid="locale-retry"
        @click="retryLocale()"
      >
        {{ t('common.languageRetry') }}
      </button>
      <button type="button" data-testid="locale-reload" @click="reloadLocalePage">
        {{ t('common.languageReload') }}
      </button>
    </template>
  </div>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { localeLoadState, retryLocale, reloadLocalePage } from '@/i18n'
const { t } = useI18n()
</script>
<style scoped>
.locale-load-notice {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 12001;
  max-width: min(440px, calc(100vw - 32px));
  padding: 10px 14px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
  background: var(--el-bg-color-overlay);
  color: var(--el-text-color-primary);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  font-size: 13px;
}
button {
  cursor: pointer;
  padding: 4px 8px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  color: var(--el-color-primary);
}
</style>
