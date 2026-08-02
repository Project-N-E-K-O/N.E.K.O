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
import type { DocumentsState } from "./types"

export function DocumentsSection(props: {
  documents: DocumentsState
  actions: HostedAction[]
  busy: boolean
}) {
  const documents = props.documents || {}
  const stats = documents.stats || {}
  const quotas = documents.quotas || {}
  const search = documents.last_search || {}
  const items = documents.items || []
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const findAction = (id: string) =>
    (props.actions || []).find((action) => action.id === id)

  if (documents.available === false) {
    return (
      <Alert tone="danger">
        {`文档库打不开：${documents.error || "未知原因"}。战术资料是可选的，链路其余部分不受影响。`}
      </Alert>
    )
  }

  return (
    <Stack gap={12}>
      <Text>
        导入的资料只用于措辞参考。它会以「不可信参考资料」的形式围栏注入，既不能补齐
        缺失的遥测数据，也不能覆盖事实。
      </Text>

      <Columns minWidth={160} gap={12}>
        <StatCard
          label="文档"
          value={`${formatCount(stats.documents)} / ${formatCount(quotas.max_documents)}`}
        />
        <StatCard label="分段" value={formatCount(stats.chunks)} />
        <StatCard
          label="已索引分段"
          value={`${formatCount(stats.indexed_chunks)} / ${formatCount(quotas.index_chunk_cap)}`}
        />
        <StatCard label="占用" value={formatBytes(stats.total_bytes)} />
      </Columns>

      {documents.index_truncated ? (
        <Alert tone="warning">
          分段数量已超过排序索引上限，超出部分只能靠 front matter 标签检索，不参与 BM25
          排序。想让全部内容参与排序，可以调高 tactics_index_chunk_cap 或减少导入量。
        </Alert>
      ) : null}

      <Card title="配额">
        <Stack gap={8}>
          <Progress
            label="存储用量"
            value={ratio(stats.total_bytes, quotas.max_total_bytes)}
          />
          <Progress
            label="索引覆盖"
            value={ratio(stats.indexed_chunks, stats.chunks)}
          />
          <KeyValue
            data={{
              单文件上限: formatBytes(quotas.max_file_bytes),
              总量上限: formatBytes(quotas.max_total_bytes),
              分段长度: `${formatCount(quotas.chunk_chars)} 字符，重叠 ${formatCount(quotas.chunk_overlap)}`,
              标签权重: formatCount(quotas.tag_weight),
              最少词命中: formatCount(quotas.min_term_hits),
              倒排条目: formatCount(stats.postings),
            }}
          />
        </Stack>
      </Card>

      <Card title="导入">
        <Stack gap={10}>
          <Inline gap={8} wrap>
            {findAction("pick_documents") ? (
              <ActionButton
                action={findAction("pick_documents")}
                tone="primary"
                refresh
              >
                选择文件导入
              </ActionButton>
            ) : null}
            {findAction("clear_documents") ? (
              <ActionButton
                action={findAction("clear_documents")}
                tone="danger"
                refresh
              >
                清空文档库
              </ActionButton>
            ) : null}
          </Inline>
          <Text>
            全屏游戏下原生文件对话框可能被挡在游戏后面，那就用下面的粘贴方式。
          </Text>
          <Divider />
          <Field label="标题（可留空，会取正文第一个标题）">
            <Input value={title} onChange={setTitle} placeholder="例如：巡洋舰站位" />
          </Field>
          <Field label="正文（Markdown / 文本，可带 front matter）">
            <Textarea
              value={content}
              onChange={setContent}
              placeholder={"---\nmaps: New Dawn\nclasses: Cruiser\n---\n\n开局别急着推线……"}
            />
          </Field>
          <Inline gap={8}>
            <ActionButton
              tone="primary"
              refresh={false}
              onResult={() => {
                setTitle("")
                setContent("")
              }}
              actionId="import_document_text"
              values={{ title, content }}
            >
              粘贴导入
            </ActionButton>
          </Inline>
        </Stack>
      </Card>

      <Card title="已导入">
        {items.length ? (
          <DataTable
            data={items}
            rowKey="doc_id"
            emptyText="还没有导入任何资料"
            columns={[
              { key: "title", label: "标题" },
              {
                key: "chunk_count",
                label: "分段",
                render: (row) =>
                  `${formatCount(row.indexed_chunks)} / ${formatCount(row.chunk_count)}`,
              },
              {
                key: "tags",
                label: "标签",
                render: (row) => (row.tags || []).join("、") || "无",
              },
              {
                key: "size_bytes",
                label: "大小",
                render: (row) => formatBytes(row.size_bytes),
              },
              {
                key: "imported_at",
                label: "导入时间",
                render: (row) => formatClock(row.imported_at),
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
                    删除
                  </ActionButton>
                ),
              },
            ]}
          />
        ) : (
          <EmptyState
            title="文档库是空的"
            description="没有资料时检索直接返回空，链路照常跑，只是不注入参考文本。"
          />
        )}
      </Card>

      <Card title="最近一次检索">
        {search.query_text ? (
          <Stack gap={8}>
            <Inline gap={8} wrap>
              <StatusBadge
                tone={search.gated ? "warning" : "success"}
                label={search.gated ? "未注入" : "已注入"}
              />
              {(search.tags_used || []).map((tag) => (
                <StatusBadge key={tag} tone="info" label={tag} />
              ))}
            </Inline>
            <KeyValue
              data={{
                查询: search.query_text,
                标签候选: formatCount(search.tag_candidates),
                词项候选: formatCount(search.term_candidates),
                最多词命中: formatCount(search.best_term_hits),
                参与排序: formatCount(search.scored),
              }}
            />
            {search.gated ? (
              <Alert tone="warning">{search.gate_reason || "未通过注入门"}</Alert>
            ) : null}
            {(search.hits || []).length ? (
              <DataTable
                data={search.hits || []}
                columns={[
                  { key: "title", label: "命中" },
                  { key: "score", label: "得分" },
                  { key: "tag_hits", label: "标签命中" },
                  { key: "term_hits", label: "词命中" },
                ]}
              />
            ) : null}
          </Stack>
        ) : (
          <Text>还没有发生过检索。有播报被选中时这里会显示命中项与得分。</Text>
        )}
      </Card>
    </Stack>
  )
}

function ratio(value?: number, total?: number): number {
  if (!total || !value) return 0
  return Math.min(1, value / total)
}

function formatBytes(value?: number): string {
  if (value === null || value === undefined) return "未知"
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`
}
