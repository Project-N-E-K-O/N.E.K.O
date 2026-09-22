<template>
  <el-config-provider
    :locale="elementLocale"
    :z-index="ELEMENT_Z_INDEX"
    :message="ELEMENT_MESSAGE_CONFIG"
  >
    <div v-if="localeLoadState.pending" class="locale-bootstrap-shell" role="status">
      {{ $t('common.languageLoading') }}
    </div>
    <router-view v-else />
    <LocaleLoadNotice />
  </el-config-provider>
</template>

<script setup lang="ts">
import { elementLocale, localeLoadState } from './i18n'
import LocaleLoadNotice from './components/common/LocaleLoadNotice.vue'

const ELEMENT_Z_INDEX = 12000
const ELEMENT_MESSAGE_CONFIG = { offset: 54 }
</script>

<style scoped>
.locale-bootstrap-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  color: var(--el-text-color-secondary);
}
</style>
