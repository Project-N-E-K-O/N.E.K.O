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
import type { DashboardState } from "./types"

export function DiagnosticsSection(props: {
  state: DashboardState
  actions: HostedAction[]
  onSetDryRun: (value: boolean) => void
  busy: boolean
}) {
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
            ? `输出已暂停：${dispatcher.pause_reason}`
            : "输出已暂停。"}
        </Alert>
      ) : null}

      <Card title="战斗播报">
        <Stack gap={10}>
          <Switch
            checked={!config.dry_run}
            disabled={props.busy}
            label="开启真实输出（关闭 dry-run）"
            onChange={(checked) => props.onSetDryRun(!checked)}
          />
          {config.dry_run ? (
            <Text>
              dry-run 开着：整条链路照跑到完整提示词，然后在投递前短路。宿主调用次数应当恒为 0。
            </Text>
          ) : (
            <Warning>
              真实输出已开启，猫娘会在战斗中主动开口。这个开关只在本次运行内有效，
              重启插件后回到 dry-run。
            </Warning>
          )}
          <Text>
            提示词通道、播报类别与插话策略都在「偏好」页。
          </Text>
        </Stack>
      </Card>

      <Card title="输出计数">
        <Stack gap={8}>
          <Columns minWidth={150} gap={10}>
            <StatCard label="宿主调用" value={formatCount(dispatcher.host_calls)} />
            <StatCard label="已投递" value={formatCount(dispatcher.delivered)} />
            <StatCard label="已压制" value={formatCount(dispatcher.suppressed)} />
            <StatCard label="队列" value={formatCount(arbiter.queued)} />
          </Columns>
          <KeyValue
            data={{
              近期失败: `${formatCount(dispatcher.recent_failures)} / ${formatCount(
                dispatcher.failure_limit
              )}`,
              冷却中的事件: formatCount(arbiter.cooldowns),
              本局已说过: (arbiter.fired_once_per_battle || []).join("、") || "无",
              场景上下文: state.context_injected ? "已注入" : "未注入",
            }}
          />
          {config.dry_run && (dispatcher.host_calls || 0) > 0 ? (
            <Alert tone="danger">
              dry-run 下宿主调用次数不应大于 0，这是一个 bug，请反馈。
            </Alert>
          ) : null}
        </Stack>
      </Card>

      <Card title="节流参数">
        <KeyValue
          data={{
            "紧急 TTL": `${config.urgent_ttl_seconds ?? "—"}s`,
            紧急最短间隔: `${config.urgent_min_gap_seconds ?? "—"}s`,
            "常规 TTL": `${config.normal_ttl_seconds ?? "—"}s`,
            常规最短间隔: `${config.normal_min_gap_seconds ?? "—"}s`,
          }}
        />
      </Card>

      <Card title="操作">
        <Stack gap={8}>
          <Inline gap={8} wrap>
            {paused && findAction("resume") ? (
              <ActionButton action={findAction("resume")} tone="success" refresh>
                恢复输出
              </ActionButton>
            ) : null}
            {!paused && findAction("pause") ? (
              <ActionButton action={findAction("pause")} tone="danger" refresh>
                急停
              </ActionButton>
            ) : null}
            {findAction("reconnect") ? (
              <ActionButton action={findAction("reconnect")} tone="primary" refresh>
                重连数据源
              </ActionButton>
            ) : null}
            {findAction("clear_timeline") ? (
              <ActionButton action={findAction("clear_timeline")} tone="info" refresh>
                清空时间线
              </ActionButton>
            ) : null}
          </Inline>
          <Inline gap={8} wrap>
            <StatusBadge
              tone={state.running ? "success" : "warning"}
              label={state.running ? "插件运行中" : "插件未运行"}
            />
          </Inline>
        </Stack>
      </Card>
    </Stack>
  )
}
