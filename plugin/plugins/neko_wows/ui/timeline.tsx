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
import type { TimelineEntry, Translate } from "./types"

export function TimelineSection(props: {
  entries: TimelineEntry[]
  t: Translate
  locale: string
}) {
  const { t } = props
  const entries = props.entries || []
  const [selected, setSelected] = useState<TimelineEntry | null>(null)

  if (!entries.length) {
    return (
      <EmptyState
        title={t("timeline.empty.title")}
        description={t("timeline.empty.description")}
      />
    )
  }

  return (
    <Stack gap={12}>
      <Text>{t("timeline.help")}</Text>

      <DataTable
        data={entries}
        maxRows={60}
        emptyText={t("timeline.empty.rows")}
        onSelect={(row) => setSelected(row)}
        columns={[
          {
            key: "at",
            label: t("timeline.columns.time"),
            render: (row) => formatClock(row.at, props.locale),
          },
          {
            key: "stage",
            label: t("timeline.columns.stage"),
            render: (row) => stageLabel(row.stage, t),
          },
          {
            key: "outcome",
            label: t("timeline.columns.result"),
            render: (row) => (
              <StatusBadge
                tone={outcomeTone(row.stage, row.outcome)}
                label={outcomeLabel(row.stage, row.outcome, t)}
              />
            ),
          },
          {
            key: "event_id",
            label: t("timeline.columns.event"),
            render: (row) => row.event_id || "—",
          },
          { key: "seq", label: "seq", render: (row) => row.seq ?? "—" },
          {
            key: "reason",
            label: t("timeline.columns.reason"),
            render: (row) => row.reason || "—",
          },
        ]}
      />

      {selected ? (
        <Card title={t("timeline.detail.title", {
          stage: stageLabel(selected.stage, t),
          outcome: outcomeLabel(selected.stage, selected.outcome, t),
        })}>
          <Stack gap={8}>
            <Text>
              {`seq ${selected.seq ?? "—"} · battleId ${selected.battle_id || "—"}`}
            </Text>
            {selected.reason ? <Text>{selected.reason}</Text> : null}
            {selected.detail && selected.detail.preview ? (
              <Stack gap={6}>
                <Divider />
                <Text>{t("timeline.detail.preview")}</Text>
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
        <Text>{t("timeline.detail.select")}</Text>
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
