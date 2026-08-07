import {
  ActionButton,
  Alert,
  Card,
  Columns,
  Inline,
  KeyValue,
  Stack,
  StatCard,
  StatusBadge,
  Switch,
  Text,
  Warning,
} from "@neko/plugin-ui"
import type { HostedAction } from "@neko/plugin-ui"

import { formatCount } from "./format"
import type { DashboardState, Translate } from "./types"

export function DiagnosticsSection(props: {
  state: DashboardState
  actions: HostedAction[]
  onSetDryRun: (value: boolean) => void
  busy: boolean
  t: Translate
}) {
  const { t } = props
  const state = props.state || {}
  const config = state.config || {}
  const dispatcher = state.dispatcher || {}
  const arbiter = state.arbiter || {}
  const catalog = state.ship_catalog || {}
  const officialTool = catalog.official_tool || {}
  const paused = Boolean(dispatcher.paused || arbiter.paused)
  const findAction = (id: string) =>
    (props.actions || []).find((action) => action.id === id)

  return (
    <Stack gap={12}>
      {paused ? (
        <Alert tone="danger">
          {dispatcher.pause_reason
            ? t("diagnostics.pausedWithReason", { reason: dispatcher.pause_reason })
            : t("diagnostics.paused")}
        </Alert>
      ) : null}

      <Card title={t("diagnostics.broadcast.title")}>
        <Stack gap={10}>
          <Switch
            checked={!config.dry_run}
            disabled={props.busy}
            label={t("diagnostics.broadcast.enable")}
            onChange={(checked) => props.onSetDryRun(!checked)}
          />
          {config.dry_run ? (
            <Text>{t("diagnostics.broadcast.dryRunHelp")}</Text>
          ) : (
            <Warning>{t("diagnostics.broadcast.liveWarning")}</Warning>
          )}
          <Text>{t("diagnostics.broadcast.preferencesHelp")}</Text>
        </Stack>
      </Card>

      <Card title={t("diagnostics.counts.title")}>
        <Stack gap={8}>
          <Columns minWidth={150} gap={10}>
            <StatCard label={t("diagnostics.counts.hostCalls")} value={formatCount(dispatcher.host_calls, t)} />
            <StatCard label={t("diagnostics.counts.delivered")} value={formatCount(dispatcher.delivered, t)} />
            <StatCard label={t("diagnostics.counts.suppressed")} value={formatCount(dispatcher.suppressed, t)} />
            <StatCard label={t("diagnostics.counts.queued")} value={formatCount(arbiter.queued, t)} />
          </Columns>
          <KeyValue
            data={{
              [t("diagnostics.counts.failures")]: `${formatCount(dispatcher.recent_failures, t)} / ${formatCount(
                dispatcher.failure_limit, t
              )}`,
              [t("diagnostics.counts.cooldowns")]: formatCount(arbiter.cooldowns, t),
              [t("diagnostics.counts.once")]: (arbiter.fired_once_per_battle || []).join(", ") || t("common.none"),
              [t("diagnostics.counts.context")]: state.context_injected
                ? t("diagnostics.context.injected")
                : t("diagnostics.context.notInjected"),
            }}
          />
          {config.dry_run && (dispatcher.host_calls || 0) > 0 ? (
            <Alert tone="danger">
              {t("diagnostics.counts.dryRunViolation")}
            </Alert>
          ) : null}
        </Stack>
      </Card>

      <Card title={t("diagnostics.throttle.title")}>
        <KeyValue
          data={{
            [t("diagnostics.throttle.urgentTtl")]: `${config.urgent_ttl_seconds ?? "—"}s`,
            [t("diagnostics.throttle.urgentGap")]: `${config.urgent_min_gap_seconds ?? "—"}s`,
            [t("diagnostics.throttle.normalTtl")]: `${config.normal_ttl_seconds ?? "—"}s`,
            [t("diagnostics.throttle.normalGap")]: `${config.normal_min_gap_seconds ?? "—"}s`,
          }}
        />
      </Card>

      <Card title={t("diagnostics.catalog.title")}>
        <Stack gap={8}>
          <Columns minWidth={140} gap={10}>
            <StatCard
              label={t("diagnostics.catalog.resolved")}
              value={formatCount(catalog.resolved_ship_types, t)}
            />
            <StatCard
              label={t("diagnostics.catalog.unresolved")}
              value={formatCount(catalog.unresolved_objects, t)}
            />
            <StatCard
              label={t("diagnostics.catalog.pending")}
              value={formatCount(catalog.pending_ship_types, t)}
            />
            <StatCard
              label={t("diagnostics.catalog.submitted")}
              value={formatCount(catalog.submitted_ship_types, t)}
            />
          </Columns>
          <KeyValue
            data={{
              [t("diagnostics.catalog.state")]: catalogStateLabel(catalog.state, t),
              [t("diagnostics.catalog.activeVersion")]: catalog.active_catalog_version || "—",
              [t("diagnostics.catalog.frozenVersion")]: catalog.frozen_catalog_version || "—",
              [t("diagnostics.catalog.gameVersion")]: catalog.catalog_game_version || "—",
              [t("diagnostics.catalog.clientVersion")]: catalog.client_game_version || "—",
              [t("diagnostics.catalog.versionStatus")]: catalogVersionLabel(catalog.version_status, t),
              [t("diagnostics.catalog.sourceCommit")]: catalog.source_commit
                ? catalog.source_commit.slice(0, 12)
                : "—",
              [t("diagnostics.catalog.official")]: officialTool.enabled
                ? t("diagnostics.catalog.enabled")
                : t("diagnostics.catalog.disabled"),
              [t("diagnostics.catalog.region")]: officialTool.region || "—",
              [t("diagnostics.catalog.key")]: officialTool.key_configured
                ? t("diagnostics.catalog.configured")
                : t("diagnostics.catalog.notConfigured"),
              [t("diagnostics.catalog.cacheEntries")]: formatCount(officialTool.cache_entries, t),
              [t("diagnostics.catalog.cacheHits")]: formatCount(officialTool.cache_hits, t),
              [t("diagnostics.catalog.cacheMisses")]: formatCount(officialTool.cache_misses, t),
            }}
          />
        </Stack>
      </Card>

      <Card title={t("diagnostics.actions.title")}>
        <Stack gap={8}>
          <Inline gap={8} wrap>
            {paused && findAction("resume") ? (
              <ActionButton action={findAction("resume")} tone="success" refresh>
                {t("diagnostics.actions.resume")}
              </ActionButton>
            ) : null}
            {!paused && findAction("pause") ? (
              <ActionButton action={findAction("pause")} tone="danger" refresh>
                {t("diagnostics.actions.pause")}
              </ActionButton>
            ) : null}
            {findAction("reconnect") ? (
              <ActionButton action={findAction("reconnect")} tone="primary" refresh>
                {t("diagnostics.actions.reconnect")}
              </ActionButton>
            ) : null}
            {findAction("clear_timeline") ? (
              <ActionButton action={findAction("clear_timeline")} tone="info" refresh>
                {t("diagnostics.actions.clearTimeline")}
              </ActionButton>
            ) : null}
          </Inline>
          <Inline gap={8} wrap>
            <StatusBadge
              tone={state.running ? "success" : "warning"}
              label={state.running
                ? t("diagnostics.running")
                : t("diagnostics.stopped")}
            />
          </Inline>
        </Stack>
      </Card>
    </Stack>
  )
}

function catalogStateLabel(value: string | undefined, t: Translate): string {
  if (value === "loaded") return t("diagnostics.catalog.state.loaded")
  if (value === "null_catalog") return t("diagnostics.catalog.state.null")
  if (value === "version_rejected") return t("diagnostics.catalog.state.rejected")
  if (value === "disabled") return t("diagnostics.catalog.state.disabled")
  return t("diagnostics.catalog.state.idle")
}

function catalogVersionLabel(value: string | undefined, t: Translate): string {
  if (value === "match") return t("diagnostics.catalog.version.match")
  if (value === "mismatch") return t("diagnostics.catalog.version.mismatch")
  return t("diagnostics.catalog.version.unknown")
}
