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
import type { DashboardState } from "./types"

export function OverviewSection(props: { state: DashboardState }) {
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
          {state.mod_hint}
        </Alert>
      ) : null}

      {state.reconnect_required ? (
        <Alert tone="warning">
          数据源配置已改动。改动只在重连后生效，点“重连数据源”应用。
        </Alert>
      ) : null}

      <Columns minWidth={180} gap={12}>
        <StatCard label="遥测服务" value={serviceModeLabel(service.mode)} />
        <StatCard label="传输" value={transport.mode || "未启动"} />
        <StatCard
          label="战局状态"
          value={sourceStatusLabel(snapshot.status)}
        />
        <StatCard
          label="输出"
          value={config.dry_run ? "dry-run（不发言）" : "真实输出"}
        />
      </Columns>

      <Card title="数据源">
        <Stack gap={8}>
          <Inline gap={8} wrap>
            <StatusBadge
              tone={serviceModeTone(service.mode)}
              label={serviceModeLabel(service.mode)}
            />
            {service.paused ? (
              <StatusBadge tone="danger" label="自动拉起已暂停" />
            ) : null}
            {snapshot.legacy ? (
              <StatusBadge tone="warning" label="旧版服务（信封本地推导）" />
            ) : null}
          </Inline>
          <KeyValue
            data={{
              地址: config.service_url || "—",
              serviceId: service.service_id || "—",
              apiVersion: service.api_version || "—",
              instanceId: service.instance_id || "—",
              进程: service.pid ? `pid ${service.pid}` : "非插件托管",
              说明: service.detail || "—",
              错误: service.error || "无",
              服务源码目录: config.service_source_dir || "（未配置，不自动拉起）",
              游戏目录: config.game_dir || "（未配置）",
            }}
          />
        </Stack>
      </Card>

      <Card title="传输与游标">
        <Stack gap={8}>
          <KeyValue
            data={{
              当前模式: transport.mode || "未启动",
              传输代次: `epoch ${transport.epoch ?? 0}`,
              "WS 连接次数": formatCount(transport.ws_connects),
              "WS 失败次数": formatCount(transport.ws_failures),
              "REST 轮询次数": formatCount(transport.rest_polls),
              "REST 失败次数": formatCount(transport.rest_failures),
              重连等待: `${transport.reconnect_delay ?? 0}s`,
              最近一帧: formatClock(transport.last_frame_at),
              最近错误: transport.last_error || "无",
            }}
          />
          <Divider />
          <KeyValue
            data={{
              已接受游标: `${cursor.instance_id || "—"} / seq ${cursor.seq ?? -1}`,
              接受帧数: formatCount(cursor.accepted),
              丢弃帧数: describeDropped(cursor.dropped),
              累计帧数: formatCount(counters.frames),
              累计事件: formatCount(counters.events),
            }}
          />
          <Text>
            重复与乱序帧按 (instanceId, seq) 与传输代次丢弃，这些丢弃是正常的去重结果。
          </Text>
        </Stack>
      </Card>

      {hasSnapshot ? (
        <Card title="当前战局">
          <Stack gap={8}>
            <Inline gap={8} wrap>
              <StatusBadge
                tone={sourceStatusTone(snapshot.status)}
                label={sourceStatusLabel(snapshot.status)}
              />
              {snapshot.transport ? (
                <StatusBadge tone="info" label={`来自 ${snapshot.transport}`} />
              ) : null}
            </Inline>
            <KeyValue
              data={{
                battleId: snapshot.battle_id || "—",
                seq: formatCount(snapshot.seq),
                地图: snapshot.map_name || "未知",
                模式: snapshot.game_mode || snapshot.battle_type || "未知",
                自身血量: formatPercent(snapshot.own_hp_ratio),
                存活友方: formatCount(snapshot.allies_alive),
                存活敌方: formatCount(snapshot.enemies_alive),
                最近敌舰: formatMetres(snapshot.nearest_enemy_m),
              }}
            />
            <Divider />
            <Text>数据域可用性</Text>
            <Inline gap={6} wrap>
              {Object.keys(snapshot.availability || {})
                .sort()
                .map((domain) => (
                  <StatusBadge
                    key={domain}
                    tone={availabilityTone((snapshot.availability || {})[domain])}
                    label={`${domain}：${availabilityLabel(
                      (snapshot.availability || {})[domain]
                    )}`}
                  />
                ))}
            </Inline>
            <Text>
              「本帧无数据」和「过期」都不等于否定结论：依赖它的检测器会被直接拦下，
              而不是当成 false。
            </Text>
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
