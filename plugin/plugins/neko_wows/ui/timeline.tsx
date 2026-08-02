import {
  Card,
  DataTable,
  Divider,
  EmptyState,
  JsonView,
  Stack,
  StatusBadge,
  Text,
  TextBlock,
  useState,
} from "@neko/plugin-ui"

import {
  formatClock,
  outcomeLabel,
  outcomeTone,
  stageLabel,
} from "./format"
import type { TimelineEntry } from "./types"

export function TimelineSection(props: { entries: TimelineEntry[] }) {
  const entries = props.entries || []
  const [selected, setSelected] = useState<TimelineEntry | null>(null)

  if (!entries.length) {
    return (
      <EmptyState
        title="还没有链路记录"
        description="启动插件并进入一局对战后，每一帧的检测、仲裁与投递结果都会出现在这里。"
      />
    )
  }

  return (
    <Stack gap={12}>
      <Text>
        每一行是一个阶段的结果，包含没有产生输出的阶段 —— 猫娘没开口时，这里会说清是
        插件自己压住了，还是宿主没有放行。
      </Text>

      <DataTable
        data={entries}
        maxRows={60}
        emptyText="暂无记录"
        onSelect={(row) => setSelected(row)}
        columns={[
          { key: "at", label: "时间", render: (row) => formatClock(row.at) },
          {
            key: "stage",
            label: "阶段",
            render: (row) => stageLabel(row.stage),
          },
          {
            key: "outcome",
            label: "结果",
            render: (row) => (
              <StatusBadge
                tone={outcomeTone(row.stage, row.outcome)}
                label={outcomeLabel(row.stage, row.outcome)}
              />
            ),
          },
          { key: "event_id", label: "事件", render: (row) => row.event_id || "—" },
          { key: "seq", label: "seq", render: (row) => row.seq ?? "—" },
          { key: "reason", label: "原因", render: (row) => row.reason || "—" },
        ]}
      />

      {selected ? (
        <Card title={`详情：${stageLabel(selected.stage)} / ${outcomeLabel(selected.stage, selected.outcome)}`}>
          <Stack gap={8}>
            <Text>
              {`seq ${selected.seq ?? "—"} · battleId ${selected.battle_id || "—"}`}
            </Text>
            {selected.reason ? <Text>{selected.reason}</Text> : null}
            {selected.detail && selected.detail.preview ? (
              <Stack gap={6}>
                <Divider />
                <Text>dry-run 下本应发给猫娘的完整提示词：</Text>
                <TextBlock text={selected.detail.preview} />
              </Stack>
            ) : null}
            {selected.detail && Object.keys(selected.detail).length ? (
              <Stack gap={6}>
                <Divider />
                <JsonView data={stripPreview(selected.detail)} />
              </Stack>
            ) : null}
          </Stack>
        </Card>
      ) : (
        <Text>点一行查看该阶段的完整事实与提示词预览。</Text>
      )}
    </Stack>
  )
}

function stripPreview(detail: Record<string, any>): Record<string, any> {
  const rest: Record<string, any> = {}
  Object.keys(detail || {}).forEach((key) => {
    if (key !== "preview") rest[key] = detail[key]
  })
  return rest
}
