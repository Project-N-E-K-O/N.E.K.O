import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Alert,
  StatCard,
  StatusBadge,
  DataTable,
  Button,
  ButtonGroup,
  Field,
  Input,
  Select,
  Textarea,
  RefreshButton,
  KeyValue,
  InlineError,
  useEffect,
  useForm,
  useToast,
  useConfirm,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type EnvironmentReport = {
  ready?: boolean
  recommended_method_ready?: boolean
  python_packages?: Record<string, boolean>
  models?: Record<string, boolean>
  devices?: Record<string, boolean>
  warnings?: string[]
}

type LayerArtifact = {
  name?: string
  path?: string
  width?: number
  height?: number
  area?: number
}

type ProcessResult = {
  session_id?: string
  status?: string
  message?: string
  output_dir?: string
  preview_path?: string
  zip_path?: string
  manifest_path?: string
  layers?: LayerArtifact[]
  warnings?: string[]
}

type DashboardState = {
  environment?: EnvironmentReport
  sessions?: ProcessResult[]
  session_count?: number
  default_method?: string
  output_dir?: string
}

const defaultForm = {
  input_path: "",
  session_id: "",
  method: "anime_face",
  parts: "Body, Face_Skin, Hair, Eye_L, Eye_R, Mouth, Eyebrow_L, Eyebrow_R",
  feather_radius: "2",
  gpt_api_key: "",
}

function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

function boolBadge(value: boolean | undefined, okLabel: string, badLabel: string) {
  return <StatusBadge tone={value ? "success" : "warning"} label={value ? okLabel : badLabel} />
}

function splitParts(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
}

function pathValue(path: string | undefined): string {
  return path && path.trim() ? path : "-"
}

export default function Live2dAutoLayerPanel(props: PluginSurfaceProps<DashboardState>) {
  const { state, t } = props
  const safeState = state || {}
  const env = safeState.environment || {}
  const sessions = Array.isArray(safeState.sessions) ? safeState.sessions : []
  const form = useForm(defaultForm)
  const toast = useToast()
  const confirm = useConfirm()
  const [busy, setBusy] = props.useLocalState("busy", false)
  const [lastResult, setLastResult] = props.useLocalState<ProcessResult | null>("lastResult", null)
  const [error, setError] = props.useLocalState("error", "")

  useEffect(() => {
    form.setField("method", String(safeState.default_method || "anime_face"))
  }, [safeState.default_method])

  async function runSplit() {
    const inputPath = form.values.input_path.trim()
    if (!inputPath) {
      toast.error(t("panel.errors.inputRequired"))
      return
    }
    setBusy(true)
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_split_image",
        {
          input_path: inputPath,
          session_id: form.values.session_id.trim(),
          method: form.values.method,
          parts: splitParts(form.values.parts),
          feather_radius: Number(form.values.feather_radius) || 2,
          gpt_api_key: form.values.gpt_api_key.trim(),
        },
        { timeoutMs: 900000 },
      )
      const result = unwrapActionResult(envelope) as ProcessResult
      setLastResult(result)
      await props.api.refresh()
      toast.success(result.message || t("panel.messages.splitDone"))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
    }
  }

  async function checkEnvironment() {
    setError("")
    try {
      await props.api.call("env_check_environment")
      await props.api.refresh()
      toast.success(t("panel.messages.environmentChecked"))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    }
  }

  async function deleteSession(row: ProcessResult) {
    const sessionId = String(row.session_id || "").trim()
    if (!sessionId) return
    const ok = await confirm({
      title: t("panel.actions.delete"),
      message: t("panel.confirm.deleteSession"),
      tone: "danger",
      confirmLabel: t("panel.actions.delete"),
      cancelLabel: t("panel.actions.cancel"),
    })
    if (!ok) return
    try {
      await props.api.call("live2d_delete_session", { session_id: sessionId })
      if (lastResult?.session_id === sessionId) setLastResult(null)
      await props.api.refresh()
      toast.success(t("panel.messages.sessionDeleted"))
    } catch (exc: any) {
      toast.error(String(exc?.message || exc))
    }
  }

  const packages = env.python_packages || {}
  const models = env.models || {}
  const devices = env.devices || {}
  const warnings = Array.isArray(env.warnings) ? env.warnings : []
  const result = lastResult || sessions[0] || null
  const layers = Array.isArray(result?.layers) ? result.layers || [] : []

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Grid cols={4}>
        <StatCard label={t("panel.stats.pipeline")} value={boolBadge(env.ready, t("panel.status.ready"), t("panel.status.incomplete"))} />
        <StatCard label={t("panel.stats.recommended")} value={boolBadge(env.recommended_method_ready, t("panel.status.ready"), t("panel.status.fallback"))} />
        <StatCard label={t("panel.stats.sessions")} value={String(safeState.session_count || sessions.length)} />
        <StatCard label={t("panel.stats.device")} value={devices.cuda ? "CUDA" : devices.mps ? "MPS" : "CPU"} />
      </Grid>

      {error ? <InlineError title={t("panel.errors.title")} message={error} /> : null}
      {warnings.length ? <Alert tone="warning">{warnings.join("\n")}</Alert> : null}

      <Grid cols={2}>
        <Card title={t("panel.environment.title")}>
          <Stack>
            <ButtonGroup>
              <Button tone="primary" onClick={checkEnvironment}>
                {t("panel.actions.checkEnvironment")}
              </Button>
              <RefreshButton label={t("panel.actions.refresh")} />
            </ButtonGroup>
            <KeyValue
              items={[
                { key: "pil", label: "PIL", value: packages.PIL ? t("panel.status.ready") : t("panel.status.missing") },
                { key: "rembg", label: "rembg", value: packages.rembg ? t("panel.status.ready") : t("panel.status.missing") },
                { key: "onnxruntime", label: "onnxruntime", value: packages.onnxruntime ? t("panel.status.ready") : t("panel.status.missing") },
                { key: "torch", label: "torch", value: packages.torch ? t("panel.status.ready") : t("panel.status.missing") },
                { key: "segment_anything", label: "segment_anything", value: packages.segment_anything ? t("panel.status.ready") : t("panel.status.missing") },
                { key: "sam", label: "SAM vit_b", value: models.sam_vit_b ? t("panel.status.ready") : t("panel.status.missing") },
              ]}
            />
          </Stack>
        </Card>

        <Card title={t("panel.process.title")}>
          <Stack>
            <Field label={t("panel.fields.inputPath")} required>
              <Input
                value={form.values.input_path}
                placeholder="/path/to/character.png"
                onChange={(value) => form.setField("input_path", value)}
              />
            </Field>
            <Field label={t("panel.fields.sessionId")}>
              <Input
                value={form.values.session_id}
                placeholder="optional-session-id"
                onChange={(value) => form.setField("session_id", value)}
              />
            </Field>
            <Grid cols={2}>
              <Field label={t("panel.fields.method")}>
                <Select
                  value={form.values.method}
                  options={[
                    { value: "anime_face", label: t("panel.methods.animeFace") },
                    { value: "grounded_sam", label: t("panel.methods.groundedSam") },
                    { value: "color", label: t("panel.methods.color") },
                  ]}
                  onChange={(value) => form.setField("method", String(value))}
                />
              </Field>
              <Field label={t("panel.fields.feather")}>
                <Input
                  value={form.values.feather_radius}
                  placeholder="2"
                  onChange={(value) => form.setField("feather_radius", value)}
                />
              </Field>
            </Grid>
            <Field label={t("panel.fields.parts")}>
              <Textarea value={form.values.parts} onChange={(value) => form.setField("parts", value)} />
            </Field>
            <Field label={t("panel.fields.gptKey")}>
              <Input value={form.values.gpt_api_key} placeholder="optional" onChange={(value) => form.setField("gpt_api_key", value)} />
            </Field>
            <Button tone="success" disabled={busy || !env.ready} onClick={runSplit}>
              {busy ? t("panel.actions.running") : t("panel.actions.split")}
            </Button>
          </Stack>
        </Card>
      </Grid>

      <Card title={t("panel.result.title")}>
        {result ? (
          <Stack>
            <KeyValue
              items={[
                { key: "session", label: t("panel.result.session"), value: result.session_id || "-" },
                { key: "message", label: t("panel.result.message"), value: result.message || "-" },
                { key: "preview", label: t("panel.result.preview"), value: pathValue(result.preview_path) },
                { key: "zip", label: t("panel.result.zip"), value: pathValue(result.zip_path) },
                { key: "manifest", label: t("panel.result.manifest"), value: pathValue(result.manifest_path) },
              ]}
            />
            <DataTable
              data={layers.map((layer, index) => ({ ...layer, id: layer.name || String(index) }))}
              rowKey="id"
              emptyText={t("panel.layers.empty")}
              columns={[
                { key: "name", label: t("panel.layers.name") },
                { key: "size", label: t("panel.layers.size"), render: (row) => `${row.width || 0} x ${row.height || 0}` },
                { key: "area", label: t("panel.layers.area"), render: (row) => String(row.area || 0) },
                { key: "path", label: t("panel.layers.path"), render: (row) => pathValue(row.path) },
              ]}
            />
          </Stack>
        ) : (
          <Text>{t("panel.result.empty")}</Text>
        )}
      </Card>

      <Card title={t("panel.sessions.title")}>
        <DataTable
          data={sessions.map((session) => ({ ...session, id: session.session_id || session.manifest_path || "" }))}
          rowKey="id"
          emptyText={t("panel.sessions.empty")}
          columns={[
            { key: "session_id", label: t("panel.sessions.session") },
            { key: "layers", label: t("panel.sessions.layers"), render: (row) => String(Array.isArray(row.layers) ? row.layers.length : 0) },
            { key: "zip_path", label: t("panel.sessions.zip"), render: (row) => pathValue(row.zip_path) },
            {
              key: "actions",
              label: t("panel.sessions.actions"),
              render: (row) => (
                <Button tone="danger" onClick={() => deleteSession(row)}>
                  {t("panel.actions.delete")}
                </Button>
              ),
            },
          ]}
        />
      </Card>

      <Text>{t("panel.outputDir")}: {safeState.output_dir || "-"}</Text>
    </Page>
  )
}
