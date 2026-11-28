<template>
  <div v-if="banner" class="legacy-consent" :data-tone="banner.tone" data-test="legacy-consent">
    <el-icon class="legacy-consent__icon" :size="16">
      <component :is="banner.icon" />
    </el-icon>
    <strong class="legacy-consent__title">{{ banner.title }}</strong>
    <span v-if="banner.description" class="legacy-consent__desc" :title="banner.description">
      {{ banner.description }}
    </span>
    <span class="legacy-consent__actions">
      <template v-if="banner.kind === 'confirm' || banner.kind === 'denied'">
        <el-button
          size="small"
          type="primary"
          :loading="busy === 'approve'"
          :disabled="busy !== null"
          @click="approve"
        >
          {{ running ? t('pluginConsent.approve') : t('pluginConsent.approveAndStart') }}
        </el-button>
        <el-button
          v-if="banner.kind === 'confirm'"
          size="small"
          :loading="busy === 'reject'"
          :disabled="busy !== null"
          @click="reject"
        >
          {{ t('pluginConsent.reject') }}
        </el-button>
        <el-button
          v-else
          size="small"
          text
          :loading="busy === 'revoke'"
          :disabled="busy !== null"
          @click="revoke"
        >
          {{ t('pluginConsent.revokeDecision') }}
        </el-button>
      </template>
      <el-button
        v-else
        size="small"
        text
        :loading="busy === 'revoke'"
        :disabled="busy !== null"
        @click="revoke"
      >
        {{ t('pluginConsent.revoke') }}
      </el-button>
    </span>
  </div>
</template>

<script setup lang="ts">
/**
 * The plugin-wide consent of the earlier flow, kept as one line above the
 * permission tab until the per-permission dialogs replace it. Same actions
 * and messages as before; analysis results are shown by the tab itself.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Component } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { CircleCheckFilled, CircleCloseFilled, WarningFilled } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { usePluginStartGate } from '@/composables/usePluginStartGate'
import { consentSaveErrorKey, consentSnapshot } from '@/utils/pluginCapabilities'

const props = defineProps<{ pluginId: string }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()
const store = usePluginStore()
const consentState = usePluginConsentState()
const startGate = usePluginStartGate()
const record = computed(() => consentState.details[props.pluginId] ?? null)
const running = computed(() => store.getPluginById(props.pluginId)?.status === 'running')
const busy = ref<'approve' | 'reject' | 'revoke' | null>(null)

const banner = computed<{
  kind: 'confirm' | 'denied' | 'approved'
  tone: 'warning' | 'danger' | 'success'
  icon: Component
  title: string
  description: string | null
} | null>(() => {
  const r = record.value
  if (!r || r.state === 'analyzing') return null
  if (r.decision === 'denied')
    return {
      kind: 'denied',
      tone: 'danger',
      icon: CircleCloseFilled,
      title: t('pluginConsent.decision.denied'),
      description: t('pluginConsent.banner.deniedDesc'),
    }
  if (r.state === 'confirm') {
    const count = r.unconfirmed.length
    return {
      kind: 'confirm',
      tone: 'warning',
      icon: WarningFilled,
      title: count
        ? t(r.upgraded ? 'pluginConsent.banner.upgradedConfirm' : 'pluginConsent.banner.confirm', { n: count })
        : t('pluginConsent.banner.incompleteTitle'),
      description: count ? t('pluginConsent.banner.capabilitiesDesc') : t('pluginConsent.banner.incompleteDesc'),
    }
  }
  if (r.decision === 'approved') {
    const decided = dayjs(r.decided_at)
    return {
      kind: 'approved',
      tone: 'success',
      icon: CircleCheckFilled,
      title: t('pluginConsent.decision.approved'),
      description:
        r.decided_at && decided.isValid()
          ? t('pluginConsent.banner.approvedAt', { date: decided.format('YYYY-MM-DD HH:mm') })
          : null,
    }
  }
  return null
})

async function load() {
  try {
    await consentState.load(props.pluginId)
  } catch {
    // The tab works without it; the line just stays hidden.
  }
}
watch(
  () => props.pluginId,
  (id, previous) => {
    if (previous) consentState.unwatchPlugin(previous)
    consentState.watchPlugin(id)
    void load()
  },
  { immediate: true },
)
onBeforeUnmount(() => consentState.unwatchPlugin(props.pluginId))

async function approve() {
  const details = record.value
  if (!details) return
  busy.value = 'approve'
  let saved = false
  try {
    await consentState.approve(props.pluginId, consentSnapshot(details))
    saved = true
    if (!running.value && (await startGate.startWithConsent(props.pluginId))) {
      ElMessage.success(t('messages.pluginStarted'))
    }
  } catch (error) {
    ElMessage.error(t(saved ? 'messages.startFailed' : consentSaveErrorKey(error)))
  } finally {
    busy.value = null
    void load()
    emit('changed')
  }
}
async function reject() {
  busy.value = 'reject'
  try {
    await consentState.reject(props.pluginId)
    // Rejecting stops the plugin server-side; refresh so the header shows it.
    await store.fetchPluginStatus()
    try {
      await store.refreshLoadedPluginData()
    } catch {
      // A refresh failure must not hide a recorded decision.
    }
  } catch (error) {
    ElMessage.error(t(consentSaveErrorKey(error)))
  } finally {
    busy.value = null
    void load()
    emit('changed')
  }
}
async function revoke() {
  busy.value = 'revoke'
  try {
    await consentState.revoke(props.pluginId)
  } catch (error) {
    ElMessage.error(t(consentSaveErrorKey(error)))
  } finally {
    busy.value = null
    void load()
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
