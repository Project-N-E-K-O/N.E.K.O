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
