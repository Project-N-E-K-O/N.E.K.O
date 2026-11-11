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
import { capabilityMessageKey } from '@/utils/pluginCapabilities'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import {
  ArrowDown,
  ArrowUp,
  ChatLineRound,
  Collection,
  Promotion,
  Refresh,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import type { PluginConsent } from '@/api/pluginConsent'
import { usePluginConsentState } from '@/stores/pluginConsentState'

const props = defineProps<{ pluginId: string; allowRetry?: boolean }>()
const emit = defineEmits<{ loaded: [report: PluginConsent] }>()
const { t } = useI18n()
const consentState = usePluginConsentState()
const report = computed(() => consentState.details[props.pluginId] ?? null)
const loading = ref(false)
const retrying = ref(false)
const error = ref(false)
const expanded = ref(new Set<string>())
const capabilities = computed(() => report.value?.analysis.capabilities ?? [])
const capabilityIcons: Record<string, Component> = {
  'conversations.read': ChatLineRound,
  'memory.read': Collection,
  'messages.push': Promotion,
}
const iconFor = (capability: string) => capabilityIcons[capability] ?? ChatLineRound
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
const locations = (capability: string) => [
  ...new Set(
    (report.value?.analysis.evidence ?? [])
      .filter((entry) => entry.capability === capability)
      .map((entry) => `${entry.file}:${entry.line}`),
  ),
]
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
