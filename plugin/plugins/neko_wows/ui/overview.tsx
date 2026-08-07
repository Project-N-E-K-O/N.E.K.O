import {
  Alert,
  Card,
  Columns,
  Divider,
  Inline,
  KeyValue,
  StatCard,
  StatusBadge,
  Stack,
  Text,
} from "@neko/plugin-ui"

import {
  availabilityLabel,
  availabilityTone,
  formatClock,
  formatCount,
  formatMetres,
  formatPercent,
  serviceModeLabel,
  serviceModeTone,
  sourceStatusLabel,
  sourceStatusTone,
} from "./format"
import type { DashboardState, Translate } from "./types"

export function OverviewSection(props: {
  state: DashboardState
  t: Translate
  locale: string
}) {
  const { t } = props
  const state = props.state || {}
  const service = state.service || {}
  const transport = state.transport || {}
  const snapshot = state.snapshot || {}
  const cursor = state.cursor || {}
  const config = state.config || {}
  const counters = state.counters || {}
  const hasSnapshot = snapshot.seq !== undefined

  return (
    <Stack gap={12}>
      {state.mod_hint ? (
        <Alert tone={service.mode === "conflict" ? "danger" : "warning"}>
          {t(`overview.hint.${state.mod_hint}`)}
        </Alert>
      ) : null}

      {state.reconnect_required ? (
        <Alert tone="warning">
          {t("overview.reconnectRequired")}
        </Alert>
      ) : null}

      <Columns minWidth={180} gap={12}>
        <StatCard
          label={t("overview.stats.service")}
          value={serviceModeLabel(service.mode, t)}
        />
        <StatCard
          label={t("overview.stats.transport")}
          value={transport.mode || t("common.notStarted")}
        />
        <StatCard
          label={t("overview.stats.battle")}
          value={sourceStatusLabel(snapshot.status, t)}
        />
        <StatCard
          label={t("overview.stats.output")}
          value={config.dry_run
            ? t("overview.output.dryRun")
            : t("overview.output.live")}
        />
      </Columns>

      <Card title={t("overview.source.title")}>
        <Stack gap={8}>
          <Inline gap={8} wrap>
            <StatusBadge
              tone={serviceModeTone(service.mode)}
              label={serviceModeLabel(service.mode, t)}
            />
            {service.paused ? (
              <StatusBadge tone="danger" label={t("overview.source.autoPaused")} />
            ) : null}
            {snapshot.legacy ? (
              <StatusBadge tone="warning" label={t("overview.source.legacy")} />
            ) : null}
          </Inline>
          <KeyValue
            data={{
              [t("overview.source.address")]: config.service_url || "—",
              serviceId: service.service_id || "—",
              apiVersion: service.api_version || "—",
              instanceId: service.instance_id || "—",
              [t("overview.source.process")]: service.pid
                ? `pid ${service.pid}`
                : t("overview.source.externalProcess"),
              [t("overview.source.detail")]: service.detail || "—",
              [t("overview.source.error")]: service.error || t("common.none"),
              [t("overview.source.sourceDir")]: config.service_source_dir
                || t("overview.source.sourceDirMissing"),
              [t("overview.source.gameDir")]: config.game_dir
                || t("common.notConfigured"),
            }}
          />
        </Stack>
      </Card>

      <Card title={t("overview.transport.title")}>
        <Stack gap={8}>
          <KeyValue
            data={{
              [t("overview.transport.mode")]: transport.mode || t("common.notStarted"),
              [t("overview.transport.epoch")]: `epoch ${transport.epoch ?? 0}`,
              [t("overview.transport.wsConnects")]: formatCount(transport.ws_connects, t),
              [t("overview.transport.wsFailures")]: formatCount(transport.ws_failures, t),
              [t("overview.transport.restPolls")]: formatCount(transport.rest_polls, t),
              [t("overview.transport.restFailures")]: formatCount(transport.rest_failures, t),
              [t("overview.transport.reconnectDelay")]: `${transport.reconnect_delay ?? 0}s`,
              [t("overview.transport.lastFrame")]: formatClock(
                transport.last_frame_at, props.locale),
              [t("overview.transport.lastError")]: transport.last_error || t("common.none"),
            }}
          />
          <Divider />
          <KeyValue
            data={{
              [t("overview.cursor.acceptedCursor")]: `${cursor.instance_id || "—"} / seq ${cursor.seq ?? -1}`,
              [t("overview.cursor.acceptedFrames")]: formatCount(cursor.accepted, t),
              [t("overview.cursor.droppedFrames")]: describeDropped(cursor.dropped),
              [t("overview.cursor.totalFrames")]: formatCount(counters.frames, t),
              [t("overview.cursor.totalEvents")]: formatCount(counters.events, t),
            }}
          />
          <Text>{t("overview.cursor.help")}</Text>
        </Stack>
      </Card>

      {hasSnapshot ? (
        <Card title={t("overview.battle.title")}>
          <Stack gap={8}>
            <Inline gap={8} wrap>
              <StatusBadge
                tone={sourceStatusTone(snapshot.status)}
                label={sourceStatusLabel(snapshot.status, t)}
              />
              {snapshot.transport ? (
                <StatusBadge
                  tone="info"
                  label={t("overview.battle.from", { source: snapshot.transport })}
                />
              ) : null}
            </Inline>
            <KeyValue
              data={{
                battleId: snapshot.battle_id || "—",
                seq: formatCount(snapshot.seq, t),
                [t("overview.battle.map")]: snapshot.map_name || t("common.unknown"),
                [t("overview.battle.mode")]: snapshot.game_mode
                  || snapshot.battle_type || t("common.unknown"),
                [t("overview.battle.health")]: formatPercent(snapshot.own_hp_ratio, t),
                [t("overview.battle.allies")]: formatCount(snapshot.allies_alive, t),
                [t("overview.battle.enemies")]: formatCount(snapshot.enemies_alive, t),
                [t("overview.battle.nearest")]: formatMetres(snapshot.nearest_enemy_m, t),
              }}
            />
            <Divider />
            <Text>{t("overview.battle.availability")}</Text>
            <Inline gap={6} wrap>
              {Object.keys(snapshot.availability || {})
                .sort()
                .map((domain) => (
                  <StatusBadge
                    key={domain}
                    tone={availabilityTone((snapshot.availability || {})[domain])}
                    label={`${domain}: ${availabilityLabel(
                      (snapshot.availability || {})[domain], t
                    )}`}
                  />
                ))}
            </Inline>
            <Text>{t("overview.battle.availabilityHelp")}</Text>
          </Stack>
        </Card>
      ) : null}
    </Stack>
  )
}

function describeDropped(dropped?: Record<string, number>): string {
  const entries = Object.entries(dropped || {})
  if (!entries.length) return "0"
  return entries.map(([reason, count]) => `${reason} ${count}`).join(" / ")
}
