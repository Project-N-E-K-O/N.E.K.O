import {
  ActionButton,
  Alert,
  Card,
  DataTable,
  Divider,
  Field,
  Inline,
  KeyValue,
  SegmentedControl,
  Stack,
  StatusBadge,
  Text,
  TextBlock,
  Textarea,
  useEffect,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction } from "@neko/plugin-ui"

import { formatClock, formatCount } from "./format"
import type { PromptsState } from "./types"

export function PromptsSection(props: {
  prompts: PromptsState
  actions: HostedAction[]
  busy: boolean
}) {
  const prompts = props.prompts || {}
  const sections = prompts.sections || {}
  const limit = prompts.max_section_chars || 8000

  const [draft, setDraft] = useState<Draft>({
    base: sections.base || "",
    urgent: sections.urgent || "",
    normal: sections.normal || "",
  })
  const [lane, setLane] = useState("urgent")
  const [preview, setPreview] = useState("")
  const [note, setNote] = useState("")

  // Adopt the active revision whenever it changes underneath us, so a rollback
  // made elsewhere does not leave a stale draft on screen.
  useEffect(() => {
    setDraft({
      base: sections.base || "",
      urgent: sections.urgent || "",
      normal: sections.normal || "",
    })
  }, [prompts.active_revision])

  const tooLong = (value: string) => value.length > limit
  const invalid =
    !draft.base.trim() ||
    !draft.urgent.trim() ||
    !draft.normal.trim() ||
    tooLong(draft.base) ||
    tooLong(draft.urgent) ||
    tooLong(draft.normal)

  const findAction = (id: string) =>
    (props.actions || []).find((action) => action.id === id)

  return (
    <Stack gap={12}>
      <Inline gap={8} wrap>
        <StatusBadge
          tone={prompts.is_builtin ? "info" : "success"}
          label={
            prompts.is_builtin
              ? "当前使用内置提示词"
              : `当前版本 ${prompts.active_revision}`
          }
        />
        <StatusBadge
          tone="default"
          label={`保留最近 ${formatCount(prompts.revisions_kept)} 版`}
        />
      </Inline>

      <Text>
        通道模式决定哪几段被拼起来：dual 用 base + 对应 overlay，single 只用 base。
        换版本只影响措辞，不动优先级、TTL 与抢占规则。
      </Text>

      <Card title="三段编辑器">
        <Stack gap={10}>
          <Field
            label={`base（${draft.base.length} / ${limit}）`}
            error={tooLong(draft.base) ? "超过上限" : undefined}
          >
            <Textarea
              value={draft.base}
              invalid={tooLong(draft.base)}
              onChange={(value) => setDraft({ ...draft, base: value })}
            />
          </Field>
          <Field
            label={`urgent overlay（${draft.urgent.length} / ${limit}）`}
            error={tooLong(draft.urgent) ? "超过上限" : undefined}
          >
            <Textarea
              value={draft.urgent}
              invalid={tooLong(draft.urgent)}
              onChange={(value) => setDraft({ ...draft, urgent: value })}
            />
          </Field>
          <Field
            label={`normal overlay（${draft.normal.length} / ${limit}）`}
            error={tooLong(draft.normal) ? "超过上限" : undefined}
          >
            <Textarea
              value={draft.normal}
              invalid={tooLong(draft.normal)}
              onChange={(value) => setDraft({ ...draft, normal: value })}
            />
          </Field>
          <Field label="备注（可选）">
            <Textarea value={note} onChange={setNote} />
          </Field>

          {invalid ? (
            <Alert tone="warning">
              三段都不能为空，且都不能超过上限。校验是整包的：任何一段不合格就整包拒绝，
              当前生效的版本不会被改动。
            </Alert>
          ) : null}

          <Inline gap={8} wrap>
            <ActionButton
              tone="primary"
              refresh
              actionId="save_prompt_revision"
              values={{ ...draft, note }}
            >
              保存并启用
            </ActionButton>
            {findAction("reset_prompts") ? (
              <ActionButton action={findAction("reset_prompts")} tone="warning" refresh>
                恢复内置提示词
              </ActionButton>
            ) : null}
          </Inline>
        </Stack>
      </Card>

      <Card title="本地预览">
        <Stack gap={10}>
          <Text>
            用最近一次真实候选（没有就用内置样例）组装完整提示词。预览只在本地拼装，
            不经过投递，也不会进入消息流。
          </Text>
          <SegmentedControl
            value={lane}
            disabled={props.busy}
            options={[
              { value: "urgent", label: "紧急" },
              { value: "normal", label: "常规" },
            ]}
            onChange={(value) => setLane(String(value))}
          />
          <Inline gap={8}>
            <ActionButton
              tone="info"
              refresh={false}
              onResult={(envelope) =>
                setPreview(String(unwrapActionResult(envelope).text || ""))
              }
              actionId="preview_prompt"
              values={{ ...draft, lane }}
            >
              生成预览
            </ActionButton>
          </Inline>
          {preview ? (
            <Stack gap={6}>
              <Divider />
              <TextBlock text={preview} />
            </Stack>
          ) : null}
        </Stack>
      </Card>

      <Card title="版本历史">
        {(prompts.revisions || []).length ? (
          <DataTable
            data={prompts.revisions || []}
            rowKey="revision_id"
            columns={[
              {
                key: "revision_id",
                label: "版本",
                render: (row) =>
                  row.active ? `${row.revision_id}（生效）` : row.revision_id,
              },
              {
                key: "created_at",
                label: "保存时间",
                render: (row) => formatClock(row.created_at),
              },
              {
                key: "lengths",
                label: "字符数",
                render: (row) =>
                  `${row.lengths?.base ?? 0} / ${row.lengths?.urgent ?? 0} / ${row.lengths?.normal ?? 0}`,
              },
              { key: "note", label: "备注", render: (row) => row.note || "—" },
              {
                key: "active",
                label: "",
                render: (row) =>
                  row.active ? (
                    <Text>当前</Text>
                  ) : (
                    <ActionButton
                      tone="info"
                      refresh
                      actionId="activate_prompt_revision"
                      values={{ revision_id: row.revision_id }}
                    >
                      回滚到该版本
                    </ActionButton>
                  ),
              },
            ]}
          />
        ) : (
          <Stack gap={6}>
            <Text>还没有自定义版本，当前跑的是内置提示词。</Text>
            <KeyValue data={{ 生效版本: prompts.active_revision || "builtin" }} />
          </Stack>
        )}
      </Card>
    </Stack>
  )
}

type Draft = {
  base: string
  urgent: string
  normal: string
}

/** Hosted actions hand back `Ok(payload)` with the payload under `result`. */
function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}
