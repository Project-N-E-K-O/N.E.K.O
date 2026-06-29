import {
  Page,
  Tabs,
  useEffect,
  useConfirm,
  useForm,
  useToast,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"
import { ComposeWorkspace } from "./components/ComposeWorkspace"
import { JobQueue } from "./components/JobQueue"
import { SettingsDiagnostics } from "./components/SettingsDiagnostics"
import { TopStatusBar } from "./components/TopStatusBar"
import { WorkflowCatalog } from "./components/WorkflowCatalog"
import type { ComposeFormValues, DashboardState, Job } from "./types"

const emptyForm: ComposeFormValues = {
  imageDataUrl: "",
  filename: "",
  mode: "four_state",
  positivePrompt: "",
  negativePrompt: "low quality, worst quality, blurry, bad anatomy, bad hands, extra fingers, missing fingers, extra limbs, multiple characters, dynamic pose, scenery, text, watermark",
  note: "",
}

function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    if (envelope.value && typeof envelope.value === "object") return envelope.value
    return envelope
  }
  return {}
}

function clampBatchSize(value: any) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 4
  return Math.max(1, Math.min(8, Math.round(parsed)))
}

function errorMessage(err: any) {
  return err instanceof Error ? err.message : String(err || "unknown")
}

export default function PNGTuberAutoComposePanel(props: PluginSurfaceProps<DashboardState>) {
  const { state } = props
  const jobs = Array.isArray(state?.jobs) ? state.jobs : []
  const workflows = Array.isArray(state?.workflows) ? state.workflows : []
  const latest = jobs[0]
  const form = useForm<ComposeFormValues>(emptyForm)
  const toast = useToast()
  const confirm = useConfirm()
  const [activeTab, setActiveTab] = props.useLocalState("activeTab", "compose")
  const [selectedJobId, setSelectedJobId] = props.useLocalState("selectedJobId", latest?.job_id || "")
  const [selectedWorkflowId, setSelectedWorkflowId] = props.useLocalState("selectedWorkflowId", "base_reference_transfer")
  const [pendingAction, setPendingAction] = props.useLocalState("pendingAction", "")
  const [syncNotice, setSyncNotice] = props.useLocalState("syncNotice", "")
  const [baseTransferBatchSize, setBaseTransferBatchSize] = props.useLocalState("baseTransferBatchSize", 4)
  const [statePreviewMode, setStatePreviewMode] = props.useLocalState("statePreviewMode", "idle")
  const selectedJob = jobs.find((job) => job.job_id === selectedJobId) || latest
  const effectiveSelectedJobId = selectedJob?.job_id || ""

  useEffect(() => {
    const jobId = selectedJob?.job_id || ""
    const status = selectedJob?.status || ""
    const stage = selectedJob?.stage || ""
    const shouldPoll = Boolean(jobId) && (status === "queued" || status === "running" || stage.includes(":submitted"))
    if (!shouldPoll) {
      setSyncNotice("")
      return
    }
    let stopped = false
    let inFlight = false
    let failures = 0
    async function poll() {
      if (stopped || inFlight) return
      inFlight = true
      try {
        await props.api.call("sync_job", { job_id: jobId }, { timeoutMs: 30000 })
        failures = 0
        setSyncNotice("")
      } catch (err) {
        failures += 1
        if (failures >= 2) {
          setSyncNotice(`Auto sync failed: ${errorMessage(err)}`)
        }
      } finally {
        try {
          await props.api.refresh()
        } catch {
          // Ignore refresh failures during polling; the next interval can recover.
        }
        inFlight = false
      }
    }
    poll()
    const timer = window.setInterval(poll, 3000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [selectedJob?.job_id, selectedJob?.status, selectedJob?.stage])

  async function checkComfyUI() {
    try {
      const result = unwrapActionResult(await props.api.call("check_comfyui"))
      const message = result.ok
        ? "ComfyUI is reachable"
        : `ComfyUI unavailable: ${result.error || result.message || "unknown"}`
      toast.show(message, {
        tone: result.ok ? "success" : "warning",
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function createJob() {
    if (!form.values.imageDataUrl) {
      toast.warning("Upload a reference image first")
      return
    }
    try {
      const result = unwrapActionResult(await props.api.call("create_job", {
        image_data_url: form.values.imageDataUrl,
        filename: form.values.filename,
        mode: form.values.mode,
        positive_prompt: form.values.positivePrompt,
        negative_prompt: form.values.negativePrompt,
        note: form.values.note,
      }, { timeoutMs: 60000 }))
      const jobId = result?.job?.job_id
      form.reset(emptyForm)
      if (jobId) setSelectedJobId(jobId)
      setActiveTab("compose")
      await props.api.refresh()
      toast.success("Job created")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function buildPackage(job: Job) {
    if (!job.job_id) return
    setPendingAction("Building package")
    try {
      const result = unwrapActionResult(await props.api.call("build_minimal_package", {
        job_id: job.job_id,
        display_name: `PNGTuber ${job.job_id}`,
      }, { timeoutMs: 60000 }))
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      toast.success(result?.job?.message || "Package built")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingAction("")
    }
  }

  async function generateTalking(job: Job) {
    if (!job.job_id) return
    setPendingAction("Generating talking")
    try {
      const result = unwrapActionResult(await props.api.call("generate_talking", {
        job_id: job.job_id,
      }, { timeoutMs: 60000 }))
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      toast.success(result?.job?.message || "Talking variant generated")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingAction("")
    }
  }

  async function importToNeko(job: Job) {
    if (!job.job_id) return
    setPendingAction("Importing to N.E.K.O")
    try {
      const result = unwrapActionResult(await props.api.call("import_to_neko", {
        job_id: job.job_id,
        folder_name: `PNGTuber ${job.job_id}`,
      }, { timeoutMs: 60000 }))
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      const installed = result?.job?.metadata?.installed_model?.url || result?.installed_model?.url
      toast.success(installed ? `Imported ${installed}` : "Imported to N.E.K.O")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingAction("")
    }
  }

  async function continuePipeline(job: Job) {
    if (!job.job_id) return
    setPendingAction("Advancing pipeline")
    try {
      const result = unwrapActionResult(await props.api.call("continue_pipeline", { job_id: job.job_id }, { timeoutMs: 60000 }))
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      toast.success(result?.job?.message || "Pipeline advanced")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingAction("")
    }
  }

  async function runWorkflow(job: Job, workflowId: string) {
    if (!job.job_id || !workflowId) return
    setPendingAction(`Running ${workflowId}`)
    const inputs = workflowId === "base_reference_transfer"
      ? { batch_size: clampBatchSize(baseTransferBatchSize) }
      : {}
    try {
      const result = unwrapActionResult(await props.api.call("run_workflow", {
        job_id: job.job_id,
        workflow_id: workflowId,
        inputs,
      }, { timeoutMs: 60000 }))
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      toast.success(result?.job?.message || "Workflow completed")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingAction("")
    }
  }

  async function retryStep(job: Job) {
    if (!job.job_id) return
    setPendingAction("Retrying workflow")
    try {
      const result = unwrapActionResult(await props.api.call("retry_step", { job_id: job.job_id }, { timeoutMs: 60000 }))
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      toast.success(result?.job?.message || "Step retried")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingAction("")
    }
  }

  async function selectCandidate(job: Job, artifactId: string, path: string) {
    if (!job.job_id) return
    try {
      await props.api.call("select_candidate", {
        job_id: job.job_id,
        artifact_id: artifactId,
        path,
      })
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      toast.success("Candidate selected")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function cancelJob(job: Job) {
    if (!job.job_id) return
    try {
      await props.api.call("cancel_job", { job_id: job.job_id })
      setSelectedJobId(job.job_id)
      await props.api.refresh()
      toast.success("Job canceled")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function deleteJob(job: Job) {
    if (!job.job_id) return
    const ok = await confirm({
      title: "Delete job",
      message: `Delete ${job.job_id}?`,
      tone: "danger",
      confirmLabel: "Delete",
      cancelLabel: "Cancel",
    })
    if (!ok) return
    try {
      await props.api.call("delete_job", { job_id: job.job_id })
      if (selectedJobId === job.job_id) setSelectedJobId("")
      await props.api.refresh()
      toast.success("Job deleted")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function clearJobs() {
    const ok = await confirm({
      title: "Clear jobs",
      message: "Clear all local job records and files?",
      tone: "danger",
      confirmLabel: "Clear",
      cancelLabel: "Cancel",
    })
    if (!ok) return
    try {
      await props.api.call("clear_jobs")
      setSelectedJobId("")
      await props.api.refresh()
      toast.success("Jobs cleared")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Page title="PNGTuber Auto Compose" subtitle="Pipeline workspace for PNGTuber asset composition">
      <TopStatusBar
        state={state}
        jobs={jobs}
        workflows={workflows}
        selectedJob={selectedJob}
        onCheckComfyUI={checkComfyUI}
      />

      <Tabs
        id="pngtuber-auto-compose-tabs"
        activeId={activeTab}
        onChange={(id) => setActiveTab(id)}
        items={[
          {
            id: "compose",
            label: "Compose",
            content: (
              <ComposeWorkspace
                form={form}
                workflows={workflows}
                selectedWorkflowId={selectedWorkflowId}
                selectedJob={selectedJob}
                pendingAction={pendingAction}
                syncNotice={syncNotice}
                baseTransferBatchSize={clampBatchSize(baseTransferBatchSize)}
                statePreviewMode={statePreviewMode}
                onBaseTransferBatchSizeChange={setBaseTransferBatchSize}
                onStatePreviewModeChange={setStatePreviewMode}
                onSelectWorkflow={setSelectedWorkflowId}
                onCreateJob={createJob}
                onContinuePipeline={continuePipeline}
                onRunWorkflow={runWorkflow}
                onRetryStep={retryStep}
                onGenerateTalking={generateTalking}
                onBuildPackage={buildPackage}
                onImportToNeko={importToNeko}
                onCancelJob={cancelJob}
                onSelectCandidate={selectCandidate}
                onUploadError={(err) => toast.error(String(err))}
              />
            ),
          },
          {
            id: "queue",
            label: "Queue",
            content: (
              <JobQueue
                jobs={jobs}
                selectedJobId={effectiveSelectedJobId}
                onSelectJob={(job) => {
                  setSelectedJobId(job.job_id || "")
                }}
                onBuildPackage={buildPackage}
                onCancelJob={cancelJob}
                onDeleteJob={deleteJob}
                onClearJobs={clearJobs}
              />
            ),
          },
          {
            id: "workflows",
            label: "Workflows",
            content: <WorkflowCatalog workflows={workflows} />,
          },
          {
            id: "settings",
            label: "Settings",
            content: <SettingsDiagnostics state={state} onCheckComfyUI={checkComfyUI} />,
          },
        ]}
      />
    </Page>
  )
}
