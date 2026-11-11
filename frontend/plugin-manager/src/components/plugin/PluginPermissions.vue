<template>
  <section class="plugin-permissions">
    <div v-if="banner" class="consent-banner" :data-tone="banner.tone">
      <el-icon class="consent-banner__icon" :size="22" :class="{ 'is-loading': banner.spin }">
        <component :is="banner.icon" />
      </el-icon>
      <div class="consent-banner__text">
        <strong>{{ banner.title }}</strong>
        <p v-if="banner.description">{{ banner.description }}</p>
      </div>
      <div v-if="banner.kind !== 'info' && banner.kind !== 'clean'" class="consent-banner__actions">
        <template v-if="banner.kind === 'confirm' || banner.kind === 'denied'">
          <el-button
            type="primary"
            :loading="busy === 'approve'"
            :disabled="busy !== null"
            @click="approve"
          >
            {{ running ? t('pluginConsent.approve') : t('pluginConsent.approveAndStart') }}
          </el-button>
          <el-button
            v-if="banner.kind === 'confirm'"
            :loading="busy === 'reject'"
            :disabled="busy !== null"
            @click="reject"
          >
            {{ t('pluginConsent.reject') }}
          </el-button>
          <el-button
            v-else
            text
            :loading="busy === 'revoke'"
            :disabled="busy !== null"
            @click="revoke"
          >
            {{ t('pluginConsent.revokeDecision') }}
          </el-button>
        </template>
        <el-button
          v-else-if="banner.kind === 'approved'"
          text
          :loading="busy === 'revoke'"
          :disabled="busy !== null"
          @click="revoke"
        >
          {{ t('pluginConsent.revoke') }}
        </el-button>
      </div>
    </div>
    <PluginCapabilityReport ref="capabilityReport" :plugin-id="pluginId" allow-retry />
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import {
  CircleCheckFilled,
  CircleCloseFilled,
  Loading,
  WarningFilled,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import { usePluginStore } from '@/stores/plugin'
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { usePluginStartGate } from '@/composables/usePluginStartGate'
import PluginCapabilityReport from './PluginCapabilityReport.vue'

const props = defineProps<{ pluginId: string }>()
const { t } = useI18n()
const store = usePluginStore()
const consentState = usePluginConsentState()
const startGate = usePluginStartGate()
const record = computed(() => consentState.details[props.pluginId] ?? null)
const running = computed(() => store.getPluginById(props.pluginId)?.status === 'running')
const busy = ref<'approve' | 'reject' | 'revoke' | null>(null)
const capabilityReport = ref<InstanceType<typeof PluginCapabilityReport> | null>(null)

type BannerKind = 'info' | 'confirm' | 'denied' | 'approved' | 'clean'
const banner = computed<{
  kind: BannerKind
  tone: 'info' | 'warning' | 'danger' | 'success'
  icon: Component
  spin: boolean
  title: string
  description: string | null
} | null>(() => {
  const r = record.value
  if (!r) return null
  if (r.state === 'analyzing')
    return {
      kind: 'info',
      tone: 'info',
      icon: Loading,
      spin: true,
      title: t('pluginConsent.banner.analyzingTitle'),
      description: t('pluginConsent.banner.analyzingDesc'),
    }
  if (r.decision === 'denied')
    return {
      kind: 'denied',
      tone: 'danger',
      icon: CircleCloseFilled,
      spin: false,
      title: t('pluginConsent.decision.denied'),
      description: t('pluginConsent.banner.deniedDesc'),
    }
  if (r.state === 'confirm') {
    const count = r.unconfirmed.length
    return {
      kind: 'confirm',
      tone: 'warning',
      icon: WarningFilled,
      spin: false,
      title: count
        ? t(
            r.upgraded
              ? 'pluginConsent.banner.upgradedConfirm'
              : 'pluginConsent.banner.confirm',
            { n: count },
          )
        : t('pluginConsent.banner.incompleteTitle'),
      description: count
        ? t('pluginConsent.banner.capabilitiesDesc')
        : t('pluginConsent.banner.incompleteDesc'),
    }
  }
  if (r.decision === 'approved') {
    const decided = dayjs(r.decided_at)
    return {
      kind: 'approved',
      tone: 'success',
      icon: CircleCheckFilled,
      spin: false,
      title: t('pluginConsent.decision.approved'),
      description:
        r.decided_at && decided.isValid()
          ? t('pluginConsent.banner.approvedAt', { date: decided.format('YYYY-MM-DD HH:mm') })
          : null,
    }
  }
  if (r.analysis.status === 'completed' && !r.analysis.capabilities.length)
    return {
      kind: 'clean',
      tone: 'success',
      icon: CircleCheckFilled,
      spin: false,
      title: t('pluginConsent.noneFound'),
      description: null,
    }
  return null
})

function refreshReport() {
  void capabilityReport.value?.refresh()
}
async function approve() {
  busy.value = 'approve'
  try {
    await consentState.approve(props.pluginId)
    if (!running.value && (await startGate.startWithConsent(props.pluginId))) {
      ElMessage.success(t('messages.pluginStarted'))
    }
  } catch {
    ElMessage.error(t('messages.startFailed'))
  } finally {
    busy.value = null
    refreshReport()
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
  } catch {
    ElMessage.error(t('pluginConsent.saveFailed'))
  } finally {
    busy.value = null
    refreshReport()
  }
}
async function revoke() {
  busy.value = 'revoke'
  try {
    await consentState.revoke(props.pluginId)
  } catch {
    ElMessage.error(t('pluginConsent.saveFailed'))
  } finally {
    busy.value = null
    refreshReport()
  }
}
</script>

<style scoped>
.plugin-permissions {
  display: grid;
  gap: 14px;
  max-width: 900px;
}
.consent-banner {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 16px;
  border: 1px solid;
  border-radius: var(--el-border-radius-base);
}
.consent-banner[data-tone='warning'] {
  border-color: var(--el-color-warning-light-7);
  background: var(--el-color-warning-light-9);
}
.consent-banner[data-tone='danger'] {
  border-color: var(--el-color-danger-light-7);
  background: var(--el-color-danger-light-9);
}
.consent-banner[data-tone='success'] {
  border-color: var(--el-color-success-light-7);
  background: var(--el-color-success-light-9);
}
.consent-banner[data-tone='info'] {
  border-color: var(--el-color-info-light-7);
  background: var(--el-color-info-light-9);
}
.consent-banner__icon {
  flex-shrink: 0;
  margin-top: 1px;
}
.consent-banner[data-tone='warning'] .consent-banner__icon {
  color: var(--el-color-warning);
}
.consent-banner[data-tone='danger'] .consent-banner__icon {
  color: var(--el-color-danger);
}
.consent-banner[data-tone='success'] .consent-banner__icon {
  color: var(--el-color-success);
}
.consent-banner[data-tone='info'] .consent-banner__icon {
  color: var(--el-color-info);
}
.consent-banner__text {
  display: grid;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.consent-banner__text p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.consent-banner__actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}
@media (max-width: 640px) {
  .consent-banner {
    flex-wrap: wrap;
  }
  .consent-banner__actions {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
