import {
  ActionButton,
  Alert,
  Card,
  Columns,
  Field,
  Inline,
  NumberInput,
  SegmentedControl,
  Stack,
  StatusBadge,
  Switch,
  Text,
  useEffect,
  useState,
} from "@neko/plugin-ui"

import { categoryLabel, intrusionModeLabel } from "./format"
import type { ArbiterState, Translate, WowsConfigView } from "./types"

export function PreferencesSection(props: {
  config: WowsConfigView
  arbiter: ArbiterState
  runtimeNow: number
  categories: string[]
  lanes: string[]
  busy: boolean
  onSetChannelMode: (mode: string) => void
  onSetIntrusion: (mode: string, quietWindowSeconds: number) => void
  onSetCategory: (category: string, enabled: boolean) => void
  onSetLane: (lane: string, enabled: boolean) => void
  onSetLaneTiming: (lane: string, ttl: number, minGap: number) => void
  t: Translate
}) {
  const { t } = props
  const config = props.config || {}
  const arbiter = props.arbiter || {}
  const disabledCategories = new Set(config.disabled_categories || [])
  const disabledLanes = new Set(config.disabled_lanes || [])
  const [quiet, setQuiet] = useState<number | "">(
    config.user_chat_quiet_window_seconds ?? 60
  )

  useEffect(() => {
    setQuiet(config.user_chat_quiet_window_seconds ?? 60)
  }, [config.user_chat_quiet_window_seconds])

  return (
    <Stack gap={12}>
      <Card title={t("preferences.intrusion.title")}>
        <Stack gap={10}>
          <SegmentedControl
            value={config.dialogue_intrusion_mode || "critical_only"}
            disabled={props.busy}
            options={[
              {
                value: "no_interrupt",
                label: t("preferences.intrusion.noInterrupt"),
              },
              {
                value: "critical_only",
                label: t("preferences.intrusion.criticalOnly"),
              },
              {
                value: "allow_interrupt",
                label: t("preferences.intrusion.allow"),
              },
            ]}
            onChange={(mode) =>
              props.onSetIntrusion(String(mode), Number(quiet) || 0)
            }
          />
          <Field label={t("preferences.intrusion.quietWindow")}>
            <NumberInput
              value={quiet}
              min={0}
              max={1800}
              step={5}
              disabled={props.busy}
              onChange={(value) => setQuiet(value === "" ? "" : Number(value))}
            />
          </Field>
          <Inline gap={8}>
            <ActionButton
              tone="primary"
              refresh
              actionId="set_intrusion_mode"
              values={{
                mode: config.dialogue_intrusion_mode || "critical_only",
                quiet_window_seconds: Number(quiet) || 0,
              }}
            >
              {t("preferences.intrusion.apply")}
            </ActionButton>
          </Inline>
          <Alert tone="info">{t("preferences.intrusion.help")}</Alert>
          {arbiter.quiet_until && arbiter.quiet_until > props.runtimeNow ? (
            <Inline gap={8} wrap>
              <StatusBadge
                tone="warning"
                label={t("preferences.intrusion.active")}
              />
              <StatusBadge
                tone="default"
                label={t("preferences.intrusion.policy", {
                  mode: intrusionModeLabel(arbiter.intrusion_mode, t),
                })}
              />
            </Inline>
          ) : null}
        </Stack>
      </Card>

      <Card title={t("preferences.promptChannel.title")}>
        <Stack gap={8}>
          <SegmentedControl
            value={config.channel_mode || "dual"}
            disabled={props.busy}
            options={[
              {
                value: "dual",
                label: t("preferences.promptChannel.dual"),
              },
              {
                value: "single",
                label: t("preferences.promptChannel.single"),
              },
            ]}
            onChange={(mode) => props.onSetChannelMode(String(mode))}
          />
          <Text>{t("preferences.promptChannel.help")}</Text>
        </Stack>
      </Card>

      <Card title={t("preferences.lanes.title")}>
        <Stack gap={8}>
          {(props.lanes || []).map((lane) => (
            <Switch
              key={lane}
              checked={!disabledLanes.has(lane)}
              disabled={props.busy}
              label={lane === "urgent"
                ? t("preferences.lanes.urgent")
                : t("preferences.lanes.normal")}
              onChange={(enabled) => props.onSetLane(lane, enabled)}
            />
          ))}
          <Text>{t("preferences.lanes.help")}</Text>
        </Stack>
      </Card>

      <Card title={t("preferences.categories.title")}>
        <Stack gap={8}>
          <Columns minWidth={200} gap={8}>
            {(props.categories || []).map((category) => (
              <Switch
                key={category}
                checked={!disabledCategories.has(category)}
                disabled={props.busy}
                label={categoryLabel(category, t)}
                onChange={(enabled) => props.onSetCategory(category, enabled)}
              />
            ))}
          </Columns>
        </Stack>
      </Card>

      <Card title={t("preferences.timing.title")}>
        <Stack gap={10}>
          <LaneTiming
            lane="urgent"
            label={t("preferences.timing.urgent")}
            ttl={config.urgent_ttl_seconds}
            minGap={config.urgent_min_gap_seconds}
            busy={props.busy}
            t={t}
          />
          <LaneTiming
            lane="normal"
            label={t("preferences.timing.normal")}
            ttl={config.normal_ttl_seconds}
            minGap={config.normal_min_gap_seconds}
            busy={props.busy}
            t={t}
          />
          <Text>{t("preferences.timing.help")}</Text>
        </Stack>
      </Card>
    </Stack>
  )
}

function LaneTiming(props: {
  lane: string
  label: string
  ttl?: number
  minGap?: number
  busy: boolean
  t: Translate
}) {
  const { t } = props
  const [ttl, setTtl] = useState<number | "">(props.ttl ?? 8)
  const [minGap, setMinGap] = useState<number | "">(props.minGap ?? 6)

  useEffect(() => {
    setTtl(props.ttl ?? 8)
  }, [props.ttl])

  useEffect(() => {
    setMinGap(props.minGap ?? 6)
  }, [props.minGap])

  return (
    <Stack gap={6}>
      <Text>{props.label}</Text>
      <Inline gap={8} wrap align="end">
        <Field label={t("preferences.timing.ttl")}>
          <NumberInput
            value={ttl}
            min={1}
            max={600}
            step={1}
            disabled={props.busy}
            onChange={(value) => setTtl(value === "" ? "" : Number(value))}
          />
        </Field>
        <Field label={t("preferences.timing.minGap")}>
          <NumberInput
            value={minGap}
            min={0}
            max={3600}
            step={1}
            disabled={props.busy}
            onChange={(value) => setMinGap(value === "" ? "" : Number(value))}
          />
        </Field>
        <ActionButton
          tone="primary"
          refresh
          actionId="set_lane_timing"
          values={{
            lane: props.lane,
            ttl_seconds: Number(ttl) || 0,
            min_gap_seconds: Number(minGap) || 0,
          }}
        >
          {t("common.apply")}
        </ActionButton>
      </Inline>
    </Stack>
  )
}
