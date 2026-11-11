<template>
  <el-dialog
    class="plugin-consent-dialog"
    :model-value="plugins.length > 0"
    :title="t('pluginConsent.dialogTitle')"
    width="min(640px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!saving"
    :show-close="!saving"
    @close="cancel"
  >
    <p>{{ t('pluginConsent.introduction') }}</p>
    <section v-for="plugin in plugins" :key="plugin.id" class="consent-plugin">
      <h3>
        {{ plugin.name }} <small>{{ plugin.version }}</small>
      </h3>
      <el-alert
        v-if="incomplete(plugin.id)"
        :title="t('pluginConsent.incomplete')"
        type="warning"
        :closable="false"
        show-icon
      />
      <ul v-if="unconfirmed(plugin.id).length" class="capability-list">
        <li v-for="capability in unconfirmed(plugin.id)" :key="capability">
          <strong>{{ t(capabilityMessageKey(capability, 'title')) }}</strong>
          <el-tag v-if="upgraded(plugin.id)" size="small" type="warning">{{
            t('pluginConsent.upgraded')
          }}</el-tag>
          <p>{{ t(capabilityMessageKey(capability, 'description')) }}</p>
        </li>
      </ul>
      <p v-else>{{ t('pluginConsent.unknown') }}</p>
    </section>
    <el-alert
      v-if="saveError"
      :title="t('pluginConsent.saveFailed')"
      type="error"
      :closable="false"
    />
    <template #footer>
      <el-button :disabled="saving" @click="cancel">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" @click="approve">{{
        t('pluginConsent.approve')
      }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { capabilityMessageKey, INCOMPLETE_STATUSES } from '@/utils/pluginCapabilities'
const { t } = useI18n()
const consentState = usePluginConsentState()
const saving = ref(false)
const saveError = ref(false)

// A plugin whose pending capabilities disappeared while the dialog was open
// leaves the list; the store auto-resolves the request when none remain.
const plugins = computed(() =>
  (consentState.activeRequest?.plugins ?? []).filter(
    (plugin) => (consentState.stateOf(plugin.id)?.state ?? 'confirm') === 'confirm',
  ),
)
const unconfirmed = (id: string) => consentState.details[id]?.unconfirmed ?? []
const upgraded = (id: string) => consentState.details[id]?.upgraded === true
const incomplete = (id: string) =>
  INCOMPLETE_STATUSES.has(consentState.details[id]?.analysis.status ?? '')
function cancel() {
  if (!saving.value) consentState.resolveConfirmation(false)
}
async function approve() {
  saving.value = true
  saveError.value = false
  try {
    for (const plugin of plugins.value) await consentState.approve(plugin.id)
    consentState.resolveConfirmation(true)
  } catch {
    saveError.value = true
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.consent-plugin {
  margin: 20px 0;
}
.capability-list {
  display: grid;
  gap: 12px;
  padding-left: 0;
  list-style: none;
}
.capability-list li {
  padding: 12px 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}
.capability-list p {
  margin-bottom: 0;
  color: var(--el-text-color-secondary);
}
small {
  color: var(--el-text-color-secondary);
  font-weight: normal;
}
</style>
<style>
.plugin-consent-dialog .el-dialog__body {
  max-height: 60vh;
  overflow-y: auto;
}
</style>
