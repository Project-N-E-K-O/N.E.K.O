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
import type { PromptsState, Translate } from "./types"

export function PromptsSection(props: {
  prompts: PromptsState
  actions: HostedAction[]
  busy: boolean
  t: Translate
  locale: string
}) {
  const { t } = props
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
              ? t("prompts.current.builtin")
              : t("prompts.current.revision", {
                  revision: prompts.active_revision || "—",
                })
          }
        />
        <StatusBadge
          tone="default"
          label={t("prompts.current.kept", {
            count: formatCount(prompts.revisions_kept, t),
          })}
        />
      </Inline>

      <Text>{t("prompts.help")}</Text>

      <Card title={t("prompts.editor.title")}>
        <Stack gap={10}>
          <Field
            label={`base（${draft.base.length} / ${limit}）`}
            error={tooLong(draft.base) ? t("prompts.editor.tooLong") : undefined}
          >
            <Textarea
              value={draft.base}
              invalid={tooLong(draft.base)}
              onChange={(value) => setDraft({ ...draft, base: value })}
            />
          </Field>
          <Field
            label={`urgent overlay（${draft.urgent.length} / ${limit}）`}
            error={tooLong(draft.urgent) ? t("prompts.editor.tooLong") : undefined}
          >
            <Textarea
              value={draft.urgent}
              invalid={tooLong(draft.urgent)}
              onChange={(value) => setDraft({ ...draft, urgent: value })}
            />
          </Field>
          <Field
            label={`normal overlay（${draft.normal.length} / ${limit}）`}
            error={tooLong(draft.normal) ? t("prompts.editor.tooLong") : undefined}
          >
            <Textarea
              value={draft.normal}
              invalid={tooLong(draft.normal)}
              onChange={(value) => setDraft({ ...draft, normal: value })}
            />
          </Field>
          <Field label={t("prompts.editor.note")}>
            <Textarea value={note} onChange={setNote} />
          </Field>

          {invalid ? (
            <Alert tone="warning">{t("prompts.editor.invalid")}</Alert>
          ) : null}

          <Inline gap={8} wrap>
            <ActionButton
              tone="primary"
              refresh
              actionId="save_prompt_revision"
              values={{ ...draft, note }}
            >
              {t("prompts.editor.save")}
            </ActionButton>
            {findAction("reset_prompts") ? (
              <ActionButton action={findAction("reset_prompts")} tone="warning" refresh>
                {t("prompts.editor.reset")}
              </ActionButton>
            ) : null}
          </Inline>
        </Stack>
      </Card>

      <Card title={t("prompts.preview.title")}>
        <Stack gap={10}>
          <Text>{t("prompts.preview.help")}</Text>
          <SegmentedControl
            value={lane}
            disabled={props.busy}
            options={[
              { value: "urgent", label: t("prompts.preview.urgent") },
              { value: "normal", label: t("prompts.preview.normal") },
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
              {t("prompts.preview.generate")}
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

      <Card title={t("prompts.history.title")}>
        {(prompts.revisions || []).length ? (
          <DataTable
            data={prompts.revisions || []}
            rowKey="revision_id"
            columns={[
              {
                key: "revision_id",
                label: t("prompts.history.version"),
                render: (row) =>
                  row.active
                    ? t("prompts.history.activeRevision", {
                        revision: row.revision_id,
                      })
                    : row.revision_id,
              },
              {
                key: "created_at",
                label: t("prompts.history.savedAt"),
                render: (row) => formatClock(row.created_at, props.locale),
              },
              {
                key: "lengths",
                label: t("prompts.history.lengths"),
                render: (row) =>
                  `${row.lengths?.base ?? 0} / ${row.lengths?.urgent ?? 0} / ${row.lengths?.normal ?? 0}`,
              },
              {
                key: "note",
                label: t("prompts.history.note"),
                render: (row) => row.note || "—",
              },
              {
                key: "active",
                label: "",
                render: (row) =>
                  row.active ? (
                    <Text>{t("prompts.history.current")}</Text>
                  ) : (
                    <ActionButton
                      tone="info"
                      refresh
                      actionId="activate_prompt_revision"
                      values={{ revision_id: row.revision_id }}
                    >
                      {t("prompts.history.rollback")}
                    </ActionButton>
                  ),
              },
            ]}
          />
        ) : (
          <Stack gap={6}>
            <Text>{t("prompts.history.empty")}</Text>
            <KeyValue
              data={{
                [t("prompts.history.effective")]: prompts.active_revision
                  || t("prompts.current.builtin"),
              }}
            />
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
