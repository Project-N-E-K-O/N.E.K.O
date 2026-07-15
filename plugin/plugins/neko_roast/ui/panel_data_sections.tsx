import {
  Alert,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Field,
  Grid,
  Input,
  KeyValue,
  Modal,
  Stack,
  StatCard,
  StatusBadge,
  Text,
  useState,
} from "@neko/plugin-ui"
import {
  eventSignalLabel,
  eventSignalTone,
  formatLatencyMs,
  interactionRoute,
  interactionRouteLabel,
  interactionRouteTone,
  recentResultTone,
  speechExplanationTone,
} from "./panel_helpers"

type PanelTranslator = (key: string) => string
type DynamicLabel = (group: string, keyPrefix: string, value: string) => string

export function LiveSessionSection({
  t,
  session,
}: {
  t: PanelTranslator
  session: any
}) {
  const [query, setQuery] = useState("")
  const [selectedViewer, setSelectedViewer] = useState<any>(null)
  const viewers = Array.isArray(session?.viewers) ? session.viewers : []
  const normalizedQuery = query.trim().toLowerCase()
  const filteredViewers = viewers.filter((viewer: any) => {
    if (!normalizedQuery) return true
    return String(viewer?.nickname || "").toLowerCase().includes(normalizedQuery)
  })
  const viewerCount = Number(session?.interaction_viewer_count || 0)
  const viewerCountLabel = session?.interaction_viewer_count_capped ? `${viewerCount}+` : viewerCount

  return (
    <Stack>
      {!session?.has_session ? <Alert tone="info">{t("panel.audience.noSession")}</Alert> : null}
      <Grid cols={2}>
        <StatCard label={t("panel.audience.interactionViewers")} value={viewerCountLabel} />
        <StatCard label={t("panel.columns.danmakuCount")} value={Number(session?.danmaku_count || 0)} />
        <StatCard label={t("panel.audience.supportEvents")} value={Number(session?.support_event_count || 0)} />
        <StatCard label={t("panel.audience.nekoOutputs")} value={Number(session?.neko_output_count || 0)} />
      </Grid>
      {session?.interaction_viewer_count_capped ? <Text>{t("panel.audience.viewerCountCapped")}</Text> : null}
      <Card title={t("panel.audience.recentViewers")}>
        <Stack>
          <Field label={t("panel.audience.searchLabel")}>
            <Input value={query} placeholder={t("panel.audience.sessionSearchPlaceholder")} onChange={setQuery} />
          </Field>
          {filteredViewers.length ? (
            <div style={{ overflowX: "auto" }}>
              <DataTable
                data={filteredViewers}
                rowKey="viewer_key"
                maxRows={30}
                columns={[
                  {
                    key: "nickname",
                    label: t("panel.columns.nickname"),
                    render: (row: any) => (
                      <Button tone="default" onClick={() => setSelectedViewer(row)}>
                        {row.nickname || t("panel.audience.anonymousViewer")}
                      </Button>
                    ),
                  },
                  {
                    key: "interactions",
                    label: t("panel.audience.interactions"),
                    render: (row: any) => `${Number(row.danmaku_count || 0)} / ${Number(row.support_event_count || 0)}`,
                  },
                  {
                    key: "last_event_type",
                    label: t("panel.audience.latestEvent"),
                    render: (row: any) => <StatusBadge tone={sessionEventTone(row.last_event_type)} label={sessionEventLabel(t, row.last_event_type)} />,
                  },
                  {
                    key: "last_interaction_at",
                    label: t("panel.columns.lastSeen"),
                    render: (row: any) => formatDateTime(row.last_interaction_at),
                  },
                ]}
              />
            </div>
          ) : (
            <Text>{session?.has_session ? t("panel.audience.noSessionViewers") : t("panel.audience.noSession")}</Text>
          )}
        </Stack>
      </Card>
      <Modal
        open={!!selectedViewer}
        title={t("panel.audience.sessionDetailTitle")}
        size="lg"
        onClose={() => setSelectedViewer(null)}
        footer={<Button tone="default" onClick={() => setSelectedViewer(null)}>{t("panel.actions.cancel")}</Button>}
      >
        {selectedViewer ? (
          <Stack>
            <Text>{selectedViewer.nickname || t("panel.audience.anonymousViewer")}</Text>
            <KeyValue
              items={[
                { key: "danmaku", label: t("panel.columns.danmakuCount"), value: Number(selectedViewer.danmaku_count || 0) },
                { key: "support", label: t("panel.audience.supportEvents"), value: Number(selectedViewer.support_event_count || 0) },
                { key: "replies", label: t("panel.audience.nekoReplyCount"), value: Number(selectedViewer.neko_reply_count || 0) },
                { key: "lastEvent", label: t("panel.audience.latestEvent"), value: sessionEventLabel(t, selectedViewer.last_event_type) },
                { key: "lastSeen", label: t("panel.columns.lastSeen"), value: formatDateTime(selectedViewer.last_interaction_at) },
              ]}
            />
          </Stack>
        ) : null}
      </Modal>
    </Stack>
  )
}

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
            { key: "status", label: t("panel.columns.status"), render: (row: any) => <StatusBadge tone={recentResultTone(String(row.status || ""))} label={String(row.status || "-")} /> },
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
  onResetImpression,
  onDeleteProfile,
}: {
  t: PanelTranslator
  profiles: any[]
  onResetImpression: (uid: string) => Promise<void>
  onDeleteProfile: (uid: string) => Promise<void>
}) {
  const [query, setQuery] = useState("")
  const [selectedProfile, setSelectedProfile] = useState<any>(null)
  const [pendingAction, setPendingAction] = useState<"reset" | "delete" | "">("")
  const [actionPending, setActionPending] = useState(false)
  const normalizedQuery = query.trim().toLowerCase()
  const filteredProfiles = profiles.filter((profile: any) => {
    if (!normalizedQuery) return true
    return String(profile?.nickname || "").toLowerCase().includes(normalizedQuery)
  })

  return (
    <Stack>
      <Card title={t("panel.profiles.title")}>
        <Stack>
          <Field label={t("panel.audience.searchLabel")}>
            <Input value={query} placeholder={t("panel.audience.profileSearchPlaceholder")} onChange={setQuery} />
          </Field>
          {filteredProfiles.length ? (
            <div style={{ overflowX: "auto" }}>
              <DataTable
                data={filteredProfiles.map((item, index) => ({ ...item, id: item.uid || String(index) }))}
                rowKey="id"
                columns={[
                  {
                    key: "nickname",
                    label: t("panel.columns.nickname"),
                    render: (row: any) => (
                      <Button tone="default" onClick={() => setSelectedProfile(row)}>
                        {row.nickname || t("panel.audience.anonymousViewer")}
                      </Button>
                    ),
                  },
                  { key: "viewer_stage", label: t("panel.columns.viewerStage"), render: (row: any) => profileBadge("viewerStage", row.viewer_stage, t) },
                  { key: "profile_confidence", label: t("panel.columns.profileConfidence"), render: (row: any) => profileBadge("profileConfidence", row.profile_confidence, t) },
                  { key: "profile_freshness", label: t("panel.columns.profileFreshness"), render: (row: any) => profileBadge("profileFreshness", row.profile_freshness, t) },
                  { key: "last_seen_at", label: t("panel.columns.lastSeen"), render: (row: any) => formatDateTime(row.last_seen_at) },
                ]}
              />
            </div>
          ) : (
            <Text>{t("panel.empty.profiles")}</Text>
          )}
        </Stack>
      </Card>
      <Modal
        open={!!selectedProfile}
        title={t("panel.audience.profileDetailTitle")}
        size="lg"
        onClose={() => setSelectedProfile(null)}
        footer={
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", flexWrap: "wrap" }}>
            <Button tone="default" disabled={actionPending} onClick={() => setSelectedProfile(null)}>{t("panel.actions.cancel")}</Button>
            <Button tone="info" disabled={actionPending} onClick={() => setPendingAction("reset")}>{t("panel.actions.resetViewerImpression")}</Button>
            <Button tone="danger" disabled={actionPending} onClick={() => setPendingAction("delete")}>{t("panel.actions.deleteViewerProfile")}</Button>
          </div>
        }
      >
        {selectedProfile ? (
          <Stack>
            <Text>{selectedProfile.nickname || t("panel.audience.anonymousViewer")}</Text>
            <KeyValue
              items={[
                { key: "stage", label: t("panel.columns.viewerStage"), value: profileLabel("viewerStage", selectedProfile.viewer_stage, t) },
                { key: "confidence", label: t("panel.columns.profileConfidence"), value: profileLabel("profileConfidence", selectedProfile.profile_confidence, t) },
                { key: "freshness", label: t("panel.columns.profileFreshness"), value: profileLabel("profileFreshness", selectedProfile.profile_freshness, t) },
                { key: "danmaku", label: t("panel.columns.danmakuCount"), value: Number(selectedProfile.danmaku_count || 0) },
                { key: "roast", label: t("panel.columns.roastCount"), value: Number(selectedProfile.roast_count || 0) },
                { key: "preferences", label: t("panel.columns.preferenceTags"), value: formatCountedTags(selectedProfile.top_preference_tags) },
                { key: "topics", label: t("panel.columns.favoriteTopics"), value: formatCountedTags(selectedProfile.top_favorite_topics) },
                { key: "jokes", label: t("panel.columns.runningJokes"), value: formatCountedTags(selectedProfile.top_running_jokes) },
                { key: "summary", label: t("panel.columns.latestSummary"), value: selectedProfile.impression_summary || selectedProfile.profile_summary || selectedProfile.last_interaction_summary || "-" },
                { key: "avoid", label: t("panel.columns.avoidGuidance"), value: selectedProfile.avoid_guidance || "-" },
                { key: "reply", label: t("panel.columns.replyGuidance"), value: selectedProfile.reply_guidance || "-" },
                { key: "lastSeen", label: t("panel.columns.lastSeen"), value: formatDateTime(selectedProfile.last_seen_at) },
              ]}
            />
          </Stack>
        ) : null}
      </Modal>
      <ConfirmDialog
        open={!!pendingAction && !!selectedProfile}
        title={t(pendingAction === "delete" ? "panel.actions.deleteViewerProfile" : "panel.actions.resetViewerImpression")}
        message={t(pendingAction === "delete" ? "panel.messages.deleteViewerProfileConfirm" : "panel.messages.resetViewerImpressionConfirm")}
        tone={pendingAction === "delete" ? "danger" : "warning"}
        confirmLabel={t(pendingAction === "delete" ? "panel.actions.deleteViewerProfile" : "panel.actions.resetViewerImpression")}
        cancelLabel={t("panel.actions.cancel")}
        onConfirm={async () => {
          if (!selectedProfile || actionPending) return
          setActionPending(true)
          try {
            if (pendingAction === "delete") await onDeleteProfile(String(selectedProfile.uid || ""))
            else await onResetImpression(String(selectedProfile.uid || ""))
            setPendingAction("")
            setSelectedProfile(null)
          } finally {
            setActionPending(false)
          }
        }}
        onCancel={() => setPendingAction("")}
      />
    </Stack>
  )
}

function formatCountedTags(value: any): string {
  return Array.isArray(value) && value.length
    ? value.map((item: any) => `${item.tag}:${item.count}`).join(", ")
    : "-"
}

function formatDateTime(value: any): string {
  const raw = String(value || "").trim()
  if (!raw) return "-"
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString()
}

function sessionEventLabel(t: PanelTranslator, value: any): string {
  const key = String(value || "unknown")
  const labelKey = `panel.audience.event.${key === "super_chat" ? "superChat" : key}`
  const label = t(labelKey)
  return label && label !== labelKey ? label : t("panel.audience.event.unknown")
}

function sessionEventTone(value: any): "success" | "warning" | "info" | "default" {
  const key = String(value || "")
  if (key === "gift" || key === "super_chat" || key === "guard") return "success"
  if (key === "danmaku") return "info"
  return "default"
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
