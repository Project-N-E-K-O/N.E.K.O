import {
  ActionButton,
  Alert,
  Card,
  Columns,
  Divider,
  Field,
  Inline,
  Input,
  KeyValue,
  StatCard,
  StatusBadge,
  Stack,
  Text,
  useEffect,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction } from "@neko/plugin-ui"

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
  actions: HostedAction[]
  busy: boolean
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
  const findAction = (id: string) =>
    (props.actions || []).find((action) => action.id === id)

  const [serviceUrl, setServiceUrl] = useState(config.service_url || "")
  const [sourceDir, setSourceDir] = useState(config.service_source_dir || "")
  const [gameDir, setGameDir] = useState(config.game_dir || "")

  useEffect(() => {
    setServiceUrl(config.service_url || "")
  }, [config.service_url])

  useEffect(() => {
    setSourceDir(config.service_source_dir || "")
  }, [config.service_source_dir])

  useEffect(() => {
    setGameDir(config.game_dir || "")
  }, [config.game_dir])

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
          <Divider />
          <Text>{t("overview.source.editHelp")}</Text>
          <Field label={t("overview.source.address")}>
            <Input
              value={serviceUrl}
              disabled={props.busy}
              placeholder="http://127.0.0.1:8111"
              onChange={setServiceUrl}
            />
          </Field>
          <Field
            label={t("overview.source.sourceDir")}
            help={t("overview.source.sourceDirHelp")}
          >
            <Input
              value={sourceDir}
              disabled={props.busy}
              placeholder={t("overview.source.sourceDirPlaceholder")}
              onChange={setSourceDir}
            />
          </Field>
          <Field
            label={t("overview.source.gameDir")}
            help={t("overview.source.gameDirHelp")}
          >
            <Input
              value={gameDir}
              disabled={props.busy}
              placeholder={t("overview.source.gameDirPlaceholder")}
              onChange={setGameDir}
            />
          </Field>
          <Inline gap={8} wrap>
            <ActionButton
              tone="primary"
              refresh
              actionId="set_connection"
              values={{
                service_url: serviceUrl,
                service_source_dir: sourceDir,
                game_dir: gameDir,
                reconnect: false,
              }}
            >
              {t("overview.source.save")}
            </ActionButton>
            <ActionButton
              tone="success"
              refresh
              actionId="set_connection"
              values={{
                service_url: serviceUrl,
                service_source_dir: sourceDir,
                game_dir: gameDir,
                reconnect: true,
              }}
            >
              {t("overview.source.saveAndReconnect")}
            </ActionButton>
            {findAction("reconnect") ? (
              <ActionButton action={findAction("reconnect")} tone="info" refresh>
                {t("diagnostics.actions.reconnect")}
              </ActionButton>
            ) : null}
          </Inline>
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
