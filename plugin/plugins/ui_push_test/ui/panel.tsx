import { Alert, Button, Card, Field, Grid, Inline, Input, JsonView, Page, Stack, Text, Textarea, useState } from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type TargetState = { target_lanlan: string; chat_id: string; agent_id: string; chat_updates: number; agent_updates: number }
type TestState = { last_target?: string; targets?: TargetState[]; events?: Array<Record<string, unknown>> }

export default function UiPushTestPanel(props: PluginSurfaceProps<TestState>) {
  const { t } = props
  const [target, setTarget] = props.useLocalState("target", props.state?.last_target || "")
  const [title, setTitle] = props.useLocalState("title", t("content.title"))
  const [html, setHtml] = props.useLocalState("html", `<p>${t("content.sample")}</p>`)
  const [css, setCss] = props.useLocalState("css", ".push-test-body { margin: 12px 0; } button { padding: 8px 12px; }")
  const [summary, setSummary] = props.useLocalState("summary", t("content.sample"))
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null)
  const current = (props.state?.targets || []).find(item => item.target_lanlan === target.trim())
  const exposed = (id: string) => (props.actions || []).some(action => action.id === id || action.entry_id === id)
  const canRun = (id: string) => !busy && !!target.trim() && exposed(id)

  async function run(id: string) {
    setBusy(id)
    setError("")
    try {
      if (id === "refresh") {
        await props.api.refresh()
        return
      }
      const args: Record<string, unknown> = { target_lanlan: target.trim(), locale: props.locale }
      if (id !== "agent_close") { args.title = title; args.css = css }
      if (id === "chat_push" || id === "agent_push") { args.html = html; args.summary = summary }
      const response = await props.api.call(id, args)
      setReceipt(response?.result || response)
      // Keep a successful submission receipt even if the subsequent state read fails.
      await props.api.refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy("")
    }
  }

  return <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
    <Stack>
      <Alert tone="info">{t("panel.instructions")}</Alert>
      {!exposed("chat_create") ? <Alert tone="warning">{t("panel.startPlugin")}</Alert> : null}
      <Card title={t("section.input")}>
        <Stack>
          <Field label={t("field.target")} help={t("field.targetHelp")} required>
            <Input value={target} onChange={setTarget} placeholder={t("field.targetPlaceholder")} />
          </Field>
          <Field label={t("field.title")}><Input value={title} onChange={setTitle} /></Field>
          <Field label={t("field.html")} help={t("field.htmlHelp")}>
            <Textarea value={html} onChange={setHtml} />
          </Field>
          <Field label="CSS"><Textarea value={css} onChange={setCss} /></Field>
          <Field label={t("field.summary")}><Input value={summary} onChange={setSummary} /></Field>
        </Stack>
      </Card>
      <Grid cols={2}>
        <Card title={t("section.chat")}>
          <Stack>
            <Text>{t("state.id")}: {current?.chat_id || "—"}</Text>
            <Text>{t("state.updates")}: {current?.chat_updates || 0}</Text>
            <Inline wrap>
              <Button disabled={!canRun("chat_create")} onClick={() => run("chat_create")}>{t("action.chatCreate")}</Button>
              <Button disabled={!canRun("chat_push") || !current?.chat_id} onClick={() => run("chat_push")}>{t("action.chatPush")}</Button>
            </Inline>
          </Stack>
        </Card>
        <Card title={t("section.agent")}>
          <Stack>
            <Text>{t("state.id")}: {current?.agent_id || "—"}</Text>
            <Text>{t("state.updates")}: {current?.agent_updates || 0}</Text>
            <Inline wrap>
              <Button disabled={!canRun("agent_create")} onClick={() => run("agent_create")}>{t("action.agentCreate")}</Button>
              <Button disabled={!canRun("agent_push") || !current?.agent_id} onClick={() => run("agent_push")}>{t("action.agentPush")}</Button>
              <Button tone="warning" disabled={!canRun("agent_close") || !current?.agent_id} onClick={() => run("agent_close")}>{t("action.agentClose")}</Button>
            </Inline>
          </Stack>
        </Card>
      </Grid>
      {busy ? <Text>{t("state.busy")}</Text> : null}
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <Card title={t("section.receipt")}>
        <Stack>
          <Text>{t("state.receiptHelp")}</Text>
          <JsonView data={receipt || {}} />
        </Stack>
      </Card>
      <Card title={t("section.events")}>
        <Stack>
          <Text>{t("state.eventsHelp")}</Text>
          <Button disabled={!!busy} onClick={() => run("refresh")}>{t("action.refresh")}</Button>
          <JsonView data={props.state?.events || []} />
        </Stack>
      </Card>
    </Stack>
  </Page>
}
