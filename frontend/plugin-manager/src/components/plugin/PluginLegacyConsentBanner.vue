<template>
  <div v-if="denied" class="legacy-consent" data-tone="danger" data-test="legacy-consent">
    <el-icon class="legacy-consent__icon" :size="16">
      <CircleCloseFilled />
    </el-icon>
    <strong class="legacy-consent__title">{{ t('pluginConsent.decision.denied') }}</strong>
    <span class="legacy-consent__desc" :title="t('pluginConsent.banner.deniedDesc')">
      {{ t('pluginConsent.banner.deniedDesc') }}
    </span>
    <span class="legacy-consent__actions">
      <el-button size="small" text :loading="busy" :disabled="busy" data-test="legacy-revoke" @click="revoke">
        {{ t('pluginConsent.revokeDecision') }}
      </el-button>
    </span>
  </div>
</template>

<script setup lang="ts">
/**
 * An earlier plugin-wide "deny", shown as one line above the permission tab
 * with a way to withdraw it. Nothing else of the earlier decision is shown.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { CircleCloseFilled } from '@element-plus/icons-vue'
import { getPluginConsent, revokePluginConsent, type PluginConsent } from '@/api/pluginConsent'
import { consentSaveErrorKey } from '@/utils/pluginCapabilities'

const props = defineProps<{ pluginId: string }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()
const record = ref<PluginConsent | null>(null)
const busy = ref(false)
const denied = computed(() => record.value?.decision === 'denied')
let sequence = 0

async function load() {
  const seq = ++sequence
  const id = props.pluginId
  try {
    const next = await getPluginConsent(id)
    if (seq === sequence) record.value = next
  } catch {
    // The tab works without it; the line just stays hidden.
    if (seq === sequence) record.value = null
  }
}
watch(() => props.pluginId, () => void load(), { immediate: true })

async function revoke() {
  busy.value = true
  try {
    const next = await revokePluginConsent(props.pluginId)
    sequence++
    record.value = next
  } catch (error) {
    ElMessage.error(t(consentSaveErrorKey(error)))
    void load()
  } finally {
    busy.value = false
    emit('changed')
  }
}
</script>

<style scoped>
.legacy-consent {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  padding: 6px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-left: 3px solid var(--legacy-tone);
  border-radius: 6px;
  background: var(--el-bg-color);
  font-size: 13px;
}
.legacy-consent[data-tone='warning'] {
  --legacy-tone: var(--el-color-warning);
}
.legacy-consent[data-tone='danger'] {
  --legacy-tone: var(--el-color-danger);
}
.legacy-consent[data-tone='success'] {
  --legacy-tone: var(--el-color-success);
}
.legacy-consent__icon {
  flex-shrink: 0;
  color: var(--legacy-tone);
}
.legacy-consent__title {
  flex-shrink: 0;
}
.legacy-consent__desc {
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.legacy-consent__actions {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  margin-left: auto;
}
</style>
