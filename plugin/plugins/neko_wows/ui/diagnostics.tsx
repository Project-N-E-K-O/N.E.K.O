import {
  ActionButton,
  Alert,
  Card,
  Columns,
  Field,
  Inline,
  KeyValue,
  NumberInput,
  PasswordInput,
  SegmentedControl,
  Stack,
  StatCard,
  StatusBadge,
  Switch,
  Text,
  Warning,
  useEffect,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction } from "@neko/plugin-ui"

import { formatClock, formatCount } from "./format"
import type { DashboardState, Translate } from "./types"

export function DiagnosticsSection(props: {
  state: DashboardState
  actions: HostedAction[]
  onSetDryRun: (value: boolean) => void
  onSetScreenshotEnabled: (value: boolean) => void
  busy: boolean
  t: Translate
  locale?: string
}) {
  const { t } = props
  const state = props.state || {}
  const config = state.config || {}
  const dispatcher = state.dispatcher || {}
  const arbiter = state.arbiter || {}
  const catalog = state.ship_catalog || {}
  const officialTool = catalog.official_tool || {}
  const screenshot = state.screenshot || {}
  const recentShots = screenshot.recent || []
  const paused = Boolean(dispatcher.paused || arbiter.paused)
  const findAction = (id: string) =>
    (props.actions || []).find((action) => action.id === id)

  const [officialEnabled, setOfficialEnabled] = useState(
    Boolean(officialTool.enabled)
  )
  const [officialRegion, setOfficialRegion] = useState(
    officialTool.region || "asia"
  )
  const [applicationIdDraft, setApplicationIdDraft] = useState("")
  const [shotInterval, setShotInterval] = useState<number | "">(
    screenshot.min_interval_seconds ?? 15
  )
  const [shotRetain, setShotRetain] = useState<number | "">(
    screenshot.retain_count ?? 20
  )

  useEffect(() => {
    setOfficialEnabled(Boolean(officialTool.enabled))
  }, [officialTool.enabled])

  useEffect(() => {
    setOfficialRegion(officialTool.region || "asia")
  }, [officialTool.region])

  useEffect(() => {
    setShotInterval(screenshot.min_interval_seconds ?? 15)
  }, [screenshot.min_interval_seconds])

  useEffect(() => {
    setShotRetain(screenshot.retain_count ?? 20)
  }, [screenshot.retain_count])

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
          <Stack gap={10}>
            <Text>{t("diagnostics.catalog.officialHelp")}</Text>
            <Switch
              checked={officialEnabled}
              disabled={props.busy}
              label={t("diagnostics.catalog.officialEnable")}
              onChange={(checked) => setOfficialEnabled(checked)}
            />
            <Field label={t("diagnostics.catalog.region")}>
              <SegmentedControl
                value={officialRegion}
                disabled={props.busy}
                options={[
                  { value: "asia", label: t("diagnostics.catalog.region.asia") },
                  { value: "eu", label: t("diagnostics.catalog.region.eu") },
                  { value: "na", label: t("diagnostics.catalog.region.na") },
                ]}
                onChange={(value) => setOfficialRegion(String(value))}
              />
            </Field>
            <Field
              label={t("diagnostics.catalog.key")}
              help={
                officialTool.key_configured
                  ? t("diagnostics.catalog.keyHelpConfigured")
                  : t("diagnostics.catalog.keyHelpEmpty")
              }
            >
              <PasswordInput
                value={applicationIdDraft}
                disabled={props.busy}
                placeholder={
                  officialTool.key_configured
                    ? t("diagnostics.catalog.keyPlaceholderConfigured")
                    : t("diagnostics.catalog.keyPlaceholderEmpty")
                }
                onChange={(value) => setApplicationIdDraft(value)}
              />
            </Field>
            <Inline gap={8} wrap>
              <ActionButton
                tone="primary"
                refresh
                actionId="set_official_api"
                values={{
                  enabled: officialEnabled,
                  region: officialRegion,
                  ...(applicationIdDraft.trim()
                    ? { application_id: applicationIdDraft.trim() }
                    : {}),
                }}
                onResult={() => setApplicationIdDraft("")}
              >
                {t("diagnostics.catalog.saveOfficial")}
              </ActionButton>
              {officialTool.key_configured ? (
                <ActionButton
                  tone="danger"
                  refresh
                  actionId="set_official_api"
                  values={{
                    enabled: officialEnabled,
                    region: officialRegion,
                    clear_application_id: true,
                  }}
                  onResult={() => setApplicationIdDraft("")}
                >
                  {t("diagnostics.catalog.clearKey")}
                </ActionButton>
              ) : null}
            </Inline>
          </Stack>
        </Stack>
      </Card>

      <Card title={t("diagnostics.screenshot.title")}>
        <Stack gap={10}>
          <Switch
            checked={Boolean(screenshot.enabled)}
            disabled={props.busy}
            label={t("diagnostics.screenshot.enable")}
            onChange={(checked) => props.onSetScreenshotEnabled(checked)}
          />
          {screenshot.enabled ? (
            <Warning>{t("diagnostics.screenshot.privacyWarning")}</Warning>
          ) : (
            <Text>{t("diagnostics.screenshot.disabledHelp")}</Text>
          )}
          <Inline gap={8} wrap align="end">
            <Field label={t("diagnostics.screenshot.interval")}>
              <NumberInput
                value={shotInterval}
                min={0}
                max={600}
                step={1}
                disabled={props.busy}
                onChange={(value) =>
                  setShotInterval(value === "" ? "" : Number(value))
                }
              />
            </Field>
            <Field label={t("diagnostics.screenshot.retain")}>
              <NumberInput
                value={shotRetain}
                min={1}
                max={100}
                step={1}
                disabled={props.busy}
                onChange={(value) =>
                  setShotRetain(value === "" ? "" : Number(value))
                }
              />
            </Field>
            <ActionButton
              tone="primary"
              refresh
              actionId="set_screenshot_settings"
              values={{
                min_interval_seconds: Number(shotInterval) || 0,
                retain_count: Number(shotRetain) || 1,
              }}
            >
              {t("diagnostics.screenshot.saveSettings")}
            </ActionButton>
          </Inline>
          <Text>{t("diagnostics.screenshot.settingsHelp")}</Text>
          {screenshot.enabled ? (
            <>
              <KeyValue
                data={{
                  [t("diagnostics.screenshot.cooldown")]:
                    `${screenshot.cooldown_remaining_seconds ?? 0}s`,
                }}
              />
              {findAction("capture_screenshot_now") ? (
                <Inline gap={8} wrap>
                  <ActionButton
                    action={findAction("capture_screenshot_now")}
                    tone="info"
                    refresh
                  >
                    {t("diagnostics.screenshot.captureNow")}
                  </ActionButton>
                </Inline>
              ) : null}
              {recentShots.length ? (
                <KeyValue
                  data={Object.fromEntries(
                    recentShots.map((shot) => [
                      shot.shot_id || "—",
                      `${formatClock(shot.captured_at, props.locale)} · ${Math.round(
                        (shot.size_bytes || 0) / 1024
                      )} KB`,
                    ])
                  )}
                />
              ) : (
                <Text>{t("diagnostics.screenshot.empty")}</Text>
              )}
            </>
          ) : null}
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
