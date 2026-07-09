import {
  Card,
  DataTable,
  Grid,
  Stack,
  StatCard,
  StatusBadge,
  Text,
} from "@neko/plugin-ui"
import {
  eventSignalLabel,
  eventSignalTone,
  formatLatencyMs,
  interactionRoute,
  interactionRouteLabel,
  interactionRouteTone,
  speechExplanationTone,
} from "./panel_helpers"

type PanelTranslator = (key: string) => string
type DynamicLabel = (group: string, keyPrefix: string, value: string) => string

export function LiveExplainSection({
  t,
  dynamicLabel,
  liveExplain,
  speechSummary,
  speechReason,
}: {
  t: PanelTranslator
  dynamicLabel: DynamicLabel
  liveExplain: any
  speechSummary: string
  speechReason: string
}) {
  const explainSummary = String(liveExplain.summary || speechSummary || "waiting")
  const explainReason = String(liveExplain.reason || speechReason || "")
  const explainTraceId = String(liveExplain.trace_id || "")
  const explainTimeline = Array.isArray(liveExplain.timeline) ? liveExplain.timeline : []
  const explainChain = Array.isArray(liveExplain.chain) ? liveExplain.chain : []
  const explainSelection = liveExplain.selection || {}
  const explainViewerMemory = liveExplain.viewer_memory || {}
  const explainLatest = liveExplain.latest_result || {}
  const explainThemes = Array.isArray(explainSelection.theme_keys) ? explainSelection.theme_keys.join(", ") : "-"
  const explainTopTags = Array.isArray(explainViewerMemory.top_preference_tags)
    ? explainViewerMemory.top_preference_tags.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"
  const explainTopTopics = Array.isArray(explainViewerMemory.top_favorite_topics)
    ? explainViewerMemory.top_favorite_topics.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"
  const explainTopJokes = Array.isArray(explainViewerMemory.top_running_jokes)
    ? explainViewerMemory.top_running_jokes.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"

  return (
    <Card title={t("panel.explain.title")}>
      <Stack>
        <Grid cols={4}>
          <StatCard
            label={t("panel.explain.summary")}
            value={<StatusBadge tone={speechExplanationTone(explainSummary)} label={dynamicLabel("speechSummary", "panel.speechExplanation.summary", explainSummary)} />}
          />
          <StatCard label={t("panel.columns.reason")} value={dynamicLabel("speechReason", "panel.speechExplanation.reason", explainReason)} />
          <StatCard label={t("panel.explain.topicThemes")} value={explainThemes || "-"} />
          <StatCard label={t("panel.explain.trace")} value={explainTraceId || "-"} />
        </Grid>
        <Grid cols={3}>
          <StatCard label={t("panel.explain.viewerMemory")} value={`${Number(explainViewerMemory.profiles_with_impressions || explainViewerMemory.profiles_with_preferences || 0)}/${Number(explainViewerMemory.profile_count || 0)}`} />
          <StatCard label={t("panel.columns.preferenceTags")} value={explainTopTags || "-"} />
          <StatCard label={t("panel.explain.latestResult")} value={`${String(explainLatest.status || "-")} / ${formatLatencyMs(explainLatest.latency_ms)}`} />
        </Grid>
        <Grid cols={2}>
          <StatCard label={t("panel.columns.favoriteTopics")} value={explainTopTopics || "-"} />
          <StatCard label={t("panel.columns.runningJokes")} value={explainTopJokes || "-"} />
        </Grid>
        {explainChain.length ? (
          <DataTable
            data={explainChain.map((item: any, index: number) => ({ ...item, row_id: item.id || String(index) }))}
            rowKey="row_id"
            columns={[
              { key: "stage", label: t("panel.explain.stage"), render: (row: any) => row.stage || row.id || "-" },
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "blocked" ? "warning" : row.status === "healthy" ? "success" : "default"} label={String(row.status || "-")} /> },
              { key: "last_outcome", label: t("panel.columns.message"), render: (row: any) => row.last_outcome || "-" },
              { key: "last_skip_reason", label: t("panel.columns.detail"), render: (row: any) => row.last_skip_reason || "-" },
            ]}
          />
        ) : null}
        {explainTimeline.length ? (
          <DataTable
            data={explainTimeline.map((item: any, index: number) => ({ ...item, row_id: `${item.trace_id || "trace"}-${index}` }))}
            rowKey="row_id"
            columns={[
              { key: "stage", label: t("panel.explain.stage"), render: (row: any) => row.stage || "-" },
              { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "skipped" ? "warning" : row.status === "ok" ? "success" : "default"} label={String(row.status || "-")} /> },
              { key: "route", label: t("panel.columns.responseModule"), render: (row: any) => row.route || "-" },
              { key: "reason", label: t("panel.columns.detail"), render: (row: any) => row.reason || "-" },
            ]}
          />
        ) : null}
      </Stack>
    </Card>
  )
}

export function RecentResultsTable({ t, results }: { t: PanelTranslator; results: any[] }) {
  return (
    <Card title={t("panel.recent.title")}>
      {results.length ? (
        <DataTable
          data={results.map((item, index) => ({ ...item, id: `${item.created_at || index}-${index}` }))}
          rowKey="id"
          columns={[
            { key: "uid", label: "UID", render: (row: any) => row.identity?.uid || row.event?.uid || "-" },
            { key: "nickname", label: t("panel.columns.nickname"), render: (row: any) => row.identity?.nickname || row.event?.nickname || "-" },
            { key: "response_module", label: t("panel.columns.responseModule"), render: (row: any) => {
              const route = interactionRoute(row)
              return <StatusBadge tone={interactionRouteTone(route)} label={interactionRouteLabel(route, t)} />
            } },
            { key: "event_signal", label: t("panel.columns.eventSignal"), render: (row: any) => {
              const signal = String(row.event_signal || "unknown")
              return <StatusBadge tone={eventSignalTone(signal)} label={eventSignalLabel(signal, t)} />
            } },
            { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={row.status === "pushed" ? "success" : "warning"} label={String(row.status || "-")} /> },
            { key: "response_latency_ms", label: t("panel.columns.responseLatency"), render: (row: any) => formatLatencyMs(row.response_latency_ms) },
            { key: "reason", label: t("panel.columns.reason"), render: (row: any) => row.reason || row.output || "-" },
          ]}
        />
      ) : (
        <Text>{t("panel.empty.results")}</Text>
      )}
    </Card>
  )
}

export function ViewerProfilesTable({
  t,
  profiles,
}: {
  t: PanelTranslator
  profiles: any[]
}) {
  return (
    <Card title={t("panel.profiles.title")}>
      {profiles.length ? (
        <DataTable
          data={profiles.map((item, index) => ({ ...item, id: item.uid || String(index) }))}
          rowKey="id"
          columns={[
            { key: "uid", label: "UID" },
            { key: "nickname", label: t("panel.columns.nickname") },
            { key: "roast_count", label: t("panel.columns.roastCount") },
            { key: "danmaku_count", label: t("panel.columns.danmakuCount"), render: (row: any) => row.danmaku_count || 0 },
            { key: "viewer_stage", label: t("panel.columns.viewerStage"), render: (row: any) => profileBadge("viewerStage", row.viewer_stage, t) },
            { key: "profile_confidence", label: t("panel.columns.profileConfidence"), render: (row: any) => profileBadge("profileConfidence", row.profile_confidence, t) },
            { key: "profile_freshness", label: t("panel.columns.profileFreshness"), render: (row: any) => profileBadge("profileFreshness", row.profile_freshness, t) },
            { key: "top_preference_tags", label: t("panel.columns.preferenceTags"), render: (row: any) => Array.isArray(row.top_preference_tags) ? row.top_preference_tags.map((item: any) => `${item.tag}:${item.count}`).join(", ") : "-" },
            { key: "top_favorite_topics", label: t("panel.columns.favoriteTopics"), render: (row: any) => Array.isArray(row.top_favorite_topics) ? row.top_favorite_topics.map((item: any) => `${item.tag}:${item.count}`).join(", ") : "-" },
            { key: "top_running_jokes", label: t("panel.columns.runningJokes"), render: (row: any) => Array.isArray(row.top_running_jokes) ? row.top_running_jokes.map((item: any) => `${item.tag}:${item.count}`).join(", ") : "-" },
            { key: "profile_summary", label: t("panel.columns.latestSummary"), render: (row: any) => row.impression_summary || row.profile_summary || row.last_interaction_summary || "-" },
            { key: "avoid_guidance", label: t("panel.columns.avoidGuidance"), render: (row: any) => row.avoid_guidance || "-" },
            { key: "reply_guidance", label: t("panel.columns.replyGuidance"), render: (row: any) => row.reply_guidance || "-" },
            { key: "last_seen_at", label: t("panel.columns.lastSeen") },
          ]}
        />
      ) : (
        <Text>{t("panel.empty.profiles")}</Text>
      )}
    </Card>
  )
}

function profileBadge(group: "viewerStage" | "profileConfidence" | "profileFreshness", value: any, t: PanelTranslator) {
  const key = String(value || "none")
  return <StatusBadge tone={profileTone(group, key)} label={profileLabel(group, key, t)} />
}

function profileLabel(group: string, key: string, t: PanelTranslator): string {
  const text = t(`panel.${group}.${key}`)
  return text && text !== `panel.${group}.${key}` ? text : key || "-"
}

function profileTone(group: string, key: string): "success" | "warning" | "danger" | "default" {
  if (group === "viewerStage") {
    if (key === "familiar_viewer" || key === "regular_viewer") return "success"
    if (key === "returning_viewer") return "warning"
    return "default"
  }
  if (group === "profileConfidence") {
    if (key === "high") return "success"
    if (key === "medium") return "warning"
    if (key === "low") return "danger"
    return "default"
  }
  if (key === "fresh" || key === "warm") return "success"
  if (key === "stale") return "warning"
  if (key === "old") return "danger"
  return "default"
}
