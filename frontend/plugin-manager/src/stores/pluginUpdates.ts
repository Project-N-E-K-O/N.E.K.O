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
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { fetchMarketPluginVersions } from '@/api/market'
import { fetchBridge, readErrorCode } from '@/api/marketBridge'
import { isGithubReleaseDownloadUrl, useGithubMirrorSource } from '@/composables/useGithubMirrorSource'
import { useMarketInstallTaskStore } from '@/stores/marketInstallTask'
import { useMarketVersionsStore } from '@/stores/marketVersions'
import { usePluginStore } from '@/stores/plugin'
import { narrowMarketChannel } from '@/utils/narrowChannel'
import { resolvePluginInstallErrorKey } from '@/utils/pluginInstallError'
import { hasNewerVersion } from '@/utils/version'
import type { PluginInstallSourceDetailMarket, PluginMeta } from '@/types/api'

const LOG_PREFIX = '[plugin-updates]'
const POPUP_FLAG_KEY = 'neko_plugin_update_popup_shown'

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
  const mirror = useGithubMirrorSource()
  const candidates = ref<MarketUpdateCandidate[]>([])
  const checking = ref(false)
  const checkFailed = ref(false)
  const unresolved = ref(0)
  const popupOpen = ref(false)
  const batchRunning = ref(false)
  const batchDone = ref(0)
  const batchTotal = ref(0)
  /** Bumped per successful popup upgrade, so the Market page can drop its own
   *  installed-version snapshot instead of offering the same upgrade again. */
  const completedUpgrades = ref(0)

  const updating = computed(
    () => candidates.value.some((candidate) => candidate.status === 'updating'),
  )
  /** True while anything is in flight: a check, a single upgrade, or a batch. */
  const busy = computed(() => checking.value || batchRunning.value || updating.value)

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
        if (pluginStore.error) {
          // Fetch failed: keep the previous snapshot rather than pretending
          // nothing is installed.
          checkFailed.value = true
          updateLog.warn('check skipped: plugin list unavailable', pluginStore.error)
          return
        }
        // A successful fetch with no plugins at all: that is a real no-target
        // result, so the stale candidate list has to go — otherwise rows for
        // uninstalled plugins stay clickable and the bridge rejects them.
        candidates.value = []
        unresolved.value = 0
        checkFailed.value = false
        updateLog.info('check done: no plugins installed')
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
    completedUpgrades.value += 1
  }

  async function updateOne(pluginId: string): Promise<boolean> {
    const candidate = candidates.value.find((entry) => entry.pluginId === pluginId)
    if (!candidate || candidate.needsManualUpgrade) return false
    if (candidate.status === 'updating') return false

    // Symmetric with `check`'s own `updating` guard: a check in flight will
    // rebuild `candidates` after its await, which would orphan the very object
    // this method is about to mutate. The popup already disables its buttons
    // while checking, so this only closes the hole structurally.
    if (checking.value) {
      updateLog.warn('upgrade refused: an update check is in flight', { pluginId })
      return false
    }

    // Claim the shared slot atomically, before the async version lookup and the
    // POST: a plain `running` read is racy because this method awaits in
    // between, so the Market panel could create a second, untracked worker.
    const installTask = useMarketInstallTaskStore()
    if (!installTask.reserve('float')) {
      updateLog.warn('upgrade refused: an install slot is already claimed', { pluginId })
      return false
    }

    candidate.status = 'updating'
    candidate.errorKey = null
    updateLog.info('upgrade start', {
      pluginId: candidate.pluginId,
      marketId: candidate.marketId,
      from: candidate.currentVersion,
      to: candidate.latestVersion,
    })

    try {
      // Fetch the version table for the candidate's *own* channel and pick the
      // exact release the check detected. The plugin-detail endpoint returns
      // whatever the default channel calls "latest", so using it here would
      // silently pull a beta install back onto stable.
      const versions = await fetchMarketPluginVersions(candidate.marketId, {
        channel: candidate.channel,
      })
      const release = versions?.find((entry) => entry.version === candidate.latestVersion)
      if (!release) {
        return failCandidate(
          candidate,
          'market.marketListFetchFailed',
          `release ${candidate.latestVersion} missing on channel ${candidate.channel}`,
        )
      }
      const packageUrl = release.package_url
      const packageSha256 = release.package_sha256
      if (!packageUrl || !packageSha256) {
        return failCandidate(candidate, 'market.installFailed', 'release has no package_url/sha256')
      }

      // Same URL resolution as the Market page: the backend only falls back
      // from a proxy to GitHub direct, never the other way, so submitting the
      // canonical URL fails wherever GitHub itself is unreachable.
      if (isGithubReleaseDownloadUrl(packageUrl)) {
        try {
          await mirror.ensureAutoSource()
        } catch (err) {
          updateLog.warn('mirror measurement failed; using the last known source', { pluginId, err })
        }
      }
      const effectiveUrl = mirror.resolveGithubDownloadUrl(packageUrl)

      const res = await fetchBridge('/market/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          package_url: effectiveUrl,
          canonical_package_url: packageUrl,
          package_sha256: packageSha256,
          payload_hash: release.payload_hash ?? null,
          plugin_id: candidate.marketId,
          version: release.version,
          channel: release.channel || candidate.channel,
          published_at: release.created_at || null,
          // Matches the active lock entry: the backend looks up by plugin.toml
          // id first and only falls back to the Market id.
          expected_plugin_toml_id: candidate.pluginId,
          mode: 'upgrade',
          on_conflict: 'fail',
        }),
      // A rejected fetch lands in the catch below as `installFailed`; only a
      // missing token is left to mean "pairing required".
      }, { throwOnTransportError: true })
      if (!res) {
        return failCandidate(candidate, 'market.pairRequired', 'bridge token unavailable')
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

      const outcome = await installTask.track(String(body.task_id), {
        pluginId: candidate.pluginId,
        name: candidate.name,
        mode: 'upgrade',
        channel: candidate.channel,
        fromVersion: candidate.currentVersion,
        toVersion: candidate.latestVersion,
      }, 'float')
      if (!outcome.ok) {
        if (outcome.refused) {
          updateLog.warn('upgrade refused after POST; leaving the candidate untouched', { pluginId })
          candidate.status = 'idle'
          return false
        }
        if (outcome.aborted || outcome.canceled) {
          // Tracking was dropped (the popup closed mid-upgrade) or the user
          // cancelled from the shared dialog. Neither is a failure — and a
          // `failed` row would otherwise be carried forward by every later
          // check, showing red until the plugin is finally upgraded.
          candidate.status = 'idle'
          candidate.errorKey = null
          return false
        }
        return failCandidate(candidate, outcome.errorKey || 'market.installFailed', 'task not ok')
      }

      await finishCandidate(candidate)
      return true
    } catch (err) {
      return failCandidate(candidate, 'market.installFailed', err)
    } finally {
      installTask.release('float')
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
    completedUpgrades,
    busy,
    check,
    updateOne,
    updateAll,
    closePopup,
    openFromButton,
    checkOnBoot,
  }
})
