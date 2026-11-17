<template>
  <ul class="consent-notice">
    <li v-for="plugin in pending" :key="plugin.plugin_id">
      <div class="consent-notice__head">
        <strong>{{ plugin.name }}</strong>
        <el-tag v-if="plugin.upgraded" size="small" type="warning">{{
          t('pluginConsent.upgraded')
        }}</el-tag>
      </div>
      <p v-if="plugin.unconfirmed.length" class="consent-notice__caps">
        {{
          plugin.unconfirmed
            .map((capability) =>
              t(`pluginConsent.capabilities.${capability.replace('.', '_')}.title`),
            )
            .join('、')
        }}
      </p>
      <p v-else class="consent-notice__caps">{{ t('pluginConsent.unknown') }}</p>
      <div class="consent-notice__actions">
        <el-button
          size="small"
          type="primary"
          :loading="busy === plugin.plugin_id"
          @click="decide(plugin, true)"
          >{{ t('pluginConsent.approve') }}</el-button
        >
        <el-button
          size="small"
          :loading="busy === plugin.plugin_id"
          @click="decide(plugin, false)"
          >{{ t('pluginConsent.reject') }}</el-button
        >
      </div>
    </li>
  </ul>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import type { PluginConsentNotice } from '@/api/pluginConsent'
import { isConsentChanged } from '@/utils/pluginCapabilities'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { usePluginStore } from '@/stores/plugin'

const props = defineProps<{ notices: PluginConsentNotice[] }>()
const emit = defineEmits<{ done: []; handled: [pluginId: string]; stale: [] }>()
const { t } = useI18n()
const consentState = usePluginConsentState()
const pluginStore = usePluginStore()
const handled = ref(new Set<string>())
const busy = ref<string | null>(null)
const pending = computed(() =>
  props.notices.filter((plugin) => !handled.value.has(plugin.plugin_id)),
)
async function refreshPlugins() {
  await pluginStore.fetchPluginStatus()
  try {
    await pluginStore.refreshLoadedPluginData()
  } catch {
    // A refresh failure must not hide a recorded decision.
  }
}
async function decide(plugin: PluginConsentNotice, approved: boolean) {
  busy.value = plugin.plugin_id
  try {
    if (approved) {
      const updated = await consentState.approve(plugin.plugin_id, plugin)
      if (updated.state !== 'clear') {
        emit('stale')
        return
      }
    } else {
      await consentState.reject(plugin.plugin_id)
    }
    handled.value = new Set([...handled.value, plugin.plugin_id])
    emit('handled', plugin.plugin_id)
    await refreshPlugins()
    if (!pending.value.length) emit('done')
  } catch (error) {
    if (isConsentChanged(error)) {
      // Loading the current state lets the list follow a pending scan.
      await consentState.load(plugin.plugin_id).catch(() => undefined)
      emit('stale')
    }
    ElMessage.error(t('pluginConsent.saveFailed'))
  } finally {
    busy.value = null
  }
}
</script>

<style scoped>
.consent-notice {
  display: grid;
  gap: 12px;
  margin: 0;
  padding-left: 0;
  list-style: none;
}
.consent-notice__head {
  display: flex;
  gap: 8px;
  align-items: center;
}
.consent-notice__caps {
  margin: 4px 0;
  color: var(--el-text-color-secondary);
}
.consent-notice__actions {
  display: flex;
  gap: 8px;
}
</style>
