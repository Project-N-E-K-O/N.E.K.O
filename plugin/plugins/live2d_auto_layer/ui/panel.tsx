import {
  Page,
  Card,
  Stack,
  Text,
  Alert,
  StatusBadge,
  DataTable,
  Button,
  ButtonGroup,
  Field,
  Input,
  PasswordInput,
  Slider,
  SegmentedControl,
  CheckboxGroup,
  Accordion,
  Markdown,
  ImageUpload,
  ImagePreview,
  Gallery,
  FileDownload,
  RefreshButton,
  KeyValue,
  InlineError,
  Progress,
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
  preview_data_url?: string
}

type ProcessResult = {
  session_id?: string
  status?: string
  message?: string
  output_dir?: string
  preview_path?: string
  preview_data_url?: string
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

const partOptions = [
  { value: "Face_Skin", label: "Face_Skin" },
  { value: "Hair", label: "Hair" },
  { value: "Body", label: "Body" },
  { value: "Eye_L", label: "Eye_L" },
  { value: "Eye_R", label: "Eye_R" },
  { value: "Mouth", label: "Mouth" },
  { value: "Eyebrow_L", label: "Eyebrow_L" },
  { value: "Eyebrow_R", label: "Eyebrow_R" },
  { value: "Nose", label: "Nose" },
]

const defaultForm = {
  input_path: "",
  input_data_url: "",
  session_id: "",
  method: "anime_face",
  parts: ["Face_Skin", "Eye_L", "Eye_R", "Mouth", "Eyebrow_L", "Eyebrow_R", "Hair", "Body"],
  feather_radius: 2,
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

function pathValue(path: string | undefined): string {
  return path && path.trim() ? path : "-"
}

function artifactUrl(pluginId: string, path: string | undefined): string {
  if (!path || !path.trim()) return ""
  return `/plugin/${encodeURIComponent(pluginId)}/hosted-ui/artifact?path=${encodeURIComponent(path)}`
}

export default function Live2dAutoLayerPanel(props: PluginSurfaceProps<DashboardState>) {
  const { state, t } = props
  const pluginId = String(props.plugin?.id || props.plugin?.plugin_id || "live2d_auto_layer")
  const safeState = state || {}
  const env = safeState.environment || {}
  const sessions = Array.isArray(safeState.sessions) ? safeState.sessions : []
  const form = useForm(defaultForm)
  const toast = useToast()
  const confirm = useConfirm()
  const [busy, setBusy] = props.useLocalState("busy", false)
  const [progressText, setProgressText] = props.useLocalState("progressText", "")
  const [lastResult, setLastResult] = props.useLocalState<ProcessResult | null>("lastResult", null)
  const [selectedLayer, setSelectedLayer] = props.useLocalState<LayerArtifact | null>("selectedLayer", null)
  const [error, setError] = props.useLocalState("error", "")
  const [checkingEnv, setCheckingEnv] = props.useLocalState("checkingEnv", false)
  const [autoEnvCheckRequested, setAutoEnvCheckRequested] = props.useLocalState("autoEnvCheckRequested", false)

  useEffect(() => {
    form.setField("method", String(safeState.default_method || "anime_face"))
  }, [safeState.default_method])

  async function runSplit() {
    const inputPath = form.values.input_path.trim()
    const inputDataUrl = form.values.input_data_url.trim()
    const method = String(form.values.method || "anime_face")
    if (!inputPath && !inputDataUrl) {
      toast.error(t("panel.errors.inputRequired"))
      return
    }
    if (!form.values.parts.length) {
      toast.error(t("panel.errors.partsRequired"))
      return
    }
    if (method === "anime_face" && env.recommended_method_ready === false) {
      const message = t("panel.errors.recommendedNotReady")
      setError(message)
      toast.error(message)
      return
    }
    if (method === "color" && env.ready === false) {
      const message = t("panel.errors.colorNotReady")
      setError(message)
      toast.error(message)
      return
    }
    setBusy(true)
    setProgressText(t("panel.messages.running"))
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_split_image",
        {
          input_path: inputPath,
          input_data_url: inputDataUrl,
          session_id: form.values.session_id.trim(),
          method: form.values.method,
          parts: form.values.parts,
          feather_radius: Number(form.values.feather_radius) || 2,
          gpt_api_key: form.values.gpt_api_key.trim(),
        },
        { timeoutMs: 900000 },
      )
      const result = unwrapActionResult(envelope) as ProcessResult
      setLastResult(result)
      setSelectedLayer(null)
      await props.api.refresh()
      toast.success(result.message || t("panel.messages.splitDone"))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runResegment() {
    const sessionId = String((lastResult || sessions[0] || {}).session_id || "").trim()
    if (!sessionId) {
      toast.error(t("panel.errors.sessionRequired"))
      return
    }
    if (!form.values.parts.length) {
      toast.error(t("panel.errors.partsRequired"))
      return
    }
    setBusy(true)
    setProgressText(t("panel.messages.resegmenting"))
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_resegment_session",
        {
          session_id: sessionId,
          method: form.values.method,
          parts: form.values.parts,
          feather_radius: Number(form.values.feather_radius) || 2,
          gpt_api_key: form.values.gpt_api_key.trim(),
        },
        { timeoutMs: 900000 },
      )
      const result = unwrapActionResult(envelope) as ProcessResult
      setLastResult(result)
      setSelectedLayer(null)
      await props.api.refresh()
      toast.success(result.message || t("panel.messages.resegmentDone"))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function checkEnvironment(options?: { silent?: boolean }) {
    setError("")
    setCheckingEnv(true)
    try {
      await props.api.call("env_check_environment")
      await props.api.refresh()
      if (!options?.silent) {
        toast.success(t("panel.messages.environmentChecked"))
      }
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      if (!options?.silent) {
        toast.error(message)
      }
    } finally {
      setCheckingEnv(false)
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
      if (lastResult?.session_id === sessionId) {
        setLastResult(null)
        setSelectedLayer(null)
      }
      await props.api.refresh()
      toast.success(t("panel.messages.sessionDeleted"))
    } catch (exc: any) {
      toast.error(String(exc?.message || exc))
    }
  }

  async function openSession(row: ProcessResult) {
    const sessionId = String(row.session_id || "").trim()
    if (!sessionId) return
    setBusy(true)
    setProgressText(t("panel.messages.loadingSession"))
    setError("")
    try {
      const envelope = await props.api.call("live2d_get_session", { session_id: sessionId }, { timeoutMs: 60000 })
      const result = unwrapActionResult(envelope) as ProcessResult
      setLastResult(result)
      setSelectedLayer(null)
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  const packages = env.python_packages || {}
  const models = env.models || {}
  const devices = env.devices || {}
  const warnings = Array.isArray(env.warnings) ? env.warnings : []
  const environmentKnown = typeof env.ready === "boolean" || typeof env.recommended_method_ready === "boolean"
  const result = lastResult || sessions[0] || null
  const layers = Array.isArray(result?.layers) ? result.layers || [] : []
  const galleryItems = layers.map((layer, index) => ({
    ...layer,
    id: layer.name || String(index),
    src: layer.preview_data_url || "",
    label: layer.name || String(index + 1),
  }))
  const previewSrc = selectedLayer?.preview_data_url || result?.preview_data_url || ""
  const sessionCount = String(safeState.session_count || sessions.length)
  const deviceLabel = devices.cuda ? "CUDA" : devices.mps ? "MPS" : "CPU"

  useEffect(() => {
    if (environmentKnown || autoEnvCheckRequested || checkingEnv) return
    setAutoEnvCheckRequested(true)
    void checkEnvironment({ silent: true })
  }, [environmentKnown, autoEnvCheckRequested, checkingEnv])

  return (
    <Page className="lal-page">
      <div className="lal-toolbar">
        <div className="lal-title">
          <strong>{t("panel.title")}</strong>
          <span>{t("panel.subtitle")}</span>
        </div>
        <div className="lal-status">
          {boolBadge(env.ready, t("panel.status.ready"), t("panel.status.incomplete"))}
          {boolBadge(env.recommended_method_ready, "SAM", t("panel.status.fallback"))}
          <span>{t("panel.stats.sessions")}: {sessionCount}</span>
          <span>{deviceLabel}</span>
        </div>
        <ButtonGroup className="lal-toolbar-actions">
          <Button tone="primary" disabled={checkingEnv} onClick={() => checkEnvironment()}>
            {checkingEnv ? t("panel.actions.checkingEnvironment") : t("panel.actions.checkEnvironment")}
          </Button>
          <RefreshButton label={t("panel.actions.refresh")} />
        </ButtonGroup>
      </div>

      <div className="lal-notices">
        {error ? <InlineError title={t("panel.errors.title")} message={error} /> : null}
        {warnings.length ? <Alert tone="warning">{warnings.join("\n")}</Alert> : null}
        {busy ? <Progress label={progressText || t("panel.messages.running")} indeterminate /> : null}
      </div>

      <div className="lal-workbench">
        <Card className="lal-input-panel" title={t("panel.process.title")}>
          <Stack className="lal-control-stack" gap={8}>
            <Field className="lal-field" label={t("panel.fields.upload")} help={t("panel.help.upload")}>
              <ImageUpload
                className="lal-upload"
                value={form.values.input_data_url}
                label={t("panel.fields.upload")}
                placeholder={t("panel.placeholders.upload")}
                onChange={(value) => {
                  form.setField("input_data_url", value)
                  form.setField("input_path", "")
                }}
              />
            </Field>

            <Field className="lal-field" label={t("panel.fields.inputPath")} help={t("panel.help.inputPath")}>
              <Input
                value={form.values.input_path}
                placeholder="/path/to/character.png"
                onChange={(value) => {
                  form.setField("input_path", value)
                  if (value.trim()) form.setField("input_data_url", "")
                }}
              />
            </Field>

            <div className="lal-panel-section">
              <div className="lal-section-title">{t("panel.sections.parts")}</div>
              <CheckboxGroup
                className="lal-parts-grid"
                value={form.values.parts}
                options={partOptions}
                onChange={(value) => form.setField("parts", value)}
              />
            </div>

            <Accordion id="advanced" title={t("panel.sections.advanced")} open={false}>
              <Field className="lal-field" label={t("panel.fields.method")}>
                <SegmentedControl
                  className="lal-method-control"
                  value={form.values.method}
                  options={[
                    { value: "anime_face", label: t("panel.methods.animeFace") },
                    { value: "grounded_sam", label: t("panel.methods.groundedSam") },
                    { value: "color", label: t("panel.methods.color") },
                  ]}
                  onChange={(value) => form.setField("method", String(value))}
                />
              </Field>
              <Field className="lal-field" label={t("panel.fields.feather")}>
                <Slider value={Number(form.values.feather_radius)} min={0} max={8} step={1} onChange={(value) => form.setField("feather_radius", value)} />
              </Field>
              <Field className="lal-field" label={t("panel.fields.gptKey")} help={t("panel.help.gptKey")}>
                <PasswordInput value={form.values.gpt_api_key} placeholder="optional" onChange={(value) => form.setField("gpt_api_key", value)} />
              </Field>
              <Field className="lal-field" label={t("panel.fields.sessionId")}>
                <Input value={form.values.session_id} placeholder="optional-session-id" onChange={(value) => form.setField("session_id", value)} />
              </Field>
            </Accordion>

            <ButtonGroup className="lal-run-actions">
              <Button tone="success" disabled={busy} onClick={runSplit}>
                {busy ? t("panel.actions.running") : t("panel.actions.split")}
              </Button>
              <Button tone="primary" disabled={busy || !result?.session_id} onClick={runResegment}>
                {t("panel.actions.resegment")}
              </Button>
            </ButtonGroup>
          </Stack>
        </Card>

        <Card className="lal-result-panel" title={t("panel.result.title")}>
          <div className="lal-result-body">
            <div className="lal-result-top">
              <Alert tone={result?.status === "succeeded" ? "success" : "warning"}>
                {result?.message || t("panel.result.empty")}
              </Alert>
              <FileDownload
                href={artifactUrl(pluginId, result?.zip_path)}
                path={result?.zip_path || ""}
                filename="live2d_layers.zip"
                label={t("panel.actions.downloadZip")}
                copiedLabel={t("panel.messages.pathCopied")}
              />
            </div>
            <div className="lal-preview-grid">
              <ImagePreview src={previewSrc} label={selectedLayer?.name || t("panel.result.preview")} emptyText={t("panel.result.noPreview")} />
              <div className="lal-layer-rail">
                <Gallery items={galleryItems} columns={2} emptyText={t("panel.layers.empty")} onSelect={(item) => setSelectedLayer(item)} />
              </div>
            </div>
            {result ? (
              <div className="lal-artifact-row">
                <span>{t("panel.result.session")}: {result.session_id || "-"}</span>
                <span>{t("panel.result.zip")}: {pathValue(result.zip_path)}</span>
              </div>
            ) : (
              <div className="lal-artifact-row is-empty">
                <span>{t("panel.result.session")}: -</span>
                <span>{t("panel.result.zip")}: -</span>
              </div>
            )}
          </div>
        </Card>
      </div>

      <div className="lal-secondary">
        <Accordion id="sessions" title={t("panel.sessions.title")} open={false}>
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
                  <ButtonGroup>
                    <Button tone="primary" onClick={() => { openSession(row) }}>{t("panel.actions.open")}</Button>
                    <Button tone="danger" onClick={() => deleteSession(row)}>{t("panel.actions.delete")}</Button>
                  </ButtonGroup>
                ),
              },
            ]}
          />
        </Accordion>

        <Accordion id="environment-details" title={t("panel.environment.title")} open={false}>
          <div className="neko-compact-kv">
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
          </div>
        </Accordion>

        <Accordion id="guide" title={t("panel.guide.title")} open={false}>
          <Markdown>{t("panel.guide.body")}</Markdown>
        </Accordion>
      </div>

      <Text>{t("panel.outputDir")}: {safeState.output_dir || "-"}</Text>
    </Page>
  )
}
