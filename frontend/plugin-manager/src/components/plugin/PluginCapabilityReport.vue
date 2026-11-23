<template>
  <section class="capability-report" :aria-busy="loading">
    <el-alert
      v-if="error"
      :title="t('pluginConsent.loadFailed')"
      type="warning"
      :closable="false"
      show-icon
    />
    <p v-if="loading && !report" role="status">{{ t('pluginConsent.loading') }}</p>
    <ul v-if="capabilities.length" class="capability-list">
      <li v-for="capability in capabilities" :key="capability" class="capability">
        <el-icon class="capability__icon" :size="20">
          <component :is="iconFor(capability)" />
        </el-icon>
        <div class="capability__body">
          <div class="capability__head">
            <strong>{{
              t(capabilityMessageKey(capability, 'title'))
            }}</strong>
            <el-tag size="small" :type="statusTag(capability).type">
              {{ statusTag(capability).label }}
            </el-tag>
            <el-tag
              v-if="origin(capability).declared !== null"
              size="small"
              type="info"
              data-testid="capability-origin"
            >
              {{ t(originKey(origin(capability).declared)) }}
            </el-tag>
          </div>
          <p class="capability__desc">
            {{ t(capabilityMessageKey(capability, 'description')) }}
          </p>
          <template v-if="locations(capability).length">
            <button type="button" class="capability__toggle" @click="toggle(capability)">
              {{ t('pluginConsent.evidenceToggle', { n: locations(capability).length }) }}
              <el-icon :size="12">
                <ArrowDown v-if="!expanded.has(capability)" />
                <ArrowUp v-else />
              </el-icon>
            </button>
            <ul v-if="expanded.has(capability)" class="evidence">
              <li v-for="location in locations(capability)" :key="location">
                <code>{{ location }}</code>
              </li>
            </ul>
          </template>
        </div>
      </li>
    </ul>
    <section v-if="permissions.length" class="finding-group" data-testid="used-permissions">
      <h4 class="finding-group__title">{{ t('pluginConsent.findings.usedTitle') }}</h4>
      <ul class="capability-list">
        <li v-for="permission in permissions" :key="permission.id" class="capability">
          <el-icon class="capability__icon" :size="20">
            <component :is="iconFor(permission.id)" />
          </el-icon>
          <div class="capability__body">
            <div class="capability__head">
              <strong>{{ permissionTitle(permission.id) }}</strong>
              <el-tag size="small" type="info">
                {{ t(originKey(permission.declared)) }}
              </el-tag>
            </div>
            <p v-if="permissionDescription(permission.id)" class="capability__desc">
              {{ permissionDescription(permission.id) }}
            </p>
            <template v-if="permission.locations.length">
              <button
                type="button"
                class="capability__toggle"
                @click="toggle(`permission:${permission.id}`)"
              >
                {{ t('pluginConsent.evidenceToggle', { n: permission.locations.length }) }}
                <el-icon :size="12">
                  <ArrowDown v-if="!expanded.has(`permission:${permission.id}`)" />
                  <ArrowUp v-else />
                </el-icon>
              </button>
              <ul v-if="expanded.has(`permission:${permission.id}`)" class="evidence">
                <li v-for="location in permission.locations" :key="location">
                  <code>{{ location }}</code>
                </li>
              </ul>
            </template>
          </div>
        </li>
      </ul>
    </section>
    <section v-if="unconfirmed.length" class="blind-spots" data-testid="blind-spots">
      <el-icon class="capability__icon blind-spots__icon" :size="20"><Warning /></el-icon>
      <div class="capability__body">
        <strong>{{ t('pluginConsent.findings.blindSpotsTitle', { n: unconfirmed.length }) }}</strong>
        <p class="capability__desc">{{ t('pluginConsent.findings.blindSpotsDesc') }}</p>
        <button type="button" class="capability__toggle" @click="toggle('blind-spots')">
          {{ t('pluginConsent.evidenceToggle', { n: unconfirmed.length }) }}
          <el-icon :size="12">
            <ArrowDown v-if="!expanded.has('blind-spots')" />
            <ArrowUp v-else />
          </el-icon>
        </button>
        <ul v-if="expanded.has('blind-spots')" class="evidence">
          <li v-for="(spot, index) in unconfirmed" :key="index">
            <code v-if="blindSpotLocation(spot)">{{ blindSpotLocation(spot) }}</code>
            <span v-else>{{ t('pluginConsent.findings.unlocated') }}</span>
          </li>
        </ul>
      </div>
    </section>
    <p v-if="report && !capabilities.length && report.analysis.status !== 'completed'">
      {{ t('pluginConsent.unknown') }}
    </p>
    <ul v-if="report?.analysis.warnings.length" class="analysis-warnings">
      <li v-for="(warning, index) in report.analysis.warnings" :key="index">
        {{ t(`pluginConsent.warnings.${warning.code}`)
        }}<span v-if="warning.file"> — {{ warning.file }}</span>
      </li>
    </ul>
    <footer v-if="metaParts.length || allowRetry" class="capability-meta">
      <span>{{ metaParts.join(' · ') }}</span>
      <el-button v-if="allowRetry" text :loading="retrying" @click="retry">
        <el-icon :size="14"><Refresh /></el-icon>
        {{ t('pluginConsent.retry') }}
      </el-button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  blindSpotLocation,
  blindSpots,
  capabilityMessageKey,
  capabilityOrigin,
  usedPermissions,
} from '@/utils/pluginCapabilities'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import {
  ArrowDown,
  ArrowUp,
  ChatLineRound,
  Collection,
  Connection,
  Document,
  EditPen,
  Key,
  Lock,
  MagicStick,
  Monitor,
  Promotion,
  Refresh,
  Warning,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import type { PluginConsent } from '@/api/pluginConsent'
import { usePluginConsentState } from '@/stores/pluginConsentState'

const props = defineProps<{ pluginId: string; allowRetry?: boolean }>()
const emit = defineEmits<{ loaded: [report: PluginConsent] }>()
const { t, te } = useI18n()
const consentState = usePluginConsentState()
const report = computed(() => consentState.details[props.pluginId] ?? null)
const loading = ref(false)
const retrying = ref(false)
const error = ref(false)
const expanded = ref(new Set<string>())
const capabilities = computed(() => report.value?.analysis.capabilities ?? [])
const permissions = computed(() => usedPermissions(report.value?.analysis))
const unconfirmed = computed(() => blindSpots(report.value?.analysis))
const capabilityIcons: Record<string, Component> = {
  'conversations.read': ChatLineRound,
  'memory.read': Collection,
  'messages.push': Promotion,
  'plugin_sdk.main_agent.conversations:read': ChatLineRound,
  'plugin_sdk.main_agent.memory:read': Collection,
  'plugin_sdk.main_agent.messages:push': Promotion,
  'plugin_sdk.models:invoke': MagicStick,
  'system.network:connect': Connection,
  'system.network:listen': Connection,
  'system.fs:read': Document,
  'system.fs:write': EditPen,
  'system.process:spawn': Monitor,
  'system.env:read': Key,
}
const iconFor = (capability: string) =>
  capabilityIcons[capability] ?? (capability.includes(':') ? Lock : ChatLineRound)
// A permission id this build has no label for is still shown, by its id.
const hasLabel = (id: string) => !te || te(capabilityMessageKey(id, 'title'))
const permissionTitle = (id: string) =>
  hasLabel(id) ? t(capabilityMessageKey(id, 'title')) : id
const permissionDescription = (id: string) =>
  hasLabel(id) ? t(capabilityMessageKey(id, 'description')) : ''
function statusTag(capability: string): { type: 'success' | 'warning' | 'info'; label: string } {
  const record = report.value
  if (record?.approved_capabilities.includes(capability))
    return { type: 'success', label: t('pluginConsent.decision.approved') }
  if (record?.unconfirmed.includes(capability))
    return {
      type: 'warning',
      label: t(record.upgraded ? 'pluginConsent.upgraded' : 'pluginConsent.pending'),
    }
  return { type: 'info', label: t('pluginConsent.pending') }
}
const origin = (capability: string) => capabilityOrigin(report.value?.analysis, capability)
const locations = (capability: string) => origin(capability).locations
const originKey = (declared: boolean | null) =>
  declared ? 'pluginConsent.findings.declared' : 'pluginConsent.findings.detected'
function toggle(capability: string) {
  const next = new Set(expanded.value)
  if (next.has(capability)) next.delete(capability)
  else next.add(capability)
  expanded.value = next
}
const metaParts = computed(() => {
  const analysis = report.value?.analysis
  if (!analysis) return []
  const parts: string[] = []
  if (analysis.analyzed_at) {
    const date = dayjs(analysis.analyzed_at)
    if (date.isValid()) parts.push(t('pluginConsent.meta.analyzed', { date: date.format('YYYY-MM-DD HH:mm') }))
  }
  if (analysis.version) parts.push(t('pluginConsent.meta.version', { version: analysis.version }))
  if (analysis.files_analyzed != null)
    parts.push(t('pluginConsent.meta.files', { n: analysis.files_analyzed }))
  return parts
})
defineExpose({ refresh: load })
async function load() {
  loading.value = !report.value
  try {
    const result = await consentState.load(props.pluginId)
    error.value = false
    emit('loaded', result)
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}
async function retry() {
  retrying.value = true
  try {
    await consentState.analyze(props.pluginId)
    error.value = false
  } catch {
    error.value = true
  } finally {
    retrying.value = false
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
</script>

<style scoped>
.capability-report {
  display: grid;
  gap: 12px;
}
.capability-report > p {
  margin: 0;
  color: var(--el-text-color-secondary);
}
.capability-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.capability {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--el-border-radius-base);
  background: var(--el-bg-color);
}
.capability__icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--el-color-primary);
}
.capability__body {
  display: grid;
  gap: 6px;
  min-width: 0;
  flex: 1;
}
.capability__head {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}
.capability__head .el-tag {
  flex-shrink: 0;
  white-space: nowrap;
}
.capability__desc {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.capability__toggle {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  justify-self: start;
  padding: 0;
  border: none;
  background: none;
  color: var(--el-color-primary);
  font-size: 12px;
  cursor: pointer;
}
.evidence {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.evidence li {
  overflow-wrap: anywhere;
}
.evidence code {
  padding: 1px 6px;
  border-radius: var(--el-border-radius-small);
  background: var(--el-fill-color-light);
  font-size: 12px;
}
.finding-group {
  display: grid;
  gap: 8px;
}
.finding-group__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.blind-spots {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--el-color-warning-light-7);
  border-radius: var(--el-border-radius-base);
  background: var(--el-color-warning-light-9);
}
.blind-spots .blind-spots__icon {
  color: var(--el-color-warning);
}
.analysis-warnings {
  display: grid;
  gap: 4px;
  margin: 0;
  padding-left: 16px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.capability-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
