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
    <div v-if="startDialog.active" class="consent-loader-host">
      <PermissionStartDialog
        :key="startDialog.active.key"
        :panel="startDialog.active.panel"
        :pending="startDialog.active.pending"
        :request="startDialog.active.request"
        :plugin-name="startDialog.active.pluginName"
        start
        :onResult="resultFor(startDialog.active.key)"
        :onClose="closeFor(startDialog.active.key)"
      />
    </div>
  </el-config-provider>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { elementLocale, localeLoadState } from './i18n'
import LocaleLoadNotice from './components/common/LocaleLoadNotice.vue'
import { PermissionStartDialog } from './components/plugin/permissionStartDialog'
import { usePermissionStartDialog } from './stores/permissionStartDialog'

// The dialog before a manual start. Its module is loaded before it is asked
// for; if the loader still shows its own box, closing that box starts the
// plugin, as a failed read does: the dialog never keeps a plugin from starting.
const startDialog = usePermissionStartDialog()
// Each handler is bound to its own dialog: a late answer from a replaced one is dropped.
function resultFor(key: number) {
  return (result: { start: boolean }) => startDialog.answer(result.start, key)
}
function closeFor(key: number) {
  return () => startDialog.answer(true, key)
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
