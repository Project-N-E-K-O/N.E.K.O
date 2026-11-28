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
    <div v-if="consentState.activeRequest" class="consent-loader-host">
      <PluginConsentDialog :onClose="closeConsentRequest(consentState.activeRequest)" />
    </div>
  </el-config-provider>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { elementLocale, localeLoadState } from './i18n'
import LocaleLoadNotice from './components/common/LocaleLoadNotice.vue'
import { deferredPanel } from './components/common/deferredPanel'
import { usePluginConsentState, type ConsentRequest } from './stores/pluginConsentState'

const consentState = usePluginConsentState()
// Reuse the optional-panel recovery UI. The existing request v-if/nextTick
// handoff unmounts an old loader before presenting the next queued request.
const PluginConsentDialog = deferredPanel(() => import('./components/plugin/PluginConsentDialog.vue'))
function closeConsentRequest(request: ConsentRequest) {
  // A late close from an old dialog/loader must not cancel its successor.
  return () => {
    if (consentState.activeRequest === request) consentState.resolveConfirmation(false)
  }
}

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
/* The real dialog owns its modal. Only the loading/error fallback needs a
   viewport host, otherwise the full-height route can place it off-screen. */
.consent-loader-host > :deep(.deferred-panel) {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 12000;
  width: min(560px, calc(100vw - 32px));
  box-sizing: border-box;
  color: var(--el-text-color-primary);
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  box-shadow: var(--el-box-shadow);
}
</style>
