<template>
  <section class="perm-tab">
    <PluginLegacyConsentBanner :plugin-id="pluginId" />
    <div v-if="loading && !panel" class="perm-tab__state">{{ t('pluginConsent.tab.loading') }}</div>
    <div v-else-if="!panel" class="perm-tab__state">
      {{ t('pluginConsent.tab.loadFailed') }}
      <el-button size="small" @click="load">{{ t('pluginConsent.tab.retry') }}</el-button>
    </div>
    <template v-else>
      <header class="perm-summary" data-test="summary">
        <strong>
          <span>{{ t('pluginConsent.tab.summary.granted') }}</span>
          <span class="perm-summary__count">{{ panel.counts.granted }} / {{ panel.counts.total }}</span>
          <template v-if="panel.counts.undecided">
            · {{ t('pluginConsent.tab.summary.undecided', { n: panel.counts.undecided }) }}
          </template>
          <template v-if="panel.counts.undeclared_found">
            · {{ t('pluginConsent.tab.summary.undeclared', { n: panel.counts.undeclared_found }) }}
          </template>
        </strong>
        <span v-if="isIncomplete" class="perm-tag" data-test="incomplete">
          {{ t('pluginConsent.tab.notices.incomplete') }}
        </span>
        <el-button
          v-if="running"
          class="perm-summary__stop"
          :loading="stopping"
          @click="stopPluginNow"
        >
          {{ t('pluginConsent.tab.stop') }}
        </el-button>
      </header>

      <p v-for="notice in notices" :key="notice.key" class="perm-notice" :data-test="notice.key">
        {{ notice.text }}
      </p>

      <div class="perm-table">
        <div class="perm-head">
          <div class="perm-head__cell">
            <strong>{{ t('pluginConsent.tab.declaredHeader') }}</strong>
            <span>{{ t('pluginConsent.tab.declaredHint') }}</span>
          </div>
          <div class="perm-head__mark" />
          <div class="perm-head__cell">
            <strong>{{ t('pluginConsent.tab.scannedHeader') }}</strong>
            <span>
              {{ t('pluginConsent.tab.collapsedByDefault') }} ·
              <a href="#" data-test="expand-all" @click.prevent="toggleAll">
                {{ allExpanded ? t('pluginConsent.tab.collapseAll') : t('pluginConsent.tab.expandAll') }} ›
              </a>
            </span>
          </div>
        </div>

        <template v-for="section in sections" :key="section.tier">
          <div class="perm-tier" :data-tier="section.tier" :data-test="`tier-${section.tier}`">
            <span class="perm-tier__dot" />
            {{ t(`pluginConsent.tab.tiers.${section.tier}`) }} ·
            {{ t('pluginConsent.tab.tierCount', { n: section.rows.length }) }}
            <span v-if="section.tier === 'high'" class="perm-tier__note">
              {{ t('pluginConsent.tab.tierAlways') }}
            </span>
          </div>
          <button
            v-if="section.collapsed && !openTiers.has(section.tier)"
            class="perm-folded"
            :data-test="`folded-${section.tier}`"
            @click="openTiers.add(section.tier)"
          >
            {{ t('pluginConsent.tab.tierFolded', { names: foldedNames(section.rows), n: section.rows.length }) }} ▾
          </button>
          <div
            v-for="row in section.collapsed && !openTiers.has(section.tier) ? [] : section.rows"
            :key="row.permission"
            class="perm-row"
            :data-tier="row.tier"
            :data-reconcile="row.reconcile ?? 'unknown'"
            :data-test="`row-${row.permission}`"
          >
            <!-- Left: what the plugin declares -->
            <div v-if="leftDeclared(row)" class="perm-cell">
              <div class="perm-cell__title">
                <strong>{{ label(row.permission) }}</strong>
                <span class="perm-tag" :data-tier="row.tier">
                  {{ t(`pluginConsent.tab.level.${row.declared!.level}`) }}
                </span>
                <span v-if="row.state === 'undecided'" class="perm-tag" data-test="undecided">
                  {{ t('pluginConsent.tab.undecided') }}
                </span>
                <span v-if="row.legacy_any" class="perm-tag" data-test="legacy">
                  {{ t('pluginConsent.tab.legacyAny') }}
                </span>
                <span v-if="enforcementTag(row)" class="perm-tag">
                  {{ t(`pluginConsent.tab.${enforcementTag(row)}`) }}
                </span>
                <el-switch
                  class="perm-cell__switch"
                  :model-value="row.state === 'granted'"
                  :loading="busy === row.permission"
                  :disabled="busy !== null"
                  :data-test="`switch-${row.permission}`"
                  :aria-label="label(row.permission)"
                  @update:model-value="(on: string | number | boolean) => toggle(row, Boolean(on))"
                />
              </div>
              <p class="perm-cell__line">
                {{
                  row.declared!.reason
                    ? t('pluginConsent.tab.pluginSays', { reason: row.declared!.reason })
                    : t('pluginConsent.tab.noReason')
                }}
                <span v-if="row.declared!.hosts.length">
                  {{ t('pluginConsent.tab.hosts', { hosts: row.declared!.hosts.join(', ') }) }}
                </span>
              </p>
              <RowFacts :row="row" />
            </div>
            <div v-else class="perm-cell perm-cell--empty" data-test="left-empty">
              {{
                panel.manifest === 'unavailable'
                  ? t('pluginConsent.tab.manifestUnavailableCell')
                  : t('pluginConsent.tab.notDeclared')
              }}
            </div>

            <div class="perm-mark" :data-mark="reconcileMark(row)">{{ reconcileMark(row) }}</div>

            <!-- Right: what the scan found -->
            <div
              v-if="row.scan || !leftDeclared(row)"
              class="perm-cell"
              :class="{ 'perm-cell--alert': row.reconcile === 'undeclared' }"
            >
              <div v-if="!leftDeclared(row)" class="perm-cell__title">
                <strong>{{ label(row.permission) }}</strong>
                <span v-if="row.state === 'undecided'" class="perm-tag" data-test="undecided">
                  {{ t('pluginConsent.tab.undecided') }}
                </span>
                <span v-if="row.legacy_any" class="perm-tag" data-test="legacy">
                  {{ t('pluginConsent.tab.legacyAny') }}
                </span>
                <span v-if="enforcementTag(row)" class="perm-tag">
                  {{ t(`pluginConsent.tab.${enforcementTag(row)}`) }}
                </span>
                <span class="perm-cell__buttons">
                  <el-button
                    size="small"
                    :type="row.state === 'granted' ? 'primary' : undefined"
                    :disabled="busy !== null"
                    :data-test="`allow-${row.permission}`"
                    @click="decide(row, 'granted')"
                  >
                    {{ t('pluginConsent.tab.allow') }}
                  </el-button>
                  <el-button
                    v-if="refusable(row)"
                    size="small"
                    :type="row.state === 'denied' ? 'primary' : undefined"
                    :disabled="busy !== null"
                    :data-test="`refuse-${row.permission}`"
                    @click="decide(row, 'denied')"
                  >
                    {{ t('pluginConsent.tab.refuse') }}
                  </el-button>
                </span>
              </div>
              <div class="perm-cell__scan">
                <span v-if="row.scan">
                  {{ t('pluginConsent.tab.scanSummary', { files: row.scan.files, sites: row.scan.sites }) }}
                  <template v-if="row.scan.possible_only">{{ t('pluginConsent.tab.scanPossible') }}</template>
                </span>
                <span v-else class="perm-muted">{{ scanEmptyText(row) }}</span>
                <a
                  v-if="row.scan"
                  href="#"
                  class="perm-link"
                  :data-test="`expand-${row.permission}`"
                  @click.prevent="toggleRow(row.permission)"
                >
                  {{ expanded.has(row.permission) ? t('pluginConsent.tab.collapse') : t('pluginConsent.tab.expand') }} ›
                </a>
              </div>
              <ul v-if="row.scan && expanded.has(row.permission)" class="perm-files" data-test="scan-files">
                <li v-for="file in scanFiles(panel, row.permission)" :key="file.file">
                  <code>{{ file.file }}</code>
                  <span>{{ t('pluginConsent.tab.scanFileSites', { n: file.sites }) }}</span>
                  <code v-if="file.methods.length" class="perm-muted">{{ file.methods.join(' / ') }}</code>
                </li>
              </ul>
              <p v-if="row.reconcile === 'undeclared'" class="perm-cell__warn">
                {{ t('pluginConsent.tab.undeclaredWarning') }}
              </p>
              <RowFacts v-if="!leftDeclared(row)" :row="row" />
            </div>
            <div v-else class="perm-cell perm-cell--empty" data-test="right-empty">
              {{ scanEmptyText(row) }}
            </div>
          </div>
        </template>

        <div class="perm-foot">
          <p class="perm-safe">{{ t('pluginConsent.tab.safe') }}</p>
          <div v-if="spotFiles.length || panel.blind_spots.not_analyzed_files" class="perm-spots" data-test="blind-spots">
            <div class="perm-spots__title">
              <strong v-if="spotFiles.length">⚠ {{ t('pluginConsent.tab.blindSpots.title', { n: spotFiles.length }) }}</strong>
              <strong v-else data-test="blind-spots-none">{{ t('pluginConsent.tab.blindSpots.none') }}</strong>
              <span v-if="spotFiles.length" class="perm-muted">{{ t('pluginConsent.tab.blindSpots.subtitle') }}</span>
            </div>
            <ul class="perm-spots__list">
              <li v-for="spot in spotFiles" :key="spot.file!">
                <code>{{ spot.file }}</code>
                <span>{{ spotText(spot.types) }}</span>
                <span v-if="spot.high_risk" class="perm-tag" data-tier="high">{{ t('pluginConsent.tab.tiers.high') }}</span>
              </li>
            </ul>
            <p v-if="panel.blind_spots.not_analyzed_files" class="perm-muted">
              {{ t('pluginConsent.tab.blindSpots.notAnalyzed', { n: panel.blind_spots.not_analyzed_files }) }}
            </p>
          </div>
        </div>
      </div>

      <details class="perm-dev" data-test="developer">
        <summary>
          <strong>{{ t('pluginConsent.tab.developer.title') }}</strong>
          <span class="perm-muted"> · {{ t('pluginConsent.tab.developer.subtitle') }}</span>
        </summary>
        <div class="perm-dev__body">
          <table class="perm-dev__table">
            <thead>
              <tr>
                <th>{{ t('pluginConsent.tab.developer.name') }}</th>
                <th>{{ t('pluginConsent.tab.developer.id') }}</th>
                <th>{{ t('pluginConsent.tab.developer.declaration') }}</th>
                <th>{{ t('pluginConsent.tab.developer.state') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in panel.rows" :key="row.permission">
                <td>{{ label(row.permission) }}</td>
                <td><code>{{ row.permission }}</code></td>
                <td>
                  {{
                    row.declared
                      ? [row.declared.level, row.declared.background ? 'background' : null].filter(Boolean).join(' · ')
                      : t('pluginConsent.tab.developer.scanFound')
                  }}
                </td>
                <td>{{ [row.state, row.condition].filter(Boolean).join(' · ') }}</td>
              </tr>
            </tbody>
          </table>
          <div class="perm-dev__side">
            <pre v-if="manifestText" class="perm-dev__toml">{{ manifestText }}</pre>
            <p v-else class="perm-muted">{{ t('pluginConsent.tab.developer.manifestNone') }}</p>
            <ul v-if="devLocations.length" class="perm-dev__locations">
              <li v-for="item in devLocations" :key="item"><code>{{ item }}</code></li>
            </ul>
          </div>
          <p class="perm-dev__footer" data-test="source-line">
            {{ sourceLine }}
            <span v-if="!panel.source.confirmed" class="perm-tag" data-tier="high" data-test="source-unconfirmed">
              {{ t('pluginConsent.tab.developer.sourceUnconfirmed') }}
            </span>
          </p>
        </div>
      </details>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import type { PropType } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import {
  clearPermission,
  decidePermission,
  getPendingPermissions,
  getPermissionAuditHistory,
  getPermissionCatalog,
  getPermissionPanel,
  type PanelRow,
  type PendingPermissions,
  type PermissionAuditPage,
  type PermissionCatalog,
  type PermissionCondition,
  type PermissionPanel,
} from '@/api/pluginPermissions'
import { usePluginStore } from '@/stores/plugin'
import PluginLegacyConsentBanner from './PluginLegacyConsentBanner.vue'
import { capabilityMessageKey } from '@/utils/pluginCapabilities'
import {
  blindSpotFiles,
  conditionLock,
  controlSide,
  decisionErrorKey,
  enforcementTag,
  firstUse,
  incomplete,
  partialBlock,
  reconcileMark,
  relativeDay,
  scanFiles,
  showsCondition,
  tierSections,
  usageLine,
} from '@/utils/permissionTab'

const props = defineProps<{ pluginId: string }>()
const { t, te } = useI18n()
const store = usePluginStore()

/** Disclosure only: the server takes no refusal for it (back to undecided instead). */
const NOT_REFUSABLE = new Set(['plugin_sdk.main_agent.messages:push'])

const panel = ref<PermissionPanel | null>(null)
const catalog = ref<PermissionCatalog | null>(null)
const audit = ref<PermissionAuditPage | null>(null)
const pending = ref<PendingPermissions | null>(null)
const loading = ref(false)
const busy = ref<string | null>(null)
const stopping = ref(false)
const expanded = reactive(new Set<string>())
const openTiers = reactive(new Set<string>())

const running = computed(() => store.getPluginById(props.pluginId)?.status === 'running')
const sections = computed(() => (panel.value ? tierSections(panel.value) : []))
const spotFiles = computed(() => (panel.value ? blindSpotFiles(panel.value) : []))
const isIncomplete = computed(() => (panel.value ? incomplete(panel.value, pending.value) : false))
const scannedRows = computed(() => panel.value?.rows.filter((row) => row.scan) ?? [])
const allExpanded = computed(
  () => scannedRows.value.length > 0 && scannedRows.value.every((row) => expanded.has(row.permission)),
)

async function optional<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request
  } catch {
    return null
  }
}

async function load() {
  loading.value = true
  try {
    const [nextPanel, nextCatalog, nextAudit, nextPending] = await Promise.all([
      optional(getPermissionPanel(props.pluginId)),
      optional(getPermissionCatalog()),
      optional(getPermissionAuditHistory(props.pluginId)),
      optional(getPendingPermissions()),
    ])
    panel.value = nextPanel
    catalog.value = nextCatalog
    audit.value = nextAudit
    pending.value = nextPending
  } finally {
    loading.value = false
  }
}
onMounted(load)

function label(permission: string) {
  const key = capabilityMessageKey(permission, 'title')
  return te(key) ? t(key) : permission
}
function leftDeclared(row: PanelRow) {
  return controlSide(row, panel.value!.manifest) === 'left'
}
function refusable(row: PanelRow) {
  return !NOT_REFUSABLE.has(row.permission)
}
function scanEmptyText(row: PanelRow) {
  switch (row.reconcile) {
    case 'unscanned':
      return t('pluginConsent.tab.scanNone')
    case 'scan_pending':
      return t('pluginConsent.tab.scanPending')
    case 'scan_unavailable':
      return t('pluginConsent.tab.scanUnavailable')
    default:
      return ''
  }
}
function foldedNames(rows: PanelRow[]) {
  return rows.slice(0, 2).map((row) => label(row.permission)).join(t('pluginConsent.tab.listSeparator'))
}
function spotText(types: Record<string, number>) {
  return Object.entries(types)
    .filter(([, n]) => n > 0)
    .map(([kind, n]) => {
      const key = `pluginConsent.tab.blindSpots.types.${kind}`
      return te(key) ? t(key, { n }) : `${kind} × ${n}`
    })
    .join(t('pluginConsent.tab.listSeparator'))
}
function formatTime(value: string) {
  const at = dayjs(value)
  if (!at.isValid()) return value
  const day = relativeDay(value)
  return day ? t(`pluginConsent.tab.${day}`, { time: at.format('HH:mm') }) : at.format('YYYY-MM-DD HH:mm')
}
function formatDate(value: string) {
  const at = dayjs(value)
  return at.isValid() ? at.format('YYYY-MM-DD') : value
}

const notices = computed(() => {
  const p = panel.value
  if (!p) return []
  const out: Array<{ key: string; text: string }> = []
  if (p.manifest === 'unavailable')
    out.push({ key: 'notice-unavailable', text: t('pluginConsent.tab.notices.manifestUnavailable') })
  if (p.manifest === 'unusable')
    out.push({ key: 'notice-unusable', text: t('pluginConsent.tab.notices.manifestUnusable') })
  if (p.grants_reset)
    out.push({
      key: 'notice-reset',
      text: p.grants_reset.at
        ? t('pluginConsent.tab.notices.grantsReset', { date: formatDate(p.grants_reset.at) })
        : t('pluginConsent.tab.notices.grantsResetPending'),
    })
  return out
})

const manifestText = computed(() => {
  const table = panel.value?.developer.manifest_permissions
  if (!table) return ''
  const lines: string[] = []
  for (const [level, items] of Object.entries(table)) {
    if (!items || typeof items !== 'object') continue
    lines.push(`[permissions.${level}]`)
    for (const [id, value] of Object.entries(items as Record<string, unknown>)) {
      lines.push(`"${id}" = ${JSON.stringify(value)}`)
    }
    lines.push('')
  }
  return lines.join('\n').trim()
})

const devLocations = computed(() => {
  const p = panel.value
  if (!p) return []
  const out: string[] = []
  for (const [permission, findings] of Object.entries(p.developer.findings)) {
    for (const finding of findings) {
      out.push(`${permission}  ${finding.file ?? '?'}:${finding.line ?? '?'}  ${finding.method ?? ''}`.trim())
    }
  }
  for (const spot of p.developer.blind_spots) {
    out.push(`${spot.type}  ${spot.file ?? '?'}:${spot.line ?? '?'}`)
  }
  return out
})

const sourceLine = computed(() => {
  const p = panel.value!
  const unknown = t('pluginConsent.tab.developer.unknown')
  const parts = [
    t('pluginConsent.tab.developer.analyzer', { version: p.analysis.analyzer_version || unknown }),
    t('pluginConsent.tab.developer.scannedAt', {
      date: p.analysis.analyzed_at ? formatTime(p.analysis.analyzed_at) : unknown,
    }),
    t('pluginConsent.tab.developer.source', { channel: p.source.channel || unknown }) +
      (p.source.fingerprint8 ? t('pluginConsent.tab.developer.fingerprint', { fp: p.source.fingerprint8 }) : ''),
  ]
  return parts.join(' · ')
})

function toggleRow(permission: string) {
  if (expanded.has(permission)) expanded.delete(permission)
  else expanded.add(permission)
}
function toggleAll() {
  const open = !allExpanded.value
  for (const row of scannedRows.value) {
    if (open) expanded.add(row.permission)
    else expanded.delete(row.permission)
  }
}

async function save(row: PanelRow, run: () => Promise<PermissionPanel>) {
  busy.value = row.permission
  try {
    panel.value = await run()
  } catch (error) {
    ElMessage.error(t(decisionErrorKey(error)))
  } finally {
    busy.value = null
  }
}
function decide(row: PanelRow, state: 'granted' | 'denied', condition?: PermissionCondition) {
  const keep = state === 'granted' ? (condition ?? row.condition ?? 'in_use') : 'in_use'
  return save(row, () => decidePermission(props.pluginId, row.permission, state, keep))
}
function toggle(row: PanelRow, on: boolean) {
  if (on) return decide(row, 'granted')
  if (!refusable(row)) return save(row, () => clearPermission(props.pluginId, row.permission))
  return decide(row, 'denied')
}
async function stopPluginNow() {
  stopping.value = true
  try {
    await store.stop(props.pluginId)
  } catch {
    ElMessage.error(t('messages.stopFailed'))
  } finally {
    stopping.value = false
  }
}

/** Condition, last use, uses while undecided and notes under a row. */
const RowFacts = defineComponent({
  props: { row: { type: Object as PropType<PanelRow>, required: true } },
  setup(factProps) {
    return () => {
      const row = factProps.row
      const p = panel.value!
      const children = []
      if (showsCondition(row)) {
        const lock = conditionLock(row, p.manifest)
        children.push(
          h('div', { class: 'perm-condition', 'data-test': `condition-${row.permission}` }, [
            h('span', t('pluginConsent.tab.condition.label')),
            h(
              'select',
              {
                value: row.condition ?? 'in_use',
                disabled: lock !== null || busy.value !== null,
                'aria-label': `${label(row.permission)} · ${t('pluginConsent.tab.condition.label')}`,
                'data-test': `condition-select-${row.permission}`,
                onChange: (event: Event) =>
                  decide(row, 'granted', (event.target as HTMLSelectElement).value as PermissionCondition),
              },
              (['in_use', 'anytime'] as const).map((value) =>
                h('option', { value }, t(`pluginConsent.tab.condition.${value}`)),
              ),
            ),
            h('span', { class: 'perm-tag' }, t('pluginConsent.tab.condition.selfReported')),
            lock
              ? h(
                  'span',
                  { class: 'perm-muted', 'data-test': `condition-lock-${row.permission}` },
                  t(`pluginConsent.tab.condition.lock.${lock}`),
                )
              : null,
          ]),
        )
      }
      const usage = usageLine(row, audit.value?.history ?? null, audit.value?.lost_at ?? null)
      if (usage) {
        let text = ''
        if (usage.kind === 'last') {
          text = t('pluginConsent.tab.lastUse', { time: formatTime(usage.at) })
          if (usage.blocked) text += ` · ${t('pluginConsent.tab.wouldBlock')}`
        } else if (usage.kind === 'unusedSince') {
          text = t('pluginConsent.tab.unusedSince', { date: formatDate(usage.since) })
        }
        const unused = usage.kind === 'unused30d' || (usage.kind === 'last' && usage.unused30d)
        children.push(
          h('p', { class: 'perm-cell__line perm-muted', 'data-test': `usage-${row.permission}` }, [
            text,
            unused ? h('span', { class: 'perm-tag', 'data-test': 'unused30d' }, t('pluginConsent.tab.unused30d')) : null,
          ]),
        )
      }
      const uses = firstUse(pending.value, p.plugin_id, row)
      if (uses) {
        children.push(
          h(
            'p',
            { class: 'perm-cell__line perm-muted', 'data-test': `first-use-${row.permission}` },
            `${t(uses.atLeast ? 'pluginConsent.tab.firstUseAtLeast' : 'pluginConsent.tab.firstUse', { n: uses.count })} · ${t('pluginConsent.tab.wouldBlock')}`,
          ),
        )
      }
      if (partialBlock(catalog.value, row.permission)) {
        children.push(
          h(
            'p',
            { class: 'perm-cell__line perm-cell__note', 'data-test': `partial-${row.permission}` },
            t('pluginConsent.tab.partialBlock'),
          ),
        )
      }
      return h('div', { class: 'perm-facts' }, children)
    }
  },
})
</script>

<style scoped>
.perm-tab {
  display: grid;
  gap: 12px;
  --perm-high: #d9534f;
  --perm-high-bg: #fdf8f7;
  --perm-high-tag: #fbe9e7;
  --perm-medium: #e0a84a;
  --perm-medium-bg: #fffdf6;
  --perm-medium-tag: #fbf1dc;
  --perm-low: #7aa7d6;
  --perm-low-bg: #f8fbfe;
  --perm-low-tag: #e6effa;
  --perm-line: var(--el-border-color-lighter);
  --perm-summary-bg: #fff8ef;
  --perm-summary-border: #f3c58f;
}
.perm-tab__state {
  display: flex;
  gap: 12px;
  align-items: center;
  color: var(--el-text-color-secondary);
}
.perm-summary {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 12px 16px;
  border: 1px solid var(--perm-summary-border);
  border-radius: 8px;
  background: var(--perm-summary-bg);
}
.perm-summary__count {
  margin-left: 4px;
  color: #c2410c;
}
.perm-summary__stop {
  margin-left: auto;
}
.perm-notice {
  margin: 0;
  padding: 8px 14px;
  border-left: 3px solid var(--perm-medium);
  background: var(--perm-medium-bg);
  font-size: 13px;
}
.perm-table {
  border: 1px solid var(--perm-line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--el-bg-color);
}
.perm-head,
.perm-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 48px minmax(0, 1fr);
}
.perm-head {
  padding: 10px 0;
  border-bottom: 1px solid var(--perm-line);
  background: var(--el-fill-color-lighter);
}
.perm-head__cell {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  padding: 0 16px;
  font-size: 13px;
}
.perm-head__cell span {
  color: var(--el-text-color-secondary);
}
.perm-tier {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 10px 16px 6px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}
.perm-tier__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--perm-tier-color);
}
.perm-tier__note {
  margin-left: auto;
  color: var(--el-text-color-secondary);
}
[data-tier='high'] {
  --perm-tier-color: var(--perm-high);
  --perm-tier-bg: var(--perm-high-bg);
  --perm-tier-tag: var(--perm-high-tag);
}
[data-tier='medium'] {
  --perm-tier-color: var(--perm-medium);
  --perm-tier-bg: var(--perm-medium-bg);
  --perm-tier-tag: var(--perm-medium-tag);
}
[data-tier='low'] {
  --perm-tier-color: var(--perm-low);
  --perm-tier-bg: var(--perm-low-bg);
  --perm-tier-tag: var(--perm-low-tag);
}
.perm-folded {
  display: block;
  width: 100%;
  padding: 8px 16px 12px;
  border: 0;
  background: none;
  color: var(--el-text-color-secondary);
  text-align: left;
  cursor: pointer;
}
.perm-row {
  border-left: 3px solid var(--perm-tier-color);
  border-top: 1px solid var(--perm-line);
  background: var(--perm-tier-bg);
}
.perm-cell {
  display: grid;
  gap: 4px;
  align-content: start;
  min-width: 0;
  padding: 12px 16px;
}
.perm-cell--empty {
  color: var(--el-text-color-placeholder);
  font-size: 13px;
  background: repeating-linear-gradient(
    135deg,
    transparent 0 8px,
    rgba(0, 0, 0, 0.03) 8px 16px
  );
}
.perm-cell--alert {
  background: #fdf3f2;
}
.perm-cell__title {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.perm-cell__switch,
.perm-cell__buttons {
  margin-left: auto;
}
.perm-tab :deep(.perm-cell__line) {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.perm-tab :deep(.perm-cell__note) {
  color: var(--perm-high);
}
.perm-cell__warn {
  margin: 0;
  color: var(--perm-high);
  font-size: 13px;
  font-weight: 600;
}
.perm-cell__scan {
  display: flex;
  gap: 8px;
  justify-content: space-between;
  font-size: 13px;
}
.perm-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
}
.perm-mark[data-mark='='] {
  color: var(--el-color-success);
}
.perm-mark[data-mark='≠'] {
  color: var(--perm-high);
}
.perm-mark[data-mark='?'] {
  color: var(--el-text-color-secondary);
}
.perm-tab :deep(.perm-tag) {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--perm-tier-tag, var(--el-fill-color));
  color: var(--perm-tier-color, var(--el-text-color-secondary));
  font-size: 12px;
  line-height: 20px;
  font-weight: normal;
}
.perm-tab :deep(.perm-cell__line .perm-tag) {
  margin-left: 6px;
}
.perm-tab :deep(.perm-muted) {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.perm-link {
  color: var(--el-color-primary);
  white-space: nowrap;
}
.perm-files,
.perm-spots__list,
.perm-dev__locations {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
  font-size: 13px;
}
.perm-files li,
.perm-spots__list li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.perm-tab :deep(.perm-condition select) {
  padding: 2px 6px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  font: inherit;
}
.perm-tab :deep(.perm-condition) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  width: fit-content;
  margin-top: 4px;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-lighter);
  font-size: 13px;
}
.perm-tab :deep(.perm-facts) {
  display: grid;
  gap: 4px;
}
.perm-foot {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  padding: 12px 16px;
  border-top: 1px solid var(--perm-line);
}
.perm-safe {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.perm-spots {
  display: grid;
  gap: 6px;
  padding: 10px 14px;
  border: 1px solid var(--perm-line);
  border-radius: 8px;
}
.perm-spots__title {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
}
.perm-dev {
  border: 1px solid var(--perm-line);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.perm-dev summary {
  padding: 10px 16px;
  cursor: pointer;
}
.perm-dev__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  padding: 0 16px 12px;
}
.perm-dev__table {
  border-collapse: collapse;
  font-size: 13px;
}
.perm-dev__table th,
.perm-dev__table td {
  padding: 6px 8px;
  border: 1px solid var(--perm-line);
  text-align: left;
  vertical-align: top;
}
.perm-dev__toml {
  margin: 0 0 8px;
  padding: 10px;
  border: 1px solid var(--perm-line);
  border-radius: 6px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.perm-dev__footer {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
@media (max-width: 1100px) {
  .perm-head,
  .perm-row {
    grid-template-columns: minmax(0, 1fr);
  }
  .perm-mark {
    justify-content: flex-start;
    padding: 0 16px;
  }
  .perm-foot,
  .perm-dev__body {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
