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
  useState,
} from "@neko/plugin-ui"

import { categoryLabel } from "./format"
import type { ArbiterState, WowsConfigView } from "./types"

const INTRUSION_OPTIONS = [
  { value: "no_interrupt", label: "不打断" },
  { value: "critical_only", label: "仅紧急打断" },
  { value: "allow_interrupt", label: "允许打断" },
]

export function PreferencesSection(props: {
  config: WowsConfigView
  arbiter: ArbiterState
  categories: string[]
  lanes: string[]
  busy: boolean
  onSetChannelMode: (mode: string) => void
  onSetIntrusion: (mode: string, quietWindowSeconds: number) => void
  onSetCategory: (category: string, enabled: boolean) => void
  onSetLane: (lane: string, enabled: boolean) => void
  onSetLaneTiming: (lane: string, ttl: number, minGap: number) => void
}) {
  const config = props.config || {}
  const arbiter = props.arbiter || {}
  const disabledCategories = new Set(config.disabled_categories || [])
  const disabledLanes = new Set(config.disabled_lanes || [])
  const [quiet, setQuiet] = useState<number | "">(
    config.user_chat_quiet_window_seconds ?? 60
  )

  return (
    <Stack gap={12}>
      <Card title="插话策略">
        <Stack gap={10}>
          <SegmentedControl
            value={config.dialogue_intrusion_mode || "critical_only"}
            disabled={props.busy}
            options={INTRUSION_OPTIONS}
            onChange={(mode) =>
              props.onSetIntrusion(String(mode), Number(quiet) || 0)
            }
          />
          <Field label="用户静默窗口（秒）">
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
              应用静默窗口
            </ActionButton>
          </Inline>
          <Alert tone="info">
            宿主自己已经有一层「用户刚说过话就不放行」的短闸门，这里是更长的、可调的那一层。
            两层都会在时间线里写明是谁压住的，不会混在一起。
          </Alert>
          {arbiter.quiet_until ? (
            <Inline gap={8} wrap>
              <StatusBadge tone="warning" label="静默窗口生效中" />
              <StatusBadge
                tone="default"
                label={`策略 ${arbiter.intrusion_mode || "—"}`}
              />
            </Inline>
          ) : null}
        </Stack>
      </Card>

      <Card title="提示词通道">
        <Stack gap={8}>
          <SegmentedControl
            value={config.channel_mode || "dual"}
            disabled={props.busy}
            options={[
              { value: "dual", label: "dual（紧急/常规分开）" },
              { value: "single", label: "single（统一）" },
            ]}
            onChange={(mode) => props.onSetChannelMode(String(mode))}
          />
          <Text>通道只影响措辞：优先级、TTL 和抢占规则两种模式完全一样。</Text>
        </Stack>
      </Card>

      <Card title="播报通道">
        <Stack gap={8}>
          {(props.lanes || []).map((lane) => (
            <Switch
              key={lane}
              checked={!disabledLanes.has(lane)}
              disabled={props.busy}
              label={lane === "urgent" ? "紧急播报" : "常规播报"}
              onChange={(enabled) => props.onSetLane(lane, enabled)}
            />
          ))}
          <Text>关掉的通道在候选阶段就被拦下，不占队列也不占冷却。</Text>
        </Stack>
      </Card>

      <Card title="事件类别">
        <Stack gap={8}>
          <Columns minWidth={200} gap={8}>
            {(props.categories || []).map((category) => (
              <Switch
                key={category}
                checked={!disabledCategories.has(category)}
                disabled={props.busy}
                label={categoryLabel(category)}
                onChange={(enabled) => props.onSetCategory(category, enabled)}
              />
            ))}
          </Columns>
        </Stack>
      </Card>

      <Card title="时序覆盖">
        <Stack gap={10}>
          <LaneTiming
            lane="urgent"
            label="紧急"
            ttl={config.urgent_ttl_seconds}
            minGap={config.urgent_min_gap_seconds}
            busy={props.busy}
          />
          <LaneTiming
            lane="normal"
            label="常规"
            ttl={config.normal_ttl_seconds}
            minGap={config.normal_min_gap_seconds}
            busy={props.busy}
          />
          <Text>时序覆盖只在本次运行内有效，重启后回到配置文件里的值。</Text>
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
}) {
  const [ttl, setTtl] = useState<number | "">(props.ttl ?? 8)
  const [minGap, setMinGap] = useState<number | "">(props.minGap ?? 6)

  return (
    <Stack gap={6}>
      <Text>{props.label}</Text>
      <Inline gap={8} wrap align="end">
        <Field label="TTL（秒）">
          <NumberInput
            value={ttl}
            min={1}
            max={600}
            step={1}
            disabled={props.busy}
            onChange={(value) => setTtl(value === "" ? "" : Number(value))}
          />
        </Field>
        <Field label="最短间隔（秒）">
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
          应用
        </ActionButton>
      </Inline>
    </Stack>
  )
}
