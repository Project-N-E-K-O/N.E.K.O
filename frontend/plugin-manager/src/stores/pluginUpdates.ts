/**
 * "Which installed Market plugins have a newer release?" — check, show, upgrade.
 *
 * Data path (no new backend surface is involved):
 *
 *   pluginStore.plugins[]  →  install_source.source_detail
 *       { plugin_market_id, version, channel }
 *   marketVersions.ensureFresh()  →  GET /market/catalog/api/v1/plugins/latest-versions
 *   hasNewerVersion(current, latest)
 *
 * Everything here is best-effort: a failed lookup must never block the panel,
 * never raise a visible error, and never make an unchecked plugin look up to
 * date. Failures are logged (frontend console with the `[plugin-updates]`
 * prefix, plus structured `[market-catalog]` / `[market-update-check]` lines in
 * the plugin server log, visible under "Server Logs" in this panel).
 *
 * The bridge helpers below are intentionally a copy of the ones in
 * ``components/plugin/MarketPanel.vue``. Extracting a shared module would mean
 * touching that file's install flow, which is out of scope for this feature.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { fetchMarketPlugin } from '@/api/market'
import { useMarketVersionsStore } from '@/stores/marketVersions'
import { usePluginStore } from '@/stores/plugin'
import { narrowMarketChannel } from '@/utils/narrowChannel'
import { resolvePluginInstallErrorKey } from '@/utils/pluginInstallError'
import { hasNewerVersion } from '@/utils/version'
import type { PluginInstallSourceDetailMarket, PluginMeta } from '@/types/api'

const LOG_PREFIX = '[plugin-updates]'
const POPUP_FLAG_KEY = 'neko_plugin_update_popup_shown'
const TOKEN_STORAGE_KEY = 'neko_bridge_token'
const TASK_POLL_INTERVAL_MS = 800
/** Consecutive 404s before we accept that the backend task is gone. */
const TASK_MISSING_TOLERANCE = 15
/** Stop tracking a task after this long; the install itself keeps running. */
const TASK_TRACKING_TIMEOUT_MS = 10 * 60 * 1000

export interface MarketUpdateCandidate {
  /** Local plugin.toml id — what the backend lock matches on. */
  pluginId: string
  /** Numeric Market plugin id — what `/plugins/latest-versions` and install use. */
  marketId: string
  name: string
  channel: 'stable' | 'beta'
  currentVersion: string
  latestVersion: string
  status: 'idle' | 'updating' | 'failed'
  /** i18n key, already resolved through `resolvePluginInstallErrorKey`. */
  errorKey: string | null
  /** This one needs the Market page (builtin override / ownership confirmation). */
  needsManualUpgrade: boolean
}

interface MarketUpdateTarget {
  pluginId: string
  marketId: string
  name: string
  channel: 'stable' | 'beta'
  currentVersion: string
}

interface MarketInstallTask {
  status?: string
  stage?: string
  progress?: number
  error?: string | null
  error_code?: string | null
}

interface PollOutcome {
  ok: boolean
  errorKey?: string
}

/**
 * Backend rejections that mean "the float window cannot do this one safely".
 * All of them are recoverable from the Market page, where the user gets the
 * bound confirmation flow (builtin override / manual takeover).
 */
const MANUAL_UPGRADE_CODES = new Set([
  'override_confirmation_required',
  'override_confirmation_changed',
  'override_target_exists',
  'manual_takeover_confirmation_required',
  'manual_takeover_confirmation_not_applicable',
  'manual_takeover_plan_changed',
  'manual_takeover_source_changed',
  'plugin_replacement_source_unsupported',
  'plugin_not_installed_for_upgrade',
  'plugin_install_blocked',
  'plugin_builtin_override_market_required',
  'plugin_builtin_override_blocked',
])

const updateLog = {
  info: (...args: unknown[]) => console.info(LOG_PREFIX, ...args),
  warn: (...args: unknown[]) => console.warn(LOG_PREFIX, ...args),
  error: (...args: unknown[]) => console.error(LOG_PREFIX, ...args),
}

/** Only consulted when sessionStorage itself is unavailable (privacy mode). */
let popupShownFallback = false

function isPopupAlreadyShown(): boolean {
  try {
    return sessionStorage.getItem(POPUP_FLAG_KEY) === '1'
  } catch {
    return popupShownFallback
  }
}

function markPopupShown(): void {
  try {
    sessionStorage.setItem(POPUP_FLAG_KEY, '1')
  } catch {
    popupShownFallback = true
  }
}

function readErrorCode(body: unknown): string {
  const record = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  const detail = record.detail && typeof record.detail === 'object'
    ? record.detail as Record<string, unknown>
    : null
  return String(detail?.code || detail?.error_code || record.code || record.error_code || '')
}

/**
 * Only Market-installed plugins are candidates.
 *
 * `manual` / `imported` plugins are rejected by the backend replacement
 * transaction, and a bare `builtin` has no Market identity at all, so neither
 * belongs in a list whose only action is "upgrade from Market".
 */
export function collectMarketUpdateTargets(plugins: readonly PluginMeta[]): MarketUpdateTarget[] {
  const byPluginId = new Map<string, MarketUpdateTarget>()
  for (const plugin of plugins) {
    const source = plugin.install_source
    if (source?.source !== 'market') continue
    const detail = source.source_detail as PluginInstallSourceDetailMarket | null | undefined
    const marketId = String(detail?.plugin_market_id || '').trim()
    // The compact latest-version endpoint only accepts numeric Market ids.
    if (!/^\d+$/.test(marketId)) continue
    const pluginId = String(plugin.id || '').trim()
    if (!pluginId || byPluginId.has(pluginId)) continue
    const channel = narrowMarketChannel(detail?.channel)
    byPluginId.set(pluginId, {
      pluginId,
      marketId,
      name: String(plugin.name || pluginId),
      channel: channel === 'beta' ? 'beta' : 'stable',
      currentVersion: String(detail?.version || plugin.version || ''),
    })
  }
  return [...byPluginId.values()]
}

export const usePluginUpdatesStore = defineStore('pluginUpdates', () => {
  const candidates = ref<MarketUpdateCandidate[]>([])
  const checking = ref(false)
  const checkFailed = ref(false)
  const unresolved = ref(0)
  const popupOpen = ref(false)
  const batchRunning = ref(false)
  const batchDone = ref(0)
  const batchTotal = ref(0)

  const updating = computed(
    () => candidates.value.some((candidate) => candidate.status === 'updating'),
  )
  /** True while anything is in flight: a check, a single upgrade, or a batch. */
  const busy = computed(() => checking.value || batchRunning.value || updating.value)

  // ─── bridge access (same-origin, token-authenticated write path) ──────────

  const bridgeToken = ref('')
  let inflightToken: Promise<string> | null = null

  function readStoredToken(): string {
    try {
      return localStorage.getItem(TOKEN_STORAGE_KEY) || ''
    } catch {
      return ''
    }
  }

  function storeToken(token: string): void {
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, token)
    } catch {
      // Non-fatal: the in-memory token still works for this session.
    }
  }

  async function ensureBridgeToken(forceRefresh = false): Promise<string> {
    if (forceRefresh) {
      bridgeToken.value = ''
      try {
        localStorage.removeItem(TOKEN_STORAGE_KEY)
      } catch {
        // ignored
      }
    }
    if (bridgeToken.value) return bridgeToken.value
    if (inflightToken) return inflightToken

    inflightToken = (async () => {
      try {
        const res = await fetch('/market/bridge-token')
        if (res.ok) {
          const data = await res.json().catch(() => null)
          const token = String(data?.bridge_token || '')
          if (token) {
            bridgeToken.value = token
            storeToken(token)
          }
        }
      } catch (err) {
        updateLog.warn('bridge token request failed', err)
      }
      if (!bridgeToken.value) bridgeToken.value = readStoredToken()
      return bridgeToken.value
    })()

    try {
      return await inflightToken
    } finally {
      inflightToken = null
    }
  }

  async function fetchBridge(path: string, init?: RequestInit): Promise<Response | null> {
    const token = await ensureBridgeToken()
    if (!token) {
      updateLog.warn('bridge request skipped: no token', path)
      return null
    }
    const separator = path.includes('?') ? '&' : '?'
    let res: Response
    try {
      res = await fetch(`${path}${separator}token=${encodeURIComponent(token)}`, init)
    } catch (err) {
      updateLog.warn('bridge request failed', path, err)
      return null
    }
    if (res.status !== 403) return res

    const freshToken = await ensureBridgeToken(true)
    if (!freshToken) return res
    try {
      return await fetch(`${path}${separator}token=${encodeURIComponent(freshToken)}`, init)
    } catch (err) {
      updateLog.warn('bridge retry failed', path, err)
      return null
    }
  }

  // ─── check ───────────────────────────────────────────────────────────────

  async function check(options: { force?: boolean } = {}): Promise<void> {
    if (checking.value) return
    // Re-deriving the list mid-upgrade would replace the very object
    // `updateOne` is mutating, so its failure state would be written to an
    // orphan and never reach the UI.
    if (updating.value) {
      updateLog.info('check skipped: an upgrade is in flight')
      return
    }
    checking.value = true
    const pluginStore = usePluginStore()
    const marketVersions = useMarketVersionsStore()

    try {
      if (pluginStore.plugins.length === 0) await pluginStore.fetchPlugins()
      if (pluginStore.plugins.length === 0) {
        checkFailed.value = !!pluginStore.error
        updateLog.warn('check skipped: plugin list unavailable', pluginStore.error)
        return
      }

      const targets = collectMarketUpdateTargets(pluginStore.pluginsWithStatus)
      updateLog.info('check start', {
        force: options.force === true,
        targets: targets.map((t) => `${t.pluginId}@${t.currentVersion}/${t.channel}`),
      })

      if (targets.length === 0) {
        candidates.value = []
        unresolved.value = 0
        checkFailed.value = false
        return
      }

      await marketVersions.ensureFresh(
        targets.map((target) => ({ pluginId: target.marketId, channel: target.channel })),
        { force: options.force },
      )

      if (marketVersions.loadError) {
        // Keep the previous snapshot — a failed lookup must not be reported as
        // "everything is up to date".
        checkFailed.value = true
        updateLog.warn('check incomplete; keeping previous snapshot', marketVersions.loadError)
        return
      }

      const next: MarketUpdateCandidate[] = []
      let unresolvedCount = 0
      // Carry a previous failure / manual verdict forward. Re-checking after a
      // failed upgrade must not silently clear the reason it failed, and a
      // builtin override must not look auto-upgradable again on the next boot.
      const previous = new Map(candidates.value.map((entry) => [entry.pluginId, entry]))
      for (const target of targets) {
        const latest = marketVersions.latest(target.marketId, target.channel)
        if (!latest) {
          unresolvedCount += 1
          updateLog.warn('no latest version reported', {
            pluginId: target.pluginId,
            marketId: target.marketId,
            channel: target.channel,
          })
          continue
        }
        if (!hasNewerVersion(target.currentVersion, latest)) continue
        updateLog.info('update available', {
          pluginId: target.pluginId,
          marketId: target.marketId,
          channel: target.channel,
          current: target.currentVersion,
          latest,
        })
        const prior = previous.get(target.pluginId)
        next.push({
          ...target,
          latestVersion: latest,
          status: prior?.status === 'failed' ? 'failed' : 'idle',
          errorKey: prior?.status === 'failed' ? prior.errorKey : null,
          needsManualUpgrade: prior?.needsManualUpgrade === true,
        })
      }

      candidates.value = next
      unresolved.value = unresolvedCount
      checkFailed.value = false
      updateLog.info('check done', { updates: next.length, unresolved: unresolvedCount })
    } catch (err) {
      checkFailed.value = true
      updateLog.error('check failed', err)
    } finally {
      checking.value = false
    }
  }

  // ─── upgrade ─────────────────────────────────────────────────────────────

  function failCandidate(
    candidate: MarketUpdateCandidate,
    errorKey: string,
    detail: unknown,
  ): false {
    candidate.status = 'failed'
    candidate.errorKey = errorKey
    updateLog.error('upgrade failed', {
      pluginId: candidate.pluginId,
      marketId: candidate.marketId,
      errorKey,
      detail,
    })
    return false
  }

  async function pollTask(taskId: string): Promise<PollOutcome> {
    const deadline = Date.now() + TASK_TRACKING_TIMEOUT_MS
    let consecutiveMissing = 0

    for (;;) {
      if (Date.now() > deadline) {
        updateLog.warn('task tracking timed out', { taskId })
        return { ok: false, errorKey: 'market.installTakingLonger' }
      }

      const res = await fetchBridge(`/market/tasks/${taskId}`)
      if (!res) return { ok: false, errorKey: 'market.pairRequired' }
      // A rejected token will not fix itself; bail out instead of polling a
      // dead endpoint until the tracking deadline.
      if (res.status === 401 || res.status === 403) {
        updateLog.warn('task poll rejected', { taskId, status: res.status })
        return { ok: false, errorKey: 'market.pairRequired' }
      }

      if (res.status === 404) {
        consecutiveMissing += 1
        if (consecutiveMissing >= TASK_MISSING_TOLERANCE) {
          updateLog.warn('task disappeared before reaching a terminal state', { taskId })
          return { ok: false, errorKey: 'market.installTaskLost' }
        }
      } else if (res.ok) {
        consecutiveMissing = 0
        const task = await res.json().catch(() => null) as MarketInstallTask | null
        if (task?.status === 'completed') return { ok: true }
        if (task?.status === 'failed') {
          updateLog.warn('task failed', {
            taskId,
            errorCode: task.error_code,
            error: task.error,
          })
          return { ok: false, errorKey: resolvePluginInstallErrorKey(task.error_code) }
        }
        if (task?.status === 'canceled') {
          updateLog.warn('task canceled', { taskId })
          return { ok: false, errorKey: 'market.installCancelled' }
        }
      }

      await new Promise((resolve) => setTimeout(resolve, TASK_POLL_INTERVAL_MS))
    }
  }

  async function finishCandidate(candidate: MarketUpdateCandidate): Promise<void> {
    updateLog.info('upgrade succeeded', {
      pluginId: candidate.pluginId,
      marketId: candidate.marketId,
      version: candidate.latestVersion,
    })
    candidates.value = candidates.value.filter((entry) => entry.pluginId !== candidate.pluginId)
    await usePluginStore().syncRegistryAndFetch().catch((err: unknown) => {
      updateLog.warn('registry sync failed after upgrade', err)
    })
  }

  async function updateOne(pluginId: string): Promise<boolean> {
    const candidate = candidates.value.find((entry) => entry.pluginId === pluginId)
    if (!candidate || candidate.needsManualUpgrade) return false
    if (candidate.status === 'updating') return false

    candidate.status = 'updating'
    candidate.errorKey = null
    updateLog.info('upgrade start', {
      pluginId: candidate.pluginId,
      marketId: candidate.marketId,
      from: candidate.currentVersion,
      to: candidate.latestVersion,
    })

    try {
      // The catalog row carries the target release's package evidence; the
      // lock only knows what is currently on disk.
      const release = await fetchMarketPlugin(candidate.marketId)
      if (!release) {
        return failCandidate(candidate, 'market.marketListFetchFailed', 'release lookup failed')
      }
      const packageUrl = release.download_url
      const packageSha256 = release.latest_package_sha256
      if (!packageUrl || !packageSha256) {
        return failCandidate(candidate, 'market.installFailed', 'release has no package_url/sha256')
      }

      const res = await fetchBridge('/market/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          package_url: packageUrl,
          canonical_package_url: packageUrl,
          package_sha256: packageSha256,
          payload_hash: release.latest_payload_hash ?? null,
          plugin_id: candidate.marketId,
          version: release.version || candidate.latestVersion,
          channel: release.latest_channel || candidate.channel,
          published_at: release.latest_published_at || null,
          // Matches the active lock entry: the backend looks up by plugin.toml
          // id first and only falls back to the Market id.
          expected_plugin_toml_id: candidate.pluginId,
          mode: 'upgrade',
          on_conflict: 'fail',
        }),
      })
      if (!res) {
        return failCandidate(candidate, 'market.pairRequired', 'bridge unavailable')
      }
      if (res.status === 403) {
        return failCandidate(candidate, 'market.pairRequired', 'bridge token rejected')
      }

      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const code = readErrorCode(body)
        if (MANUAL_UPGRADE_CODES.has(code.toLowerCase())) {
          updateLog.warn('upgrade needs the Market page', {
            pluginId: candidate.pluginId,
            status: res.status,
            code,
          })
          candidate.status = 'idle'
          candidate.errorKey = null
          candidate.needsManualUpgrade = true
          return false
        }
        return failCandidate(
          candidate,
          resolvePluginInstallErrorKey(code),
          `install rejected status=${res.status} code=${code || 'unknown'}`,
        )
      }

      const body = await res.json().catch(() => null) as { task_id?: string } | null
      if (!body?.task_id) {
        await finishCandidate(candidate)
        return true
      }

      const outcome = await pollTask(String(body.task_id))
      if (!outcome.ok) {
        return failCandidate(candidate, outcome.errorKey || 'market.installFailed', 'task not ok')
      }

      await finishCandidate(candidate)
      return true
    } catch (err) {
      return failCandidate(candidate, 'market.installFailed', err)
    }
  }

  /** Serial by construction: concurrent upgrades would fight over the same
   *  plugin directories and the backend's replacement transaction. */
  async function updateAll(): Promise<void> {
    if (batchRunning.value) return
    const queue = candidates.value
      .filter((candidate) => !candidate.needsManualUpgrade && candidate.status !== 'updating')
      .map((candidate) => candidate.pluginId)
    if (queue.length === 0) return

    batchRunning.value = true
    batchTotal.value = queue.length
    batchDone.value = 0
    updateLog.info('batch start', { count: queue.length })

    try {
      for (const pluginId of queue) {
        // Earlier iterations may have removed this candidate (success) or sent
        // it down the manual path; re-read instead of trusting the queue.
        const current = candidates.value.find((entry) => entry.pluginId === pluginId)
        if (current && !current.needsManualUpgrade) await updateOne(pluginId)
        batchDone.value += 1
      }
      updateLog.info('batch finished', { done: batchDone.value, total: batchTotal.value })
      // Reconcile once at the end; per-item registry syncs already happened.
      // Kept inside the batch flag so a click cannot start a second round while
      // the list is being rebuilt.
      await check({ force: true })
    } finally {
      batchRunning.value = false
    }
  }

  // ─── popup plumbing ──────────────────────────────────────────────────────

  function closePopup(): void {
    popupOpen.value = false
  }

  /** Toolbar button: always gives feedback, even when there is nothing to show. */
  async function openFromButton(): Promise<void> {
    popupOpen.value = true
    if (busy.value) return
    await check({ force: true })
  }

  /**
   * Once per panel window. The dashboard window is opened with
   * `window.open(..., 'neko_plugin_dashboard')`, so every open is a fresh
   * browsing context with an empty sessionStorage — while an in-window reload
   * keeps the flag and does not pop again.
   */
  async function checkOnBoot(): Promise<void> {
    if (isPopupAlreadyShown()) {
      updateLog.info('boot check skipped: already shown in this window')
      return
    }
    markPopupShown()
    await check()
    if (candidates.value.length > 0) {
      popupOpen.value = true
      updateLog.info('boot check opened the popup', { count: candidates.value.length })
    } else {
      updateLog.info('boot check stays hidden', {
        unresolved: unresolved.value,
        failed: checkFailed.value,
      })
    }
  }

  return {
    candidates,
    checking,
    checkFailed,
    unresolved,
    popupOpen,
    batchRunning,
    batchDone,
    batchTotal,
    busy,
    check,
    updateOne,
    updateAll,
    closePopup,
    openFromButton,
    checkOnBoot,
  }
})
