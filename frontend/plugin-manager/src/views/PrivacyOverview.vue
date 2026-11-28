<template>
  <div class="privacy-page perm-palette">
    <section class="privacy-card privacy-overview" data-test="overview">
      <header class="privacy-head">
        <h1>{{ t('pluginConsent.privacy.title') }}</h1>
      </header>
      <p class="privacy-subtitle">{{ t('pluginConsent.privacy.subtitle') }}</p>

      <div v-if="loading && !overview" class="privacy-state">{{ t('pluginConsent.privacy.loading') }}</div>
      <div v-else-if="!overview" class="privacy-state" data-test="overview-failed">
        {{ t('pluginConsent.privacy.loadFailed') }}
        <el-button size="small" @click="load()">{{ t('pluginConsent.tab.retry') }}</el-button>
      </div>
      <template v-else>
        <p v-if="overview.checks_disabled" class="privacy-notice privacy-notice--alert" data-test="checks-disabled">
          {{ t('pluginConsent.privacy.checksDisabled') }}
        </p>
        <p v-if="overview.incomplete.length" class="privacy-notice" data-test="incomplete">
          {{ t('pluginConsent.privacy.incomplete', { n: overview.incomplete.length }) }}
        </p>

        <div v-if="pending.length" class="privacy-pending" data-test="pending">
          <span>
            {{ t('pluginConsent.privacy.pending.label') }}
            <template v-for="(part, index) in pending" :key="part.key">
              <template v-if="index">{{ t('pluginConsent.privacy.pending.separator') }}</template>
              <span :data-test="`pending-${part.key}`">{{ t(`pluginConsent.privacy.pending.${part.key}`, { n: part.n }) }}</span>
            </template>
          </span>
          <router-link class="privacy-link" to="/plugins" data-test="pending-go">
            {{ t('pluginConsent.privacy.pending.go') }} ›
          </router-link>
        </div>

        <div v-for="section in sections" :key="section.tier" class="privacy-section" :data-tier="section.tier">
          <button
            v-if="section.collapsed"
            class="privacy-tier privacy-tier--toggle"
            type="button"
            :aria-expanded="isOpen(section)"
            :data-test="`tier-${section.tier}`"
            @click="toggleTier(section.tier)"
          >
            <span class="privacy-tier__dot" />
            {{ t(`pluginConsent.tab.tiers.${section.tier}`) }} · {{ t('pluginConsent.tab.tierCount', { n: section.groups.length }) }}
            <span aria-hidden="true">{{ isOpen(section) ? '▴' : '▾' }}</span>
          </button>
          <div v-else class="privacy-tier" :data-test="`tier-${section.tier}`">
            <span class="privacy-tier__dot" />
            {{ t(`pluginConsent.tab.tiers.${section.tier}`) }}
          </div>
          <template v-if="isOpen(section)">
            <button
              v-for="group in section.groups"
              :key="group.group"
              type="button"
              class="privacy-row"
              :class="{ 'is-selected': group.group === selected }"
              :aria-pressed="group.group === selected"
              :data-test="`group-${group.group}`"
              @click="select(group.group)"
            >
              <span class="privacy-row__name">
                <strong>{{ groupName(group.group) }}</strong>
                <span v-if="group.partial_block" class="perm-tag perm-tag--tier" data-test="partial-block">
                  {{ t('pluginConsent.tab.partialBlock') }}
                </span>
              </span>
              <span class="privacy-row__count">{{ countText(group) }}</span>
              <span class="privacy-row__latest">{{ latestText(group) }}</span>
              <span class="privacy-row__chevron" aria-hidden="true">›</span>
            </button>
          </template>
        </div>

        <p v-for="combo in overview.combos" :key="combo.plugin_id" class="privacy-combo" data-test="combo">
          {{
            t('pluginConsent.privacy.combo', {
              plugin: nameOf(combo.plugin_id),
              reads: combo.reads.map(readName).join(t('pluginConsent.tab.listSeparator')),
              sends: combo.sends.map(label).join(t('pluginConsent.tab.listSeparator')),
            })
          }}
        </p>
      </template>
    </section>

    <section v-if="overview && selected" class="privacy-card privacy-detail" :data-tier="selectedTier" data-test="detail">
      <header class="privacy-head privacy-head--detail">
        <h2>{{ groupName(selected) }}</h2>
        <span v-if="selectedTier" class="perm-tag perm-tag--tier" data-test="detail-tier">{{ t(`pluginConsent.tab.tiers.${selectedTier}`) }}</span>
        <span v-if="selectedPartial" class="perm-tag perm-tag--tier" data-test="detail-partial-block">
          {{ t('pluginConsent.tab.partialBlock') }}
        </span>
      </header>
      <p v-if="PERSONAL_GROUPS.has(selected)" class="privacy-subtitle" data-test="personal-note">
        {{ t('pluginConsent.privacy.detail.personalNote') }}
      </p>

      <div v-if="detailLoading && !detail" class="privacy-state">{{ t('pluginConsent.privacy.detail.loading') }}</div>
      <div v-else-if="!detail" class="privacy-state" data-test="detail-failed">
        {{ t('pluginConsent.privacy.detail.loadFailed') }}
        <el-button size="small" @click="loadDetail(selected)">{{ t('pluginConsent.tab.retry') }}</el-button>
      </div>
      <template v-else>
        <p v-if="lines.some((line) => line.condition)" class="privacy-hint" data-test="condition-note">
          {{ t('pluginConsent.tab.condition.label') }}{{ t('pluginConsent.tab.condition.selfReported') }}
        </p>
        <p v-if="!lines.length" class="privacy-empty" data-test="detail-empty">{{ t('pluginConsent.privacy.detail.empty') }}</p>
        <div v-for="line in lines" :key="line.key" class="privacy-plugin" :data-test="`plugin-${line.pluginId}-${line.permission}`">
          <div class="privacy-plugin__name">
            <strong>{{ line.name }}</strong>
            <span v-if="multiPermission" class="perm-muted">{{ label(line.permission) }}</span>
          </div>
          <div class="privacy-plugin__condition">
            <span v-if="line.legacyAny" class="perm-tag" data-test="legacy">{{ t('pluginConsent.tab.legacyAny') }}</span>
            <span v-else-if="line.condition" data-test="condition">{{ t(`pluginConsent.tab.condition.${line.condition}`) }}</span>
            <span v-else class="perm-muted">—</span>
          </div>
          <div class="privacy-plugin__usage">
            <span v-if="!line.installed" class="perm-tag" data-test="uninstalled">{{ t('pluginConsent.privacy.detail.uninstalled') }}</span>
            <span v-if="line.state === 'undecided'" class="perm-tag" data-test="undecided">{{ t('pluginConsent.tab.undecided') }}</span>
            <span v-if="line.noPermission" data-test="no-permission">{{ t('pluginConsent.privacy.detail.noPermission') }}</span>
            <span v-if="usageText(line)" :class="{ 'privacy-would-block': line.usage?.kind === 'last' && line.usage.blocked }" data-test="usage">
              {{ usageText(line) }}
            </span>
            <span v-if="line.suggestOff" class="perm-tag privacy-suggest" data-test="suggest-off">{{ t('pluginConsent.privacy.detail.suggestOff') }}</span>
            <span v-if="enforcementTag({ enforced: line.enforced, usage_tracked: line.usageTracked })" class="perm-tag" data-test="enforcement">
              {{ t(`pluginConsent.tab.${enforcementTag({ enforced: line.enforced, usage_tracked: line.usageTracked })}`) }}
            </span>
          </div>
          <div class="privacy-plugin__switch">
            <el-switch
              v-if="line.switchable"
              :model-value="line.state === 'granted'"
              :loading="busy === line.key"
              :disabled="busy !== null"
              :aria-label="`${line.name} ${label(line.permission)}`"
              :data-test="`switch-${line.pluginId}-${line.permission}`"
              @update:model-value="(on: string | number | boolean) => toggle(line, Boolean(on))"
            />
          </div>
        </div>

        <div class="privacy-recent" data-test="recent">
          <div class="privacy-recent__head">
            <h3>{{ t('pluginConsent.privacy.recent.title') }}</h3>
            <label class="privacy-recent__filter">
              <input v-model="onlyWouldBlock" type="checkbox" data-test="only-would-block" />
              {{ t('pluginConsent.privacy.recent.onlyWouldBlock') }}
            </label>
          </div>
          <p v-if="!recent.length" class="privacy-empty" data-test="recent-empty">{{ recentEmptyText }}</p>
          <div
            v-for="(use, index) in recent"
            :key="`${use.at}-${use.plugin_id}-${use.permission}-${index}`"
            class="privacy-use"
            :class="{ 'privacy-would-block': wouldBlock(use) }"
            data-test="recent-use"
          >
            <span class="privacy-use__time">{{ formatUseTime(use.at, t) }}</span>
            <span class="privacy-use__text">{{ useText(use) }}</span>
          </div>
          <p class="privacy-footer">{{ t('pluginConsent.privacy.recent.footer') }}</p>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import '@/assets/styles/permissionPalette.css'
import {
  clearPermission,
  decidePermission,
  getPermissionCatalog,
  getPrivacyGroup,
  getPrivacyOverview,
  type PermissionCatalog,
  type PrivacyGroupDetail,
  type PrivacyOverview,
  type PrivacyUse,
  type RiskTier,
} from '@/api/pluginPermissions'
import { usePluginStore } from '@/stores/plugin'
import { capabilityMessageKey } from '@/utils/pluginCapabilities'
import {
  decisionErrorKey,
  enforcementTag,
  formatUseDate,
  formatUseTime,
  validCatalog,
} from '@/utils/permissionTab'
import {
  ORIGIN_KINDS,
  PERSONAL_GROUPS,
  overviewSections,
  pendingParts,
  pluginLines,
  pluginNames,
  recentUses,
  selectedGroup,
  switchRequest,
  validGroupDetail,
  validOverview,
  wouldBlock,
  type OverviewGroup,
  type OverviewSection,
  type PluginLine,
} from '@/utils/privacyPage'

const { t, te } = useI18n()
const pluginStore = usePluginStore()

const overview = ref<PrivacyOverview | null>(null)
const catalog = ref<PermissionCatalog | null>(null)
const detail = ref<PrivacyGroupDetail | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const chosen = ref<string | null>(null)
const opened = ref(new Set<RiskTier>())
const onlyWouldBlock = ref(false)
const busy = ref<string | null>(null)
let detailRequest = 0

const selected = computed(() => (overview.value ? selectedGroup(overview.value, chosen.value) : null))
const sections = computed(() => (overview.value ? overviewSections(overview.value) : []))
const pending = computed(() => (overview.value ? pendingParts(overview.value) : []))
const selectedInfo = computed(() => overview.value?.groups.find((group) => group.group === selected.value) ?? null)
const selectedTier = computed(() => selectedInfo.value?.tier ?? null)
const selectedPartial = computed(() => selectedInfo.value?.partial_block ?? false)
const lines = computed(() => (detail.value ? pluginLines(detail.value) : []))
const names = computed(() => pluginNames(detail.value))
const multiPermission = computed(() => groupPermissions(selected.value).length > 1)
const recent = computed(() => (detail.value ? recentUses(detail.value, onlyWouldBlock.value) : []))
const recentEmptyText = computed(() => {
  if (onlyWouldBlock.value && detail.value?.recent.length) return t('pluginConsent.privacy.recent.emptyFiltered')
  const since = detail.value?.history.since
  return since
    ? t('pluginConsent.privacy.recent.emptySince', { date: formatUseDate(since) })
    : t('pluginConsent.privacy.recent.empty')
})

async function optional<T>(request: Promise<T>, valid: (value: unknown) => value is T): Promise<T | null> {
  try {
    const value = await request
    return valid(value) ? value : null
  } catch {
    return null
  }
}

async function load() {
  loading.value = true
  try {
    const [next, nextCatalog] = await Promise.all([
      optional(getPrivacyOverview(), validOverview),
      catalog.value ? Promise.resolve(catalog.value) : optional(getPermissionCatalog(), validCatalog),
    ])
    catalog.value = nextCatalog
    overview.value = next
  } finally {
    loading.value = false
  }
  if (selected.value) await loadDetail(selected.value)
}

async function loadDetail(group: string) {
  const request = ++detailRequest
  detailLoading.value = true
  const next = await optional(getPrivacyGroup(group), validGroupDetail)
  // A later choice wins over an answer that comes back late.
  if (request !== detailRequest) return
  detail.value = next && next.group === group ? next : null
  detailLoading.value = false
}

function select(group: string) {
  if (group === selected.value) return
  chosen.value = group
  detail.value = null
  onlyWouldBlock.value = false
  void loadDetail(group)
}

function isOpen(section: OverviewSection) {
  return !section.collapsed || opened.value.has(section.tier)
}
function toggleTier(tier: RiskTier) {
  const next = new Set(opened.value)
  if (next.has(tier)) next.delete(tier)
  else next.add(tier)
  opened.value = next
}

function label(permission: string) {
  const key = capabilityMessageKey(permission, 'title')
  return te(key) ? t(key) : permission
}
function groupPermissions(group: string | null): string[] {
  return catalog.value?.privacy_groups.find((item) => item.group === group)?.permissions ?? []
}
/** Personal data has a name of its own; the other groups are their permissions. */
function groupName(group: string) {
  const key = `pluginConsent.privacy.groups.${group}`
  if (te(key)) return t(key)
  const permissions = groupPermissions(group)
  return permissions.length ? permissions.map(label).join(t('pluginConsent.privacy.nameSeparator')) : group
}
function readName(permission: string) {
  const group = catalog.value?.permissions[permission]?.privacy_group
  return group && te(`pluginConsent.privacy.groups.${group}`) ? t(`pluginConsent.privacy.groups.${group}`) : label(permission)
}
function nameOf(pluginId: string) {
  return names.value.get(pluginId)
    ?? pluginStore.pluginSummariesWithStatus.find((plugin) => plugin.id === pluginId)?.name
    ?? pluginId
}
function countText(group: OverviewGroup) {
  if (!group.plugins) return t('pluginConsent.privacy.count.none')
  const key = PERSONAL_GROUPS.has(group.group) ? 'read' : 'use'
  return t(`pluginConsent.privacy.count.${key}`, { n: group.plugins })
}
function latestText(group: OverviewGroup) {
  if (!group.latest) return t('pluginConsent.privacy.latestNone')
  return t('pluginConsent.privacy.latest', { plugin: nameOf(group.latest.plugin_id), time: formatUseTime(group.latest.at, t) })
}
function usageText(line: PluginLine) {
  const parts: string[] = []
  const usage = line.usage
  if (usage?.kind === 'last') {
    parts.push(t('pluginConsent.tab.lastUse', { time: formatUseTime(usage.at, t) }))
    if (usage.blocked) parts.push(t('pluginConsent.tab.wouldBlock'))
  } else if (usage?.kind === 'unused30d') {
    parts.push(t('pluginConsent.tab.unused30d'))
  } else if (usage?.kind === 'unusedSince') {
    parts.push(t('pluginConsent.tab.unusedSince', { date: formatUseDate(usage.since) }))
  }
  if (line.week) {
    const key = line.week.atLeast ? 'weekAtLeast' : 'week'
    parts.push(t(`pluginConsent.privacy.detail.${key}`, { n: line.week.count }))
  }
  return parts.join(' · ')
}
function useText(use: PrivacyUse) {
  const parts = [t('pluginConsent.privacy.recent.use', { plugin: nameOf(use.plugin_id), permission: label(use.permission) })]
  if (use.target) parts.push(use.target)
  if (use.origin_kind && ORIGIN_KINDS.has(use.origin_kind)) parts.push(t(`pluginConsent.privacy.recent.origin.${use.origin_kind}`))
  if (wouldBlock(use)) parts.push(t('pluginConsent.tab.wouldBlock'))
  return parts.join(' · ')
}

async function toggle(line: PluginLine, on: boolean) {
  if (busy.value) return
  busy.value = line.key
  const request = switchRequest(line, on)
  try {
    if (request.kind === 'clear') await clearPermission(line.pluginId, line.permission)
    else await decidePermission(line.pluginId, line.permission, request.state, request.condition)
  } catch (error) {
    ElMessage.error(t(decisionErrorKey(error)))
  } finally {
    busy.value = null
  }
  await load()
}

onMounted(() => {
  void load()
  if (pluginStore.pluginSummariesWithStatus.length === 0) {
    void pluginStore.fetchPluginSummaries().catch(() => undefined)
  }
})
</script>

<style scoped>
.privacy-page {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
@media (max-width: 1100px) {
  .privacy-page {
    grid-template-columns: minmax(0, 1fr);
  }
}
.privacy-card {
  display: grid;
  gap: 12px;
  min-width: 0;
  padding: 24px 0 20px;
  border-radius: var(--radius-card);
  background: var(--surface-card);
  box-shadow: var(--surface-shadow);
}
.privacy-card > * {
  margin: 0 24px;
}
.privacy-card > .privacy-section,
.privacy-card > .privacy-plugin,
.privacy-card > .privacy-recent {
  margin: 0;
}
.privacy-head {
  display: flex;
  gap: 8px;
  align-items: center;
}
.privacy-overview > .privacy-head {
  margin: 0;
  padding: 0 24px 10px;
  border-bottom: 2px solid var(--perm-high);
}
.privacy-head h1,
.privacy-head h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}
.privacy-subtitle,
.privacy-hint {
  margin-top: 0;
  margin-bottom: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.privacy-state,
.privacy-empty {
  display: flex;
  gap: 12px;
  align-items: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.privacy-notice {
  margin-top: 0;
  margin-bottom: 0;
  padding: 10px 14px;
  border-left: 3px solid var(--perm-medium);
  border-radius: 6px;
  background: var(--perm-medium-bg);
  font-size: 13px;
}
.privacy-notice--alert {
  border-left-color: var(--perm-high);
  background: var(--perm-high-bg);
}
.privacy-pending {
  display: flex;
  gap: 12px;
  align-items: baseline;
  justify-content: space-between;
  padding: 14px 18px;
  border: 1px solid var(--surface-border);
  border-radius: 10px;
  background: var(--surface-inset);
  line-height: 1.6;
}
.privacy-link {
  color: var(--el-color-primary);
  white-space: nowrap;
  text-decoration: none;
}
.privacy-tier {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 10px 24px 6px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}
.privacy-tier--toggle {
  width: 100%;
  border: 0;
  background: none;
  cursor: pointer;
  text-align: left;
}
.privacy-tier__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--perm-tier-color);
}
.privacy-row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1.5fr) 16px;
  gap: 12px;
  align-items: center;
  width: 100%;
  padding: 14px 24px;
  border: 0;
  border-top: 1px solid var(--perm-line);
  border-left: 3px solid var(--perm-tier-color);
  background: var(--perm-tier-bg);
  color: var(--el-text-color-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.privacy-row.is-selected {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}
.privacy-row__name {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.privacy-row__count,
.privacy-row__latest {
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.privacy-row__latest {
  color: var(--el-text-color-secondary);
}
.privacy-row__chevron {
  color: var(--el-text-color-placeholder);
}
.privacy-combo {
  margin-top: 4px;
  margin-bottom: 0;
  padding: 10px 14px;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-regular);
  font-size: 13px;
}
.privacy-plugin {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.6fr) 48px;
  gap: 12px;
  align-items: center;
  padding: 14px 24px;
  border-bottom: 1px solid var(--perm-line);
}
.privacy-plugin__name {
  display: grid;
  gap: 2px;
}
.privacy-plugin__usage {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  color: var(--el-text-color-regular);
  font-size: 13px;
}
.privacy-plugin__switch {
  display: flex;
  justify-content: flex-end;
}
.privacy-recent {
  display: grid;
  gap: 0;
  padding-top: 8px;
}
.privacy-recent__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
}
.privacy-recent__head h3 {
  margin: 0;
  font-size: 16px;
}
.privacy-recent__filter {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
}
.privacy-recent > .privacy-empty {
  padding: 8px 24px;
  margin: 0;
}
.privacy-use {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 12px;
  padding: 10px 24px;
  border-bottom: 1px dashed var(--perm-line);
  font-size: 14px;
}
.privacy-use__time {
  color: var(--el-text-color-secondary);
}
.privacy-would-block,
.privacy-would-block .privacy-use__time {
  color: var(--perm-high);
}
.privacy-footer {
  margin: 24px 24px 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-align: right;
}
.perm-tag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 20px;
  font-weight: normal;
}
.perm-tag--tier {
  background: var(--perm-tier-tag, var(--el-fill-color));
  color: var(--perm-tier-color, var(--el-text-color-secondary));
}
.privacy-suggest {
  background: var(--perm-medium-tag);
  color: #a06a10;
}
.perm-muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
