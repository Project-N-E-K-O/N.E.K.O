<template>
  <el-config-provider
    :locale="elementLocale"
    :z-index="ELEMENT_Z_INDEX"
    :message="ELEMENT_MESSAGE_CONFIG"
  >
    <div v-if="localeBootstrapping" class="locale-bootstrap-shell" role="status">
      {{ $t('common.languageLoading') }}
    </div>
    <router-view v-else />
    <LocaleLoadNotice />
  </el-config-provider>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { elementLocale, localeLoadState } from './i18n'
import LocaleLoadNotice from './components/common/LocaleLoadNotice.vue'

const ELEMENT_Z_INDEX = 12000
const ELEMENT_MESSAGE_CONFIG = { offset: 54 }
const localeBootstrapping = ref(localeLoadState.pending !== null)
watch(
  () => localeLoadState.pending,
  (pending) => {
    if (pending === null) localeBootstrapping.value = false
  },
  { immediate: true },
)
</script>

<style scoped>
.locale-bootstrap-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
  color: var(--el-text-color-secondary);
}
</style>
