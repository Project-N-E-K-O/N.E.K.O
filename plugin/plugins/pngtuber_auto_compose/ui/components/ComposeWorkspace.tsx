import {
  Button,
  Card,
  DataTable,
  Field,
  FileDownload,
  Gallery,
  Grid,
  ImagePreview,
  ImageUpload,
  NumberInput,
  Progress,
  SegmentedControl,
  Select,
  Slider,
  Stack,
  StatusBadge,
  Text,
  Textarea,
} from "@neko/plugin-ui"
import type { FormState } from "@neko/plugin-ui"
import type { ComposeFormValues, Job, Workflow } from "../types"
import { formatTime, progressForJob, qaChecks, qaSummary, shortPath, toneForStatus } from "../lib/status"

type Props = {
  form: FormState<ComposeFormValues>
  workflows: Workflow[]
  selectedWorkflowId: string
  selectedJob?: Job
  pendingAction?: string
  syncNotice?: string
  baseTransferBatchSize: number
  statePreviewMode: string
  onBaseTransferBatchSizeChange: (value: number) => void
  onStatePreviewModeChange: (value: string) => void
  onSelectWorkflow: (workflowId: string) => void
  onCreateJob: () => void | Promise<void>
  onContinuePipeline: (job: Job) => void | Promise<void>
  onRunWorkflow: (job: Job, workflowId: string) => void | Promise<void>
  onRetryStep: (job: Job) => void | Promise<void>
  onGenerateTalking: (job: Job) => void | Promise<void>
  onBuildPackage: (job: Job) => void | Promise<void>
  onImportToNeko: (job: Job) => void | Promise<void>
  onCancelJob: (job: Job) => void | Promise<void>
  onSelectCandidate: (job: Job, artifactId: string, path: string) => void | Promise<void>
  onUploadError: (error: any) => void
}

export function ComposeWorkspace({
  form,
  workflows,
  selectedWorkflowId,
  selectedJob,
  pendingAction = "",
  syncNotice = "",
  baseTransferBatchSize,
  statePreviewMode,
  onBaseTransferBatchSizeChange,
  onStatePreviewModeChange,
  onSelectWorkflow,
  onCreateJob,
  onContinuePipeline,
  onRunWorkflow,
  onRetryStep,
  onGenerateTalking,
  onBuildPackage,
  onImportToNeko,
  onCancelJob,
  onSelectCandidate,
  onUploadError,
}: Props) {
  const qaRows = qaChecks(selectedJob)
  const runs = Array.isArray(selectedJob?.workflow_runs) ? selectedJob.workflow_runs : []
  const artifacts = Array.isArray(selectedJob?.artifacts) ? selectedJob.artifacts : []
  const imageArtifacts = artifacts.filter((artifact) => artifact.type === "image")
  const selectedCandidate = selectedJob?.metadata?.selected_candidate || {}
  const readyWorkflows = workflows.filter((workflow) => workflow.status === "ready")
  const actionInFlight = Boolean(pendingAction)
  const workflowOptions = workflows.map((workflow) => ({
    value: workflow.id || "",
    label: `${workflow.name || workflow.id || "Workflow"} (${workflow.status || "planned"})`,
    disabled: actionInFlight || (workflow.status !== "ready" && workflow.id !== "package_native"),
  }))
  const effectiveWorkflowId = selectedWorkflowId || readyWorkflows[0]?.id || "base_reference_transfer"
  const clampedBatchSize = clampBatchSize(baseTransferBatchSize)
  const hasNativeBase = hasMetadataPath(selectedJob, "native_base_image")
  const hasTalking = Boolean(
    hasVariantPath(selectedJob, "talking")
    || artifacts.some((artifact) => artifact.role === "state_variant_talking" && artifact.path)
  )
  const hasPackage = Boolean(selectedJob?.package_path)
  const idlePreview = latestArtifactByRoles(artifacts, ["idle", "idle_image"])
  const talkingPreview = latestArtifactByRoles(artifacts, ["talking", "state_variant_talking"])
  const activeStatePreview = statePreviewMode === "talking" && talkingPreview ? talkingPreview : idlePreview

  return (
    <Grid cols={3}>
      <Card title="Input">
        <Stack>
          <ImageUpload
            label="Reference"
            accept="image/png,image/jpeg,image/webp,image/gif"
            maxBytes={15728640}
            value={form.values.imageDataUrl}
            onChange={(dataUrl, file) => {
              form.setField("imageDataUrl", dataUrl)
              form.setField("filename", file.name)
            }}
            onError={onUploadError}
          />
          {form.values.imageDataUrl ? (
            <ImagePreview value={form.values.imageDataUrl} caption={form.values.filename || "reference"} />
          ) : null}
          <Field label="Recipe">
            <SegmentedControl
              value={form.values.mode}
              options={[
                { value: "two_state", label: "Two-state" },
                { value: "four_state", label: "Four-state" },
                { value: "expressions", label: "Expressions" },
                { value: "layered", label: "Layered" },
              ]}
              onChange={(value) => form.setField("mode", String(value))}
            />
          </Field>
          <Field label="Positive prompt">
            <Textarea value={form.values.positivePrompt} onChange={(value) => form.setField("positivePrompt", value)} />
          </Field>
          <Field label="Negative prompt">
            <Textarea value={form.values.negativePrompt} onChange={(value) => form.setField("negativePrompt", value)} />
          </Field>
          <Field label="Note">
            <Textarea value={form.values.note} onChange={(value) => form.setField("note", value)} />
          </Field>
          <Button tone="primary" disabled={!form.values.imageDataUrl || actionInFlight} onClick={onCreateJob}>Create job</Button>
        </Stack>
      </Card>

      <Card title="Run">
        <Stack>
          {selectedJob ? (
            <>
              <Grid cols={2}>
                <StatusBadge tone={toneForStatus(selectedJob.status)} label={selectedJob.status || "unknown"} />
                <Text>{selectedJob.mode || "-"}</Text>
              </Grid>
              {pendingAction ? (
                <>
                  <StatusBadge tone="primary" label={pendingAction} />
                  <Progress value={0.6} label="Request is active; waiting for backend/ComfyUI result" />
                </>
              ) : null}
              {syncNotice ? (
                <>
                  <StatusBadge tone="warning" label="Sync warning" />
                  <Text>{syncNotice}</Text>
                </>
              ) : null}
              <Progress value={progressForJob(selectedJob)} label={selectedJob.stage || "-"} />
              <Text>{selectedJob.message || ""}</Text>
              <DataTable
                data={readyWorkflows}
                rowKey="id"
                maxRows={5}
                emptyText="No ready workflows"
                columns={[
                  { key: "name", label: "Step" },
                  { key: "stage", label: "Stage" },
                  { key: "status", label: "Status", render: (workflow) => workflow.status || "-" },
                ]}
              />
              <Field label="Workflow">
                <Select
                  value={effectiveWorkflowId}
                  options={workflowOptions}
                  onChange={(value) => onSelectWorkflow(String(value))}
                />
              </Field>
              {effectiveWorkflowId === "base_reference_transfer" ? (
                <Field label="Batch size">
                  <Grid cols={2}>
                    <Slider
                      min={1}
                      max={8}
                      step={1}
                      value={clampedBatchSize}
                      disabled={actionInFlight}
                      onChange={(value) => onBaseTransferBatchSizeChange(clampBatchSize(value))}
                    />
                    <NumberInput
                      min={1}
                      max={8}
                      step={1}
                      value={clampedBatchSize}
                      onChange={(value) => onBaseTransferBatchSizeChange(clampBatchSize(value))}
                    />
                  </Grid>
                </Field>
              ) : null}
              <Grid cols={2}>
                <Button tone="primary" disabled={!selectedJob.job_id || selectedJob.status === "running" || actionInFlight} onClick={() => onContinuePipeline(selectedJob)}>Continue</Button>
                <Button tone="warning" disabled={!selectedJob.job_id || selectedJob.status === "running" || actionInFlight} onClick={() => onRetryStep(selectedJob)}>Retry</Button>
              </Grid>
              <Grid cols={2}>
                <Button disabled={!selectedJob.job_id || selectedJob.status === "running" || actionInFlight || !effectiveWorkflowId} onClick={() => onRunWorkflow(selectedJob, effectiveWorkflowId)}>Run selected</Button>
                <Button tone="success" disabled={!selectedJob.job_id || actionInFlight} onClick={() => onBuildPackage(selectedJob)}>Build package</Button>
              </Grid>
              <Grid cols={2}>
                <Button disabled={!selectedJob.job_id || !hasNativeBase || selectedJob.status === "running" || actionInFlight} onClick={() => onGenerateTalking(selectedJob)}>{hasTalking ? "Regenerate talking" : "Generate talking"}</Button>
                <Button tone="success" disabled={!selectedJob.job_id || !hasPackage || selectedJob.status === "running" || actionInFlight} onClick={() => onImportToNeko(selectedJob)}>Import to N.E.K.O</Button>
              </Grid>
              <Grid cols={1}>
                <Button tone="warning" disabled={!selectedJob.job_id || selectedJob.status === "succeeded" || actionInFlight} onClick={() => onCancelJob(selectedJob)}>Cancel</Button>
              </Grid>
            </>
          ) : (
            <Text>No selected job</Text>
          )}
        </Stack>
      </Card>

      <Card title="Inspector">
        <Stack>
          {selectedJob ? (
            <>
              <DataTable
                data={[
                  { key: "Job", value: selectedJob.job_id || "-" },
                  { key: "Source", value: selectedJob.source_filename || "-" },
                  { key: "Updated", value: formatTime(selectedJob.updated_at || selectedJob.created_at) },
                  { key: "QA", value: qaSummary(selectedJob) },
                  { key: "Selected", value: selectedCandidateLabel(selectedCandidate) },
                  { key: "Package", value: selectedJob.package_path ? shortPath(selectedJob.package_path) : "-" },
                  { key: "Installed", value: selectedJob.metadata?.installed_model?.url || "-" },
                ]}
                rowKey="key"
                columns={[
                  { key: "key", label: "Field" },
                  { key: "value", label: "Value" },
                ]}
              />
              {selectedJob.package_path ? <FileDownload path={selectedJob.package_path} label="Open package folder" /> : null}
              {idlePreview || talkingPreview ? (
                <>
                  <Field label="State preview">
                    <SegmentedControl
                      value={statePreviewMode}
                      options={[
                        { value: "idle", label: "Idle" },
                        { value: "talking", label: "Talking", disabled: !talkingPreview },
                      ]}
                      onChange={(value) => onStatePreviewModeChange(String(value))}
                    />
                  </Field>
                  <ImagePreview
                    value={artifactPreviewSrc(activeStatePreview)}
                    caption={activeStatePreview?.label || activeStatePreview?.role || "state"}
                  />
                  <Grid cols={2}>
                    <ImagePreview value={artifactPreviewSrc(idlePreview)} caption="idle" />
                    <ImagePreview value={artifactPreviewSrc(talkingPreview)} caption="talking" emptyText="No talking image" />
                  </Grid>
                </>
              ) : null}
              <Gallery
                items={imageArtifacts.map((artifact, index) => ({
                  ...artifact,
                  name: `${isSelectedArtifact(artifact, selectedCandidate) ? "Selected - " : ""}${artifact.label || artifact.role || `Image ${index + 1}`}`,
                }))}
                columns={2}
                emptyText="No image previews"
                onSelect={(artifact) => {
                  if (artifact && typeof artifact === "object") {
                    onSelectCandidate(selectedJob, artifact.artifact_id || "", artifact.path || "")
                  }
                }}
              />
              <DataTable
                data={artifacts}
                rowKey="artifact_id"
                maxRows={5}
                emptyText="No artifacts"
                columns={[
                  { key: "role", label: "Role" },
                  {
                    key: "selected",
                    label: "Selected",
                    render: (artifact) => isSelectedArtifact(artifact, selectedCandidate)
                      ? <StatusBadge tone="success" label="selected" />
                      : "",
                  },
                  { key: "type", label: "Type" },
                  { key: "path", label: "Path", render: (artifact) => shortPath(artifact.path) },
                  {
                    key: "actions",
                    label: "Actions",
                    render: (artifact) => (
                      <Button
                        tone="success"
                        disabled={!artifact.artifact_id && !artifact.path}
                        onClick={() => onSelectCandidate(selectedJob, artifact.artifact_id || "", artifact.path || "")}
                      >
                        {isSelectedArtifact(artifact, selectedCandidate) ? "Selected" : "Select"}
                      </Button>
                    ),
                  },
                ]}
              />
              <DataTable
                data={qaRows}
                rowKey="id"
                maxRows={5}
                emptyText="No QA checks"
                columns={[
                  { key: "id", label: "Check" },
                  { key: "ok", label: "OK", render: (check) => <StatusBadge tone={check.ok ? "success" : "warning"} label={check.ok ? "pass" : "pending"} /> },
                  { key: "message", label: "Message" },
                ]}
              />
              <DataTable
                data={runs}
                rowKey="run_id"
                maxRows={4}
                emptyText="No runs"
                columns={[
                  { key: "workflow_id", label: "Workflow" },
                  { key: "status", label: "Status", render: (run) => <StatusBadge tone={toneForStatus(run.status)} label={run.status || "-"} /> },
                  { key: "prompt_id", label: "Prompt" },
                ]}
              />
            </>
          ) : (
            <Text>No selected job</Text>
          )}
        </Stack>
      </Card>
    </Grid>
  )
}

function clampBatchSize(value: any) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 4
  return Math.max(1, Math.min(8, Math.round(parsed)))
}

function isSelectedArtifact(artifact: any, selectedCandidate: any) {
  if (!artifact || !selectedCandidate) return false
  const artifactId = String(artifact.artifact_id || "")
  const selectedId = String(selectedCandidate.artifact_id || "")
  if (artifactId && selectedId && artifactId === selectedId) return true
  const artifactPath = String(artifact.path || "")
  const selectedPath = String(selectedCandidate.path || "")
  return Boolean(artifactPath && selectedPath && artifactPath === selectedPath)
}

function selectedCandidateLabel(selectedCandidate: any) {
  if (!selectedCandidate || typeof selectedCandidate !== "object") return "-"
  const role = String(selectedCandidate.role || "")
  const path = String(selectedCandidate.path || "")
  if (!role && !path) return "-"
  return role ? `${role}: ${shortPath(path)}` : shortPath(path)
}

function hasMetadataPath(job: Job | undefined, key: string) {
  const value = job?.metadata?.[key]
  return Boolean(value && typeof value === "object" && String(value.path || ""))
}

function hasVariantPath(job: Job | undefined, key: string) {
  const variants = job?.metadata?.state_variants
  const value = variants && typeof variants === "object" ? variants[key] : null
  return Boolean(value && typeof value === "object" && String(value.path || ""))
}

function latestArtifactByRoles(artifacts: Array<any>, roles: string[]) {
  for (let index = artifacts.length - 1; index >= 0; index -= 1) {
    const artifact = artifacts[index]
    if (artifact && roles.includes(String(artifact.role || "")) && artifactPreviewSrc(artifact)) {
      return artifact
    }
  }
  return null
}

function artifactPreviewSrc(artifact: any) {
  if (!artifact || typeof artifact !== "object") return ""
  return String(artifact.preview_data_url || artifact.data_url || artifact.src || artifact.url || "")
}
