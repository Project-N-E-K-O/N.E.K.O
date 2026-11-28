<template>
  <section class="perm-tab perm-palette">
    <PluginLegacyConsentBanner :plugin-id="pluginId" @changed="load()" />
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
          :loading="stopAction.isStopping(pluginId)"
          data-test="stop-plugin"
          @click="stopAction.stop(pluginId)"
        >
          {{ t('pluginConsent.tab.stop') }}
        </el-button>
      </header>

      <p v-for="notice in notices" :key="notice.key" class="perm-notice" :data-test="notice.key">
        {{ notice.text }}
        <el-button
          v-if="notice.rescan"
          size="small"
          text
          type="primary"
          :loading="rescanning"
          data-test="rescan"
          @click="rescan"
        >
          {{ t('pluginConsent.retry') }}
        </el-button>
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
                  <!-- Disclosure only: it cannot be refused, but an approval can be taken back. -->
                  <el-button
                    v-else-if="row.state === 'granted'"
                    size="small"
                    :disabled="busy !== null"
                    :data-test="`undecide-${row.permission}`"
                    @click="save(row, () => clearPermission(pluginId, row.permission))"
                  >
                    {{ t('pluginConsent.tab.undecide') }}
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
          <div v-if="showSpots" class="perm-spots" data-test="blind-spots">
            <div class="perm-spots__title">
              <strong v-if="spotFiles.length">⚠ {{ t('pluginConsent.tab.blindSpots.title', { n: spotFiles.length }) }}</strong>
              <strong v-else-if="panel.analysis.status === 'completed'" data-test="blind-spots-none">{{ t('pluginConsent.tab.blindSpots.none') }}</strong>
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
              <li v-for="(item, index) in devLocations" :key="index"><code>{{ item }}</code></li>
            </ul>
            <ul v-if="scanWarnings.length" class="perm-dev__locations" data-test="scan-warnings">
              <li v-for="(item, index) in scanWarnings" :key="index">{{ item }}</li>
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
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { PropType } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import '@/assets/styles/permissionPalette.css'
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
import { usePluginConsentState } from '@/stores/pluginConsentState'
import { usePluginStopAction } from '@/composables/usePluginStopAction'
import PluginLegacyConsentBanner from './PluginLegacyConsentBanner.vue'
import { capabilityMessageKey } from '@/utils/pluginCapabilities'
import {
  blindSpotFiles,
  conditionLock,
  controlSide,
  decisionErrorKey,
  enforcementTag,
  firstUse,
  grantCondition,
  incomplete,
  partialBlock,
  permissionsToml,
  reconcileMark,
  formatUseDate,
  formatUseTime,
  NOT_REFUSABLE,
  scanFiles,
  scanNotice,
  scanPending,
  showsCondition,
  tierSections,
  usageLine,
  validCatalog,
  validHistory,
  validPanel,
  validPending,
} from '@/utils/permissionTab'

const props = defineProps<{ pluginId: string }>()
const { t, te } = useI18n()
const store = usePluginStore()
const consentState = usePluginConsentState()
const stopAction = usePluginStopAction()

const panel = ref<PermissionPanel | null>(null)
const catalog = ref<PermissionCatalog | null>(null)
const audit = ref<PermissionAuditPage | null>(null)
const pending = ref<PendingPermissions | null>(null)
const loading = ref(false)
const busy = ref<string | null>(null)
const rescanning = ref(false)
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

/** While the scan is queued or running, the panel is read again this often. */
const SCAN_POLL_MS = 5000
let loadSeq = 0
/** Saves in flight: a load answered meanwhile may predate them. */
let saving = 0
/** A load was dropped during a save: it runs again once the save is done. */
let reloadAfterSave = false
let pollTimer: ReturnType<typeof setTimeout> | null = null
/** Failed refreshes in a row: the next poll waits longer (up to a minute). */
let failedPolls = 0
/** Answers in a row with the declaration unreadable: read again less often too. */
let heldPolls = 0
const MAX_POLL_MS = 60000
let unmounted = false

/**
 * Whether the panel is read again on its own: a scan still to come, a
 * declaration held by a plugin operation for now, or a failed refresh.
 */
function needsRefresh(p: PermissionPanel | null): boolean {
  return Boolean(p && (scanPending(p) || p.manifest === 'unavailable' || failedPolls > 0))
}
/** A running scan is followed every 5 s; a failed refresh or a declaration still unreadable waits longer each time. */
function nextPollMs() {
  const held = panel.value && !scanPending(panel.value) ? heldPolls : 0
  return Math.min(SCAN_POLL_MS * 2 ** Math.max(failedPolls, held), MAX_POLL_MS)
}
/**
 * Only the timed reads of a finished scan move the backoff for an unreadable
 * declaration; a user action or a running scan starts it over.
 */
function shown(next: PermissionPanel, timed = false) {
  panel.value = next
  failedPolls = 0
  heldPolls = next.manifest === 'unavailable' && timed && !scanPending(next) ? heldPolls + 1 : 0
}
const poll = () => load(true)

function stopPolling() {
  if (pollTimer !== null) clearTimeout(pollTimer)
  pollTimer = null
}

async function load(timed = false) {
  if (saving > 0) {
    // Not even sent: its answer could arrive after the save and predate it.
    reloadAfterSave = true
    return
  }
  const seq = ++loadSeq
  stopPolling()
  loading.value = true
  try {
    const [nextPanel, nextCatalog, nextAudit, nextPending] = await Promise.all([
      optional(getPermissionPanel(props.pluginId)),
      optional(getPermissionCatalog()),
      optional(getPermissionAuditHistory(props.pluginId)),
      optional(getPendingPermissions()),
    ])
    // A later load owns the view now; a save that started meanwhile asked for a new one.
    if (seq !== loadSeq || unmounted) return
    // A refresh that fails keeps what is shown: only the first load can fail the tab.
    if (validPanel(nextPanel)) {
      shown(nextPanel, timed)
    } else if (panel.value) {
      failedPolls++
    }
    if (validCatalog(nextCatalog)) catalog.value = nextCatalog
    // Without a current answer, nothing is claimed about unused time.
    audit.value = validHistory(nextAudit) ? nextAudit : null
    if (validPending(nextPending)) pending.value = nextPending
    else if (pending.value && validPanel(nextPanel)) {
      // The old counts belong to the rows before this answer: not shown for them.
      pending.value = {
        ...pending.value,
        first_use: pending.value.first_use.filter((use) => use.plugin_id !== props.pluginId),
        incomplete: pending.value.incomplete.filter((id) => id !== props.pluginId),
      }
    }
    // Follow a running scan or a held declaration; retry a failed refresh too, waiting longer each time.
    if (needsRefresh(panel.value)) pollTimer = setTimeout(poll, nextPollMs())
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}
onMounted(() => load())
onBeforeUnmount(() => {
  unmounted = true
  stopPolling()
})

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
  return formatUseTime(value, t)
}
const formatDate = formatUseDate

const notices = computed(() => {
  const p = panel.value
  if (!p) return []
  const out: Array<{ key: string; text: string; rescan?: boolean }> = []
  if (p.manifest === 'unavailable')
    out.push({ key: 'notice-unavailable', text: t('pluginConsent.tab.notices.manifestUnavailable') })
  if (p.manifest === 'unusable')
    out.push({ key: 'notice-unusable', text: t('pluginConsent.tab.notices.manifestUnusable') })
  const scan = scanNotice(p)
  if (scan)
    out.push({
      key: `notice-scan-${scan}`,
      text: t(`pluginConsent.tab.notices.scan.${scan}`),
      rescan: scan === 'failed' || scan === 'partial',
    })
  if (p.grants_reset)
    out.push({
      key: 'notice-reset',
      text: p.grants_reset.at
        ? t('pluginConsent.tab.notices.grantsReset', { date: formatDate(p.grants_reset.at) })
        : t('pluginConsent.tab.notices.grantsResetPending'),
    })
  return out
})

/** Warnings of the last scan (budget, unreadable files), from the consent record. */
const scanWarnings = computed(() =>
  (consentState.details[props.pluginId]?.analysis.warnings ?? []).map((warning) => {
    const key = `pluginConsent.warnings.${warning.code}`
    const text = te(key) ? t(key) : warning.code
    return warning.file ? `${text} (${warning.file})` : text
  }),
)

async function rescan() {
  rescanning.value = true
  try {
    await consentState.analyze(props.pluginId)
  } catch {
    ElMessage.error(t('pluginConsent.tab.errors.rescanFailed'))
  } finally {
    rescanning.value = false
    await load()
  }
}

const manifestText = computed(() => permissionsToml(panel.value?.developer.manifest_permissions ?? null))
/** Shown once the scan answered, also to say that nothing was hard to see. */
const showSpots = computed(() => {
  const p = panel.value
  if (!p) return false
  const status = p.analysis.status
  // A partial scan has no "nothing found" to report, only what it found.
  return status === 'completed' || spotFiles.value.length > 0 || p.blind_spots.not_analyzed_files > 0
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
    if (open) {
      expanded.add(row.permission)
      // A folded tier would hide what was just expanded.
      openTiers.add(row.tier)
    } else {
      // Tiers the user opened stay open.
      expanded.delete(row.permission)
    }
  }
}

async function save(row: PanelRow, run: () => Promise<PermissionPanel>) {
  busy.value = row.permission
  // A load still on its way, or one a timer starts now, would put the old answer back.
  // A load on its way is dropped below; it was asked for, so it runs again after the save.
  if (loading.value) reloadAfterSave = true
  loadSeq++
  saving++
  stopPolling()
  loading.value = false
  try {
    const next = await run()
    if (validPanel(next)) shown(next)
    // The plugin-wide consent line above reads the shared record: it may have moved too.
    void consentState.load(props.pluginId).catch(() => undefined)
    // Uses while undecided count from the last answer, and the last-use
    // coverage may have moved: both are read again.
    const [nextPending, nextAudit] = await Promise.all([
      optional(getPendingPermissions()),
      optional(getPermissionAuditHistory(props.pluginId)),
    ])
    if (validPending(nextPending)) pending.value = nextPending
    else if (pending.value) {
      // The old count belongs to the answer before this one: not shown for it.
      pending.value = {
        ...pending.value,
        first_use: pending.value.first_use.filter(
          (use) => !(use.plugin_id === props.pluginId && use.permission === row.permission),
        ),
        // A panel that came back with this save says itself whether the declaration is read.
        incomplete: validPanel(next)
          ? pending.value.incomplete.filter((id) => id !== props.pluginId)
          : pending.value.incomplete,
      }
    }
    // Without a current answer, nothing is claimed about unused time.
    audit.value = validHistory(nextAudit) ? nextAudit : null
  } catch (error) {
    ElMessage.error(t(decisionErrorKey(error)))
  } finally {
    saving--
    busy.value = null
    if (!unmounted && saving === 0) {
      if (reloadAfterSave) {
        reloadAfterSave = false
        void load()
      } else if (pollTimer === null && needsRefresh(panel.value)) {
        // Resume what the save paused: following a running scan, or retrying a
        // failed refresh (with its backoff), also after a failed save.
        pollTimer = setTimeout(poll, nextPollMs())
      }
    }
  }
}
function decide(row: PanelRow, state: 'granted' | 'denied', condition?: PermissionCondition) {
  const keep = state === 'granted' ? grantCondition(row, condition) : 'in_use'
  return save(row, () => decidePermission(props.pluginId, row.permission, state, keep))
}
function toggle(row: PanelRow, on: boolean) {
  if (on) return decide(row, 'granted')
  if (!refusable(row)) return save(row, () => clearPermission(props.pluginId, row.permission))
  return decide(row, 'denied')
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
                // Only "any time" is locked: narrowing to "while in use" stays possible.
                disabled: busy.value !== null,
                'aria-label': `${label(row.permission)} · ${t('pluginConsent.tab.condition.label')}`,
                'data-test': `condition-select-${row.permission}`,
                onChange: (event: Event) =>
                  decide(row, 'granted', (event.target as HTMLSelectElement).value as PermissionCondition),
              },
              (['in_use', 'anytime'] as const).map((value) =>
                h(
                  'option',
                  { value, disabled: value === 'anytime' && lock !== null },
                  t(`pluginConsent.tab.condition.${value}`),
                ),
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
