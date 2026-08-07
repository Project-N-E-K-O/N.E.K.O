import {
  ActionButton,
  Alert,
  Card,
  Columns,
  DataTable,
  Divider,
  EmptyState,
  Field,
  Inline,
  Input,
  KeyValue,
  Progress,
  Stack,
  StatCard,
  StatusBadge,
  Text,
  Textarea,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction } from "@neko/plugin-ui"

import { formatClock, formatCount } from "./format"
import type { DocumentsState, Translate } from "./types"

export function DocumentsSection(props: {
  documents: DocumentsState
  actions: HostedAction[]
  busy: boolean
  t: Translate
  locale: string
}) {
  const { t } = props
  const documents = props.documents || {}
  const stats = documents.stats || {}
  const quotas = documents.quotas || {}
  const search = documents.last_search || {}
  const items = documents.items || []
  const locale = props.locale.toLowerCase()
  const showBackendReason = locale === "zh"
    || locale.startsWith("zh-cn")
    || locale.startsWith("zh-hans")
    || locale.startsWith("zh-sg")
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const findAction = (id: string) =>
    (props.actions || []).find((action) => action.id === id)

  if (documents.available === false) {
    return (
      <Alert tone="danger">
        {t("documents.unavailable", {
          error: documents.error || t("common.unknownReason"),
        })}
      </Alert>
    )
  }

  return (
    <Stack gap={12}>
      <Text>{t("documents.help")}</Text>

      <Columns minWidth={160} gap={12}>
        <StatCard
          label={t("documents.stats.documents")}
          value={`${formatCount(stats.documents, t)} / ${formatCount(quotas.max_documents, t)}`}
        />
        <StatCard label={t("documents.stats.chunks")} value={formatCount(stats.chunks, t)} />
        <StatCard
          label={t("documents.stats.indexed")}
          value={`${formatCount(stats.indexed_chunks, t)} / ${formatCount(quotas.index_chunk_cap, t)}`}
        />
        <StatCard label={t("documents.stats.bytes")} value={formatBytes(stats.total_bytes, t)} />
      </Columns>

      {documents.index_truncated ? (
        <Alert tone="warning">
          {t("documents.indexTruncated")}
        </Alert>
      ) : null}

      <Card title={t("documents.quotas.title")}>
        <Stack gap={8}>
          <Progress
            label={t("documents.quotas.storage")}
            value={ratio(stats.total_bytes, quotas.max_total_bytes)}
          />
          <Progress
            label={t("documents.quotas.coverage")}
            value={ratio(stats.indexed_chunks, stats.chunks)}
          />
          <KeyValue
            data={{
              [t("documents.quotas.file")]: formatBytes(quotas.max_file_bytes, t),
              [t("documents.quotas.total")]: formatBytes(quotas.max_total_bytes, t),
              [t("documents.quotas.chunk")]: t("documents.quotas.chunkValue", {
                size: formatCount(quotas.chunk_chars, t),
                overlap: formatCount(quotas.chunk_overlap, t),
              }),
              [t("documents.quotas.tagWeight")]: formatCount(quotas.tag_weight, t),
              [t("documents.quotas.minHits")]: formatCount(quotas.min_term_hits, t),
              [t("documents.quotas.postings")]: formatCount(stats.postings, t),
            }}
          />
        </Stack>
      </Card>

      <Card title={t("documents.import.title")}>
        <Stack gap={10}>
          <Inline gap={8} wrap>
            {findAction("pick_documents") ? (
              <ActionButton
                action={findAction("pick_documents")}
                tone="primary"
                refresh
              >
                {t("documents.import.pick")}
              </ActionButton>
            ) : null}
            {findAction("clear_documents") ? (
              <ActionButton
                action={findAction("clear_documents")}
                tone="danger"
                refresh
              >
                {t("documents.import.clear")}
              </ActionButton>
            ) : null}
          </Inline>
          <Text>{t("documents.import.fullscreenHelp")}</Text>
          <Divider />
          <Field label={t("documents.import.titleLabel")}>
            <Input
              value={title}
              onChange={setTitle}
              placeholder={t("documents.import.titlePlaceholder")}
            />
          </Field>
          <Field label={t("documents.import.contentLabel")}>
            <Textarea
              value={content}
              onChange={setContent}
              placeholder={t("documents.import.contentPlaceholder")}
            />
          </Field>
          <Inline gap={8}>
            <ActionButton
              tone="primary"
              refresh
              onResult={() => {
                setTitle("")
                setContent("")
              }}
              actionId="import_document_text"
              values={{ title, content }}
            >
              {t("documents.import.paste")}
            </ActionButton>
          </Inline>
        </Stack>
      </Card>

      <Card title={t("documents.list.title")}>
        {items.length ? (
          <DataTable
            data={items}
            rowKey="doc_id"
            emptyText={t("documents.list.emptyRows")}
            columns={[
              { key: "title", label: t("documents.list.columns.title") },
              {
                key: "chunk_count",
                label: t("documents.list.columns.chunks"),
                render: (row) =>
                  `${formatCount(row.indexed_chunks, t)} / ${formatCount(row.chunk_count, t)}`,
              },
              {
                key: "tags",
                label: t("documents.list.columns.tags"),
                render: (row) => (row.tags || []).join(", ") || t("common.none"),
              },
              {
                key: "size_bytes",
                label: t("documents.list.columns.size"),
                render: (row) => formatBytes(row.size_bytes, t),
              },
              {
                key: "imported_at",
                label: t("documents.list.columns.importedAt"),
                render: (row) => formatClock(row.imported_at, props.locale),
              },
              {
                key: "doc_id",
                label: "",
                render: (row) => (
                  <ActionButton
                    tone="danger"
                    refresh
                    actionId="delete_document"
                    values={{ doc_id: row.doc_id }}
                  >
                    {t("documents.list.delete")}
                  </ActionButton>
                ),
              },
            ]}
          />
        ) : (
          <EmptyState
            title={t("documents.list.empty.title")}
            description={t("documents.list.empty.description")}
          />
        )}
      </Card>

      <Card title={t("documents.search.title")}>
        {search.query_text ? (
          <Stack gap={8}>
            <Inline gap={8} wrap>
              <StatusBadge
                tone={search.gated ? "warning" : "success"}
                label={search.gated
                  ? t("documents.search.gated")
                  : t("documents.search.injected")}
              />
              {(search.tags_used || []).map((tag) => (
                <StatusBadge key={tag} tone="info" label={tag} />
              ))}
            </Inline>
            <KeyValue
              data={{
                [t("documents.search.query")]: search.query_text,
                [t("documents.search.tagCandidates")]: formatCount(search.tag_candidates, t),
                [t("documents.search.termCandidates")]: formatCount(search.term_candidates, t),
                [t("documents.search.bestHits")]: formatCount(search.best_term_hits, t),
                [t("documents.search.scored")]: formatCount(search.scored, t),
              }}
            />
            {search.gated ? (
              <Alert tone="warning">
                {(showBackendReason && search.gate_reason)
                  || t("documents.search.gateFallback")}
              </Alert>
            ) : null}
            {(search.hits || []).length ? (
              <DataTable
                data={search.hits || []}
                columns={[
                  { key: "title", label: t("documents.search.columns.hit") },
                  { key: "score", label: t("documents.search.columns.score") },
                  { key: "tag_hits", label: t("documents.search.columns.tags") },
                  { key: "term_hits", label: t("documents.search.columns.terms") },
                ]}
              />
            ) : null}
          </Stack>
        ) : (
          <Text>{t("documents.search.empty")}</Text>
        )}
      </Card>
    </Stack>
  )
}

function ratio(value?: number, total?: number): number {
  if (!total || !value) return 0
  return Math.min(1, value / total)
}

function formatBytes(value: number | undefined, t: Translate): string {
  if (value === null || value === undefined) return t("common.unknown")
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`
}
