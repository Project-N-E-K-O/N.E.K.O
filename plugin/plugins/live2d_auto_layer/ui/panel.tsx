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
  useRef,
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
  cubism_handoff_zip_path?: string
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

type AutoRigQualitySummary = {
  visual_status?: string
  rig_geometry_status?: string
  high_risk_layer_count?: number
  medium_risk_layer_count?: number
}

type AutoRigParameter = {
  id?: string
  name?: string
  min?: number
  max?: number
  default?: number
}

type AutoRigBinding = {
  parameter?: string
  type?: string
  scale?: number
}

type AutoRigLayerGroup = "head" | "hair" | "body" | "accessory"

type AutoRigPreviewLayer = {
  name?: string
  texture_path?: string
  draw_order?: number
  width?: number
  height?: number
  bbox?: number[]
  bindings?: AutoRigBinding[]
  metadata?: Record<string, any>
}

type AutoRigPreviewModel = {
  session_id?: string
  format?: string
  canvas_size?: number[]
  preview_path?: string
  parameters?: AutoRigParameter[]
  layers?: AutoRigPreviewLayer[]
  quality_summary?: AutoRigQualitySummary
}

type AutoRigBounds = {
  x: number
  y: number
  width: number
  height: number
  pivotX: number
  pivotY: number
}

type AutoRigTransform = {
  pivotX: number
  pivotY: number
  offsetX: number
  offsetY: number
  rotationDeg: number
  scaleX: number
  scaleY: number
}

type CubismConnectionStatus = {
  host?: string
  port?: number
  port_open?: boolean
  token_saved?: boolean
  status?: string
}

type CubismEditorState = {
  approved?: boolean
  approval_wait_timed_out?: boolean
  approval_wait_seconds?: number
  host?: string
  port?: number
  current_edit_mode?: Record<string, any>
  current_model?: Record<string, any>
  current_document?: Record<string, any>
  documents?: Record<string, any>
}

type CubismExportEvent = {
  event?: string
  accepted?: Record<string, any>
  data?: Record<string, any>
  timed_out?: boolean
  timeout_seconds?: number
}

const CUBISM_APPROVAL_WAIT_SECONDS = 120

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
  layer_source_path: "",
  session_id: "",
  method: "anime_face",
  parts: ["Face_Skin", "Eye_L", "Eye_R", "Mouth", "Eyebrow_L", "Eyebrow_R", "Hair", "Body"],
  feather_radius: 2,
  mesh_alpha_threshold: 10,
  gpt_api_key: "",
  cubism_host: "127.0.0.1",
  cubism_port: 22033,
  cubism_log_message: "N.E.K.O Live2D Auto Layer connected.",
  cubism_export_timeout: 30,
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

function qualityStatusLabel(t: (key: string) => string, value: string | undefined): string {
  const status = String(value || "")
  if (status === "preserved") return t("panel.quality.status.preserved")
  if (status === "needs_review") return t("panel.quality.status.needsReview")
  if (status === "watch") return t("panel.quality.status.watch")
  if (status === "ok") return t("panel.quality.status.ok")
  return status || "-"
}

function cubismStatusLabel(t: (key: string) => string, status: CubismConnectionStatus | null): string {
  const value = String(status?.status || "")
  if (value === "api_unavailable") return t("panel.cubismEditor.status.apiUnavailable")
  if (value === "api_available_token_saved") return t("panel.cubismEditor.status.tokenSaved")
  if (value === "api_available_no_token") return t("panel.cubismEditor.status.noToken")
  return value || "-"
}

function cubismPortBadge(t: (key: string) => string, status: CubismConnectionStatus | null) {
  return (
    <StatusBadge
      tone={status?.port_open ? "success" : "warning"}
      label={status?.port_open ? t("panel.cubismEditor.portOpen") : t("panel.cubismEditor.portClosed")}
    />
  )
}

function jsonMarkdown(value: unknown): string {
  if (!value) return "```json\n{}\n```"
  return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``
}

function autoRigCanvasSize(model: AutoRigPreviewModel | null): [number, number] {
  const size = Array.isArray(model?.canvas_size) ? model?.canvas_size || [] : []
  const width = Math.max(1, Number(size[0]) || 512)
  const height = Math.max(1, Number(size[1]) || 512)
  return [width, height]
}

function defaultAutoRigPose(model: AutoRigPreviewModel | null): Record<string, number> {
  const pose: Record<string, number> = {}
  for (const parameter of model?.parameters || []) {
    const id = String(parameter.id || "")
    if (!id) continue
    pose[id] = Number(parameter.default ?? 0)
  }
  return pose
}

function autoRigParameterMap(model: AutoRigPreviewModel): Record<string, AutoRigParameter> {
  const map: Record<string, AutoRigParameter> = {}
  for (const parameter of model.parameters || []) {
    const id = String(parameter.id || "")
    if (id) map[id] = parameter
  }
  return map
}

function autoRigPoseValue(pose: Record<string, number>, parameter: AutoRigParameter | undefined, parameterId: string): number {
  if (Object.prototype.hasOwnProperty.call(pose, parameterId)) return Number(pose[parameterId])
  return Number(parameter?.default ?? 0)
}

function normalizedAutoRigValue(value: number, parameter: AutoRigParameter | undefined): number {
  const min = Number(parameter?.min ?? -1)
  const max = Number(parameter?.max ?? 1)
  const fallbackDefault = min <= 0 && max >= 0 ? 0 : min
  const defaultValue = Number(parameter?.default ?? fallbackDefault)
  const denominator = value >= defaultValue
    ? Math.max(1e-6, max - defaultValue)
    : Math.max(1e-6, defaultValue - min)
  return Math.max(-1, Math.min(1, (value - defaultValue) / denominator))
}

function normalizedUnitValue(value: number, parameter: AutoRigParameter | undefined): number {
  const min = Number(parameter?.min ?? 0)
  const max = Number(parameter?.max ?? 1)
  return Math.max(0, Math.min(1, (value - min) / Math.max(1e-6, max - min)))
}

function autoRigParameterStep(parameter: AutoRigParameter): number {
  const min = Number(parameter.min ?? 0)
  const max = Number(parameter.max ?? 1)
  return Math.abs(max - min) <= 2 ? 0.01 : 1
}

function autoRigParameterValue(pose: Record<string, number>, parameter: AutoRigParameter): number {
  const id = String(parameter.id || "")
  return autoRigPoseValue(pose || {}, parameter, id)
}

function autoRigLayerGroup(layer: AutoRigPreviewLayer): AutoRigLayerGroup {
  const metadataGroup = String(layer.metadata?.rig_group || "")
  if (metadataGroup === "head" || metadataGroup === "hair" || metadataGroup === "body" || metadataGroup === "accessory") {
    return metadataGroup
  }
  const name = String(layer.name || "").trim().toLowerCase().replace(/[\s-]+/g, "_")
  if (name === "headwear" || name === "head_accessory" || name === "face_accessory") return "head"
  if (hasAutoRigNameToken(name, ["hair", "tail"])) return "hair"
  if (
    [
      "face_skin",
      "face_detail",
      "ears",
      "neck",
      "eye_white",
      "iris",
      "eyelash",
      "eyebrow",
      "mouth",
      "nose",
    ].includes(name)
    || name.startsWith("eye_")
    || name.startsWith("eyebrow_")
  ) {
    return "head"
  }
  if (
    hasAutoRigNameToken(name, ["body", "foot", "leg", "hand", "arm"])
    || name.endsWith("wear")
    || ["topwear", "bottomwear", "legwear", "footwear", "handwear"].includes(name)
  ) {
    return "body"
  }
  return "accessory"
}

function hasAutoRigNameToken(name: string, tokens: string[]): boolean {
  const parts = name.split("_").filter(Boolean)
  return tokens.some((token) => parts.includes(token))
}

function isAutoRigBlinkLayer(layer: AutoRigPreviewLayer): boolean {
  const name = String(layer.name || "").trim().toLowerCase().replace(/[\s-]+/g, "_")
  return name === "eye_white" || name === "iris" || name === "eyelash" || name.startsWith("eye_")
}

function isAutoRigMouthLayer(layer: AutoRigPreviewLayer): boolean {
  const name = String(layer.name || "").trim().toLowerCase().replace(/[\s-]+/g, "_")
  return name === "mouth" || name.startsWith("mouth_")
}

function autoRigLayerBounds(layer: AutoRigPreviewLayer, canvasWidth: number, canvasHeight: number): AutoRigBounds {
  const bbox = Array.isArray(layer.bbox) ? layer.bbox : []
  const x = Number(bbox[0] ?? 0)
  const y = Number(bbox[1] ?? 0)
  const width = Math.max(1, Number(bbox[2] ?? layer.width ?? canvasWidth))
  const height = Math.max(1, Number(bbox[3] ?? layer.height ?? canvasHeight))
  return {
    x,
    y,
    width,
    height,
    pivotX: x + width / 2,
    pivotY: y + height / 2,
  }
}

function autoRigGroupBounds(
  layers: AutoRigPreviewLayer[],
  canvasWidth: number,
  canvasHeight: number,
): Record<AutoRigLayerGroup, AutoRigBounds> {
  const groups: Record<AutoRigLayerGroup, AutoRigBounds | null> = {
    head: null,
    hair: null,
    body: null,
    accessory: null,
  }
  for (const layer of layers) {
    const group = autoRigLayerGroup(layer)
    const bounds = autoRigLayerBounds(layer, canvasWidth, canvasHeight)
    groups[group] = unionAutoRigBounds(groups[group], bounds)
  }
  const fallback = {
    x: 0,
    y: 0,
    width: canvasWidth,
    height: canvasHeight,
    pivotX: canvasWidth / 2,
    pivotY: canvasHeight / 2,
  }
  return {
    head: groups.head || fallback,
    hair: groups.hair || groups.head || fallback,
    body: groups.body || fallback,
    accessory: groups.accessory || fallback,
  }
}

function unionAutoRigBounds(a: AutoRigBounds | null, b: AutoRigBounds): AutoRigBounds {
  if (!a) return b
  const x1 = Math.min(a.x, b.x)
  const y1 = Math.min(a.y, b.y)
  const x2 = Math.max(a.x + a.width, b.x + b.width)
  const y2 = Math.max(a.y + a.height, b.y + b.height)
  return {
    x: x1,
    y: y1,
    width: Math.max(1, x2 - x1),
    height: Math.max(1, y2 - y1),
    pivotX: x1 + (x2 - x1) / 2,
    pivotY: y1 + (y2 - y1) / 2,
  }
}

function identityAutoRigTransform(bounds: AutoRigBounds): AutoRigTransform {
  return {
    pivotX: bounds.pivotX,
    pivotY: bounds.pivotY,
    offsetX: 0,
    offsetY: 0,
    rotationDeg: 0,
    scaleX: 1,
    scaleY: 1,
  }
}

function autoRigGroupTransform(
  group: AutoRigLayerGroup,
  bounds: AutoRigBounds,
  parameters: Record<string, AutoRigParameter>,
  pose: Record<string, number>,
): AutoRigTransform {
  const transform = identityAutoRigTransform(bounds)
  if (group === "head") {
    const angleX = normalizedAutoRigValue(autoRigPoseValue(pose, parameters.ParamAngleX, "ParamAngleX"), parameters.ParamAngleX)
    const angleY = normalizedAutoRigValue(autoRigPoseValue(pose, parameters.ParamAngleY, "ParamAngleY"), parameters.ParamAngleY)
    const angleZ = autoRigPoseValue(pose, parameters.ParamAngleZ, "ParamAngleZ")
    transform.offsetX = angleX * bounds.width * 0.08
    transform.offsetY = angleY * bounds.height * 0.06
    transform.rotationDeg = angleZ * 0.16
  } else if (group === "body") {
    const bodyX = normalizedAutoRigValue(autoRigPoseValue(pose, parameters.ParamBodyAngleX, "ParamBodyAngleX"), parameters.ParamBodyAngleX)
    const bodyY = normalizedAutoRigValue(autoRigPoseValue(pose, parameters.ParamBodyAngleY, "ParamBodyAngleY"), parameters.ParamBodyAngleY)
    const breath = normalizedUnitValue(autoRigPoseValue(pose, parameters.ParamBreath, "ParamBreath"), parameters.ParamBreath)
    transform.offsetX = bodyX * bounds.width * 0.04
    transform.offsetY = bodyY * bounds.height * 0.03
    transform.scaleY = 1 + breath * 0.015
  }
  return transform
}

function applyAutoRigTransform(ctx: any, transform: AutoRigTransform) {
  ctx.translate(transform.pivotX + transform.offsetX, transform.pivotY + transform.offsetY)
  ctx.rotate((transform.rotationDeg * Math.PI) / 180)
  ctx.scale(Math.max(0.05, transform.scaleX), Math.max(0.05, transform.scaleY))
  ctx.translate(-transform.pivotX, -transform.pivotY)
}

async function drawAutoRigModel(
  canvas: any,
  model: AutoRigPreviewModel,
  pluginId: string,
  pose: Record<string, number>,
): Promise<void> {
  const [width, height] = autoRigCanvasSize(model)
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext("2d")
  if (!ctx) return
  ctx.clearRect(0, 0, width, height)
  const parameters = autoRigParameterMap(model)
  const layers = [...(model.layers || [])].sort((a, b) => Number(a.draw_order || 0) - Number(b.draw_order || 0))
  const groupBounds = autoRigGroupBounds(layers, width, height)
  for (const layer of layers) {
    const url = artifactUrl(pluginId, layer.texture_path)
    if (!url) continue
    const image = await loadImage(url)
    drawAutoRigLayer(ctx, image, layer, parameters, pose, groupBounds, width, height)
  }
}

function loadImage(src: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error(`Image load failed: ${src}`))
    image.src = src
  })
}

function drawAutoRigLayer(
  ctx: any,
  image: any,
  layer: AutoRigPreviewLayer,
  parameters: Record<string, AutoRigParameter>,
  pose: Record<string, number>,
  groupBounds: Record<AutoRigLayerGroup, AutoRigBounds>,
  canvasWidth: number,
  canvasHeight: number,
) {
  const layerWidth = Number(layer.width || canvasWidth)
  const layerHeight = Number(layer.height || canvasHeight)
  const group = autoRigLayerGroup(layer)
  const parentGroup = group === "hair" ? "head" : group
  const parentTransform = autoRigGroupTransform(parentGroup, groupBounds[parentGroup], parameters, pose)
  const localTransform = identityAutoRigTransform(autoRigLayerBounds(layer, canvasWidth, canvasHeight))

  for (const binding of layer.bindings || []) {
    const parameterId = String(binding.parameter || "")
    if (!parameterId) continue
    const parameter = parameters[parameterId]
    const value = autoRigPoseValue(pose, parameter, parameterId)
    const normalized = normalizedAutoRigValue(value, parameter)
    const scale = Number(binding.scale ?? 0)
    if (binding.type === "sway" && group === "hair") {
      localTransform.offsetX += normalized * groupBounds.hair.width * scale
      localTransform.rotationDeg += normalized * 12 * scale
    } else if (binding.type === "scale_y" && parameterId === "ParamMouthOpenY" && isAutoRigMouthLayer(layer)) {
      localTransform.scaleY += normalized * scale
    } else if (binding.type === "mask_y" && parameterId === "ParamEyeBlink" && isAutoRigBlinkLayer(layer)) {
      localTransform.scaleY *= Math.max(0.05, normalizedUnitValue(value, parameter))
    }
  }

  ctx.save()
  applyAutoRigTransform(ctx, parentTransform)
  applyAutoRigTransform(ctx, localTransform)
  ctx.drawImage(image, 0, 0, layerWidth, layerHeight)
  ctx.restore()
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
  const [workspaceMode, setWorkspaceMode] = props.useLocalState("workspaceMode", "extract")
  const [sourceMode, setSourceMode] = props.useLocalState("sourceMode", "split")
  const [cubismHandoffPath, setCubismHandoffPath] = props.useLocalState("cubismHandoffPath", "")
  const [autoRigPath, setAutoRigPath] = props.useLocalState("autoRigPath", "")
  const [autoRigQuality, setAutoRigQuality] = props.useLocalState<AutoRigQualitySummary | null>("autoRigQuality", null)
  const [autoRigModel, setAutoRigModel] = props.useLocalState<AutoRigPreviewModel | null>("autoRigModel", null)
  const [autoRigPose, setAutoRigPose] = props.useLocalState<Record<string, number>>("autoRigPose", {})
  const [cubismStatus, setCubismStatus] = props.useLocalState<CubismConnectionStatus | null>("cubismStatus", null)
  const [cubismEditorState, setCubismEditorState] = props.useLocalState<CubismEditorState | null>("cubismEditorState", null)
  const [cubismExportEvent, setCubismExportEvent] = props.useLocalState<CubismExportEvent | null>("cubismExportEvent", null)
  const autoRigCanvasRef = useRef<any>(null)

  useEffect(() => {
    form.setField("method", String(safeState.default_method || "anime_face"))
  }, [safeState.default_method])

  function applyAutomaticCubismHandoff(result: ProcessResult) {
    const path = String(result.cubism_handoff_zip_path || "")
    setCubismHandoffPath(path)
    if (path) {
      toast.success(t("panel.messages.cubismAutoExportDone"))
    }
  }

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
      setCubismHandoffPath("")
      setAutoRigPath("")
      setAutoRigQuality(null)
      setAutoRigModel(null)
      setAutoRigPose({})
      applyAutomaticCubismHandoff(result)
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
      setCubismHandoffPath("")
      setAutoRigPath("")
      setAutoRigQuality(null)
      setAutoRigModel(null)
      setAutoRigPose({})
      applyAutomaticCubismHandoff(result)
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

  async function runImportLayerSource() {
    const layerSourcePath = form.values.layer_source_path.trim()
    if (!layerSourcePath) {
      toast.error(t("panel.errors.layerSourceRequired"))
      return
    }
    setBusy(true)
    setProgressText(t("panel.messages.importingLayers"))
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_import_layer_source",
        {
          layer_source_path: layerSourcePath,
          session_id: form.values.session_id.trim(),
          source: "see_through",
        },
        { timeoutMs: 300000 },
      )
      const result = unwrapActionResult(envelope) as ProcessResult
      setLastResult(result)
      setSelectedLayer(null)
      setCubismHandoffPath("")
      setAutoRigPath("")
      setAutoRigQuality(null)
      setAutoRigModel(null)
      setAutoRigPose({})
      applyAutomaticCubismHandoff(result)
      await props.api.refresh()
      toast.success(result.message || t("panel.messages.importDone"))
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
        setCubismHandoffPath("")
        setAutoRigPath("")
        setAutoRigQuality(null)
        setAutoRigModel(null)
        setAutoRigPose({})
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
      setCubismHandoffPath("")
      setAutoRigPath("")
      setAutoRigQuality(null)
      setAutoRigModel(null)
      setAutoRigPose({})
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runExportAutoRigModel() {
    const sessionId = String(result?.session_id || "").trim()
    if (!sessionId) {
      toast.error(t("panel.errors.sessionRequired"))
      return
    }
    setBusy(true)
    setProgressText(t("panel.messages.exportingAutoRig"))
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_export_auto_rig_model",
        {
          session_id: sessionId,
          mesh_alpha_threshold: Number(form.values.mesh_alpha_threshold) || 0,
        },
        { timeoutMs: 120000 },
      )
      const data = unwrapActionResult(envelope)
      const path = String(data.auto_rig_zip_path || "")
      setAutoRigPath(path)
      setAutoRigQuality(
        data.quality_summary && typeof data.quality_summary === "object"
          ? data.quality_summary as AutoRigQualitySummary
          : null,
      )
      setAutoRigModel(null)
      setAutoRigPose({})
      toast.success(String(data.message || t("panel.messages.autoRigExportDone")))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runLoadAutoRigModel() {
    const sessionId = String(result?.session_id || "").trim()
    if (!sessionId) {
      toast.error(t("panel.errors.sessionRequired"))
      return
    }
    setBusy(true)
    setProgressText(t("panel.messages.loadingAutoRig"))
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_load_auto_rig_model",
        { session_id: sessionId },
        { timeoutMs: 120000 },
      )
      const data = unwrapActionResult(envelope) as AutoRigPreviewModel
      setAutoRigModel(data)
      setAutoRigPose(defaultAutoRigPose(data))
      setAutoRigQuality(data.quality_summary || null)
      toast.success(t("panel.messages.autoRigLoaded"))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runExportCubismHandoff() {
    const sessionId = String(result?.session_id || "").trim()
    if (!sessionId) {
      toast.error(t("panel.errors.sessionRequired"))
      return
    }
    setBusy(true)
    setProgressText(t("panel.messages.exportingCubism"))
    setError("")
    try {
      await exportCubismHandoffForSession(sessionId)
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function exportCubismHandoffForSession(sessionId: string, options?: { auto?: boolean }) {
    const cleanSessionId = String(sessionId || "").trim()
    if (!cleanSessionId) return
    setProgressText(t("panel.messages.exportingCubism"))
    try {
      const envelope = await props.api.call(
        "live2d_export_cubism_handoff",
        { session_id: cleanSessionId },
        { timeoutMs: 120000 },
      )
      const data = unwrapActionResult(envelope)
      const path = String(data.cubism_handoff_zip_path || "")
      setCubismHandoffPath(path)
      toast.success(String(data.message || (options?.auto ? t("panel.messages.cubismAutoExportDone") : t("panel.messages.cubismExportDone"))))
    } catch (exc: any) {
      if (!options?.auto) throw exc
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    }
  }

  function cubismConnectionArgs() {
    return {
      host: String(form.values.cubism_host || "127.0.0.1").trim() || "127.0.0.1",
      port: Number(form.values.cubism_port) || 22033,
    }
  }

  async function runCubismStatus() {
    setBusy(true)
    setProgressText(t("panel.messages.checkingCubism"))
    setError("")
    try {
      const envelope = await props.api.call("live2d_cubism_status", cubismConnectionArgs(), { timeoutMs: 30000 })
      const data = unwrapActionResult(envelope) as CubismConnectionStatus
      setCubismStatus(data)
      toast.success(cubismStatusLabel(t, data))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runCubismRegister() {
    setBusy(true)
    setProgressText(t("panel.messages.registeringCubism"))
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_cubism_register",
        { ...cubismConnectionArgs(), approval_wait_seconds: CUBISM_APPROVAL_WAIT_SECONDS },
        { timeoutMs: (CUBISM_APPROVAL_WAIT_SECONDS + 20) * 1000 },
      )
      const data = unwrapActionResult(envelope) as Record<string, any>
      setCubismStatus({
        host: String(data.host || form.values.cubism_host),
        port: Number(data.port || form.values.cubism_port),
        port_open: true,
        token_saved: Boolean(data.token_saved),
        status: data.token_saved ? "api_available_token_saved" : "api_available_no_token",
      })
      setCubismEditorState({
        ...(cubismEditorState || {}),
        approved: Boolean(data.approved),
        approval_wait_timed_out: Boolean(data.approval_wait_timed_out),
        approval_wait_seconds: Number(data.approval_wait_seconds || CUBISM_APPROVAL_WAIT_SECONDS),
        host: String(data.host || form.values.cubism_host),
        port: Number(data.port || form.values.cubism_port),
      })
      toast.success(data.approved ? t("panel.messages.cubismApproved") : t("panel.messages.cubismRegistered"))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runCubismEditorState() {
    setBusy(true)
    setProgressText(t("panel.messages.loadingCubismState"))
    setError("")
    try {
      const envelope = await props.api.call("live2d_cubism_editor_state", cubismConnectionArgs(), { timeoutMs: 30000 })
      const data = unwrapActionResult(envelope) as CubismEditorState
      setCubismEditorState(data)
      toast.success(data.approved ? t("panel.messages.cubismStateLoaded") : t("panel.messages.cubismNeedsApproval"))
    } catch (exc: any) {
      const message = String(exc?.message || exc)
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runCubismSendLog() {
    const message = String(form.values.cubism_log_message || "").trim()
    if (!message) {
      toast.error(t("panel.errors.cubismLogRequired"))
      return
    }
    setBusy(true)
    setProgressText(t("panel.messages.sendingCubismLog"))
    setError("")
    try {
      await props.api.call(
        "live2d_cubism_send_log",
        { ...cubismConnectionArgs(), message, log_type: "info", display: true },
        { timeoutMs: 30000 },
      )
      toast.success(t("panel.messages.cubismLogSent"))
    } catch (exc: any) {
      const messageText = String(exc?.message || exc)
      setError(messageText)
      toast.error(messageText)
    } finally {
      setBusy(false)
      setProgressText("")
    }
  }

  async function runCubismWaitMocExport() {
    setBusy(true)
    setProgressText(t("panel.messages.waitingCubismExport"))
    setError("")
    try {
      const envelope = await props.api.call(
        "live2d_cubism_wait_moc_export",
        { ...cubismConnectionArgs(), timeout_seconds: Number(form.values.cubism_export_timeout) || 30 },
        { timeoutMs: Math.max(60000, (Number(form.values.cubism_export_timeout) || 30) * 1000 + 10000) },
      )
      const data = unwrapActionResult(envelope) as CubismExportEvent
      setCubismExportEvent(data)
      toast.success(data.timed_out ? t("panel.messages.cubismExportTimeout") : t("panel.messages.cubismExportEventReceived"))
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
  const isExtractWorkspace = workspaceMode === "extract"
  const isAutoRigWorkspace = workspaceMode === "auto_rig"
  const isAutoRigPreviewWorkspace = workspaceMode === "auto_rig_preview"
  const isCubismWorkspace = workspaceMode === "cubism"
  const isCubismEditorWorkspace = workspaceMode === "cubism_editor"
  const isImportMode = sourceMode === "import"
  const autoRigParameters = Array.isArray(autoRigModel?.parameters) ? autoRigModel?.parameters || [] : []
  const workspaceTitle = isAutoRigWorkspace
    ? t("panel.autoRig.title")
    : isAutoRigPreviewWorkspace
      ? t("panel.autoRigPreview.title")
    : isCubismEditorWorkspace
      ? t("panel.cubismEditor.title")
    : isCubismWorkspace
      ? t("panel.cubism.title")
      : t("panel.process.title")

  useEffect(() => {
    if (environmentKnown || autoEnvCheckRequested || checkingEnv) return
    setAutoEnvCheckRequested(true)
    void checkEnvironment({ silent: true })
  }, [environmentKnown, autoEnvCheckRequested, checkingEnv])

  useEffect(() => {
    const canvas = autoRigCanvasRef.current
    if (!canvas || !autoRigModel) return
    let cancelled = false
    drawAutoRigModel(canvas, autoRigModel, pluginId, autoRigPose || {}).catch((exc) => {
      if (cancelled) return
      setError(String(exc?.message || exc))
    })
    return () => {
      cancelled = true
    }
  }, [autoRigModel, autoRigPose, pluginId])

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

      <SegmentedControl
        className="lal-workspace-tabs"
        value={workspaceMode}
        options={[
          { value: "extract", label: t("panel.workspaces.extract") },
          { value: "auto_rig", label: t("panel.workspaces.autoRig") },
          { value: "auto_rig_preview", label: t("panel.workspaces.autoRigPreview") },
          { value: "cubism", label: t("panel.workspaces.cubism") },
          { value: "cubism_editor", label: t("panel.workspaces.cubismEditor") },
        ]}
        onChange={(value) => setWorkspaceMode(String(value))}
      />

      <div className="lal-workbench">
        <Card className="lal-input-panel" title={workspaceTitle}>
          <Stack className="lal-control-stack" gap={8}>
            {isExtractWorkspace ? (
              <>
                <Field className="lal-field" label={t("panel.fields.sourceMode")}>
                  <SegmentedControl
                    className="lal-method-control"
                    value={sourceMode}
                    options={[
                      { value: "split", label: t("panel.modes.split") },
                      { value: "import", label: t("panel.modes.import") },
                    ]}
                    onChange={(value) => setSourceMode(String(value))}
                  />
                </Field>

                {isImportMode ? (
                  <>
                    <Field className="lal-field" label={t("panel.fields.layerSourcePath")} help={t("panel.help.layerSourcePath")}>
                      <Input
                        value={form.values.layer_source_path}
                        placeholder="/path/to/see-through/output-or.psd"
                        onChange={(value) => form.setField("layer_source_path", value)}
                      />
                    </Field>
                    <Field className="lal-field" label={t("panel.fields.sessionId")}>
                      <Input value={form.values.session_id} placeholder="optional-session-id" onChange={(value) => form.setField("session_id", value)} />
                    </Field>
                  </>
                ) : (
                  <>
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
                  </>
                )}

                <ButtonGroup className="lal-run-actions">
                  {isImportMode ? (
                    <Button tone="success" disabled={busy} onClick={runImportLayerSource}>
                      {busy ? t("panel.actions.running") : t("panel.actions.importLayers")}
                    </Button>
                  ) : (
                    <>
                      <Button tone="success" disabled={busy} onClick={runSplit}>
                        {busy ? t("panel.actions.running") : t("panel.actions.split")}
                      </Button>
                      <Button tone="primary" disabled={busy || !result?.session_id} onClick={runResegment}>
                        {t("panel.actions.resegment")}
                      </Button>
                    </>
                  )}
                </ButtonGroup>
              </>
            ) : isAutoRigWorkspace ? (
              <>
                <KeyValue
                  items={[
                    { key: "session", label: t("panel.result.session"), value: result?.session_id || "-" },
                    { key: "layers", label: t("panel.sessions.layers"), value: String(layers.length) },
                  ]}
                />
                <Field className="lal-field" label={t("panel.fields.meshAlphaThreshold")} help={t("panel.help.meshAlphaThreshold")}>
                  <Slider
                    value={Number(form.values.mesh_alpha_threshold)}
                    min={0}
                    max={64}
                    step={1}
                    onChange={(value) => form.setField("mesh_alpha_threshold", value)}
                  />
                </Field>
                {autoRigQuality ? (
                  <KeyValue
                    items={[
                      { key: "visual", label: t("panel.quality.visual"), value: qualityStatusLabel(t, autoRigQuality.visual_status) },
                      { key: "rig", label: t("panel.quality.rigGeometry"), value: qualityStatusLabel(t, autoRigQuality.rig_geometry_status) },
                      { key: "high", label: t("panel.quality.highRiskLayers"), value: String(autoRigQuality.high_risk_layer_count || 0) },
                      { key: "medium", label: t("panel.quality.mediumRiskLayers"), value: String(autoRigQuality.medium_risk_layer_count || 0) },
                    ]}
                  />
                ) : null}
                <ButtonGroup className="lal-run-actions">
                  <Button tone="success" disabled={busy || !result?.session_id} onClick={runExportAutoRigModel}>
                    {t("panel.actions.exportAutoRig")}
                  </Button>
                  <FileDownload
                    href={artifactUrl(pluginId, autoRigPath)}
                    path={autoRigPath || ""}
                    filename="auto_rig_model.zip"
                    label={t("panel.actions.downloadAutoRig")}
                    copiedLabel={t("panel.messages.pathCopied")}
                  />
                </ButtonGroup>
              </>
            ) : isAutoRigPreviewWorkspace ? (
              <>
                <KeyValue
                  items={[
                    { key: "session", label: t("panel.result.session"), value: result?.session_id || "-" },
                    { key: "model", label: t("panel.autoRigPreview.model"), value: autoRigModel?.format || "-" },
                    { key: "layers", label: t("panel.sessions.layers"), value: String(autoRigModel?.layers?.length || 0) },
                  ]}
                />
                {autoRigQuality ? (
                  <KeyValue
                    items={[
                      { key: "visual", label: t("panel.quality.visual"), value: qualityStatusLabel(t, autoRigQuality.visual_status) },
                      { key: "rig", label: t("panel.quality.rigGeometry"), value: qualityStatusLabel(t, autoRigQuality.rig_geometry_status) },
                      { key: "high", label: t("panel.quality.highRiskLayers"), value: String(autoRigQuality.high_risk_layer_count || 0) },
                      { key: "medium", label: t("panel.quality.mediumRiskLayers"), value: String(autoRigQuality.medium_risk_layer_count || 0) },
                    ]}
                  />
                ) : null}
                {autoRigModel ? (
                  <div className="lal-panel-section">
                    <div className="lal-section-title">{t("panel.autoRigPreview.pose")}</div>
                    {autoRigParameters.map((parameter) => {
                      const id = String(parameter.id || "")
                      const value = autoRigParameterValue(autoRigPose || {}, parameter)
                      return (
                        <div key={id}>
                          <Field
                            className="lal-field"
                            label={String(parameter.name || id)}
                            help={String(value)}
                          >
                            <Slider
                              value={value}
                              min={Number(parameter.min ?? 0)}
                              max={Number(parameter.max ?? 1)}
                              step={autoRigParameterStep(parameter)}
                              onChange={(nextValue) => setAutoRigPose({ ...(autoRigPose || {}), [id]: Number(nextValue) })}
                            />
                          </Field>
                        </div>
                      )
                    })}
                  </div>
                ) : null}
                <ButtonGroup className="lal-run-actions">
                  <Button tone="primary" disabled={busy || !result?.session_id} onClick={runLoadAutoRigModel}>
                    {t("panel.actions.loadAutoRig")}
                  </Button>
                  <Button tone="default" disabled={busy || !autoRigModel} onClick={() => { setAutoRigPose(defaultAutoRigPose(autoRigModel)) }}>
                    {t("panel.actions.resetPose")}
                  </Button>
                </ButtonGroup>
              </>
            ) : isCubismEditorWorkspace ? (
              <>
                <div className="lal-cubism-status">
                  {cubismPortBadge(t, cubismStatus)}
                  <KeyValue
                    items={[
                      { key: "endpoint", label: t("panel.cubismEditor.endpoint"), value: `${form.values.cubism_host}:${form.values.cubism_port}` },
                      { key: "status", label: t("panel.cubismEditor.status"), value: cubismStatusLabel(t, cubismStatus) },
                      { key: "token", label: t("panel.cubismEditor.token"), value: cubismStatus?.token_saved ? t("panel.status.ready") : t("panel.status.missing") },
                      { key: "approved", label: t("panel.cubismEditor.approval"), value: cubismEditorState?.approved ? t("panel.cubismEditor.approved") : t("panel.cubismEditor.notApproved") },
                    ]}
                  />
                </div>

                <div className="lal-panel-section">
                  <div className="lal-section-title">{t("panel.cubismEditor.connection")}</div>
                  <Field className="lal-field" label={t("panel.fields.cubismHost")}>
                    <Input value={String(form.values.cubism_host)} onChange={(value) => form.setField("cubism_host", value)} />
                  </Field>
                  <Field className="lal-field" label={t("panel.fields.cubismPort")}>
                    <Input value={String(form.values.cubism_port)} onChange={(value) => form.setField("cubism_port", Number(value) || 22033)} />
                  </Field>
                  <ButtonGroup className="lal-run-actions">
                    <Button tone="default" disabled={busy} onClick={runCubismStatus}>{t("panel.actions.cubismStatus")}</Button>
                    <Button tone="primary" disabled={busy} onClick={runCubismRegister}>{t("panel.actions.cubismRegister")}</Button>
                    <Button tone="primary" disabled={busy} onClick={runCubismEditorState}>{t("panel.actions.cubismState")}</Button>
                  </ButtonGroup>
                </div>

                <div className="lal-panel-section">
                  <div className="lal-section-title">{t("panel.cubismEditor.log")}</div>
                  <Field className="lal-field" label={t("panel.fields.cubismLogMessage")}>
                    <Input value={String(form.values.cubism_log_message)} onChange={(value) => form.setField("cubism_log_message", value)} />
                  </Field>
                  <ButtonGroup className="lal-run-actions">
                    <Button tone="primary" disabled={busy} onClick={runCubismSendLog}>{t("panel.actions.cubismSendLog")}</Button>
                  </ButtonGroup>
                </div>

                <div className="lal-panel-section">
                  <div className="lal-section-title">{t("panel.cubismEditor.exports")}</div>
                  <Field className="lal-field" label={t("panel.fields.cubismExportTimeout")}>
                    <Slider
                      value={Number(form.values.cubism_export_timeout)}
                      min={5}
                      max={180}
                      step={5}
                      onChange={(value) => form.setField("cubism_export_timeout", value)}
                    />
                  </Field>
                  <ButtonGroup className="lal-run-actions">
                    <Button tone="primary" disabled={busy} onClick={runCubismWaitMocExport}>{t("panel.actions.cubismWaitExport")}</Button>
                  </ButtonGroup>
                </div>

                {cubismEditorState ? (
                  <Accordion id="cubism-editor-state" title={t("panel.cubismEditor.stateJson")} open={false}>
                    <Markdown>{jsonMarkdown(cubismEditorState)}</Markdown>
                  </Accordion>
                ) : null}
                {cubismExportEvent ? (
                  <Accordion id="cubism-export-event" title={t("panel.cubismEditor.exportJson")} open={false}>
                    <Markdown>{jsonMarkdown(cubismExportEvent)}</Markdown>
                  </Accordion>
                ) : null}
              </>
            ) : (
              <>
                <KeyValue
                  items={[
                    { key: "session", label: t("panel.result.session"), value: result?.session_id || "-" },
                    { key: "layers", label: t("panel.sessions.layers"), value: String(layers.length) },
                  ]}
                />
                <ButtonGroup className="lal-run-actions">
                  <Button tone="primary" disabled={busy || !result?.session_id} onClick={runExportCubismHandoff}>
                    {t("panel.actions.exportCubism")}
                  </Button>
                  <FileDownload
                    href={artifactUrl(pluginId, cubismHandoffPath)}
                    path={cubismHandoffPath || ""}
                    filename="cubism_handoff.zip"
                    label={t("panel.actions.downloadCubism")}
                    copiedLabel={t("panel.messages.pathCopied")}
                  />
                </ButtonGroup>
              </>
            )}
          </Stack>
        </Card>

        <Card className="lal-result-panel" title={t("panel.result.title")}>
          <div className="lal-result-body">
            <div className="lal-result-top">
              {isAutoRigPreviewWorkspace ? (
                <Alert tone={autoRigModel ? "success" : "warning"}>
                  {autoRigModel ? t("panel.autoRigPreview.loaded") : t("panel.autoRigPreview.empty")}
                </Alert>
              ) : (
                <Alert tone={result?.status === "succeeded" ? "success" : "warning"}>
                  {result?.message || t("panel.result.empty")}
                </Alert>
              )}
              {isExtractWorkspace ? (
                <FileDownload
                  href={artifactUrl(pluginId, result?.zip_path)}
                  path={result?.zip_path || ""}
                  filename="live2d_layers.zip"
                  label={t("panel.actions.downloadZip")}
                  copiedLabel={t("panel.messages.pathCopied")}
                />
              ) : null}
            </div>
            {isAutoRigPreviewWorkspace ? (
              <div className="lal-auto-rig-preview">
                {autoRigModel ? (
                  <canvas
                    ref={autoRigCanvasRef}
                    width={autoRigCanvasSize(autoRigModel)[0]}
                    height={autoRigCanvasSize(autoRigModel)[1]}
                    style={{
                      width: "100%",
                      maxHeight: "720px",
                      objectFit: "contain",
                      border: "1px solid #d8e0ea",
                      borderRadius: "8px",
                      background:
                        "repeating-conic-gradient(#f1f5f9 0% 25%, #ffffff 0% 50%) 50% / 24px 24px",
                    }}
                  />
                ) : (
                  <Text>{t("panel.autoRigPreview.noModel")}</Text>
                )}
              </div>
            ) : (
              <div className="lal-preview-grid">
                <ImagePreview src={previewSrc} label={selectedLayer?.name || t("panel.result.preview")} emptyText={t("panel.result.noPreview")} />
                <div className="lal-layer-rail">
                  <Gallery items={galleryItems} columns={2} emptyText={t("panel.layers.empty")} onSelect={(item) => setSelectedLayer(item)} />
                </div>
              </div>
            )}
            {result && !isAutoRigPreviewWorkspace ? (
              <div className="lal-artifact-row">
                <span>{t("panel.result.session")}: {result.session_id || "-"}</span>
                <span>{t("panel.result.zip")}: {pathValue(result.zip_path)}</span>
              </div>
            ) : !isAutoRigPreviewWorkspace ? (
              <div className="lal-artifact-row is-empty">
                <span>{t("panel.result.session")}: -</span>
                <span>{t("panel.result.zip")}: -</span>
              </div>
            ) : null}
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










