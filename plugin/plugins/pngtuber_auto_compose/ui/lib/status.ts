import type { Job, QaCheck, Workflow } from "../types"

export function toneForStatus(status?: string) {
  if (status === "succeeded") return "success"
  if (status === "ready") return "success"
  if (status === "failed") return "danger"
  if (status === "canceled") return "warning"
  if (status === "blocked") return "warning"
  if (status === "running") return "primary"
  if (status === "queued") return "info"
  return "default"
}

export function toneForWorkflow(workflow?: Workflow) {
  if (workflow?.status === "ready") return "success"
  if (workflow?.status === "missing") return "danger"
  if (workflow?.status === "planned") return "info"
  return "default"
}

export function progressForJob(job?: Job) {
  if (!job) return 0
  if (job.stage?.includes(":completed")) return 1
  if (job.status === "succeeded") return 1
  if (job.status === "ready") return 1
  if (job.status === "failed" || job.status === "canceled") return 1
  if (job.status === "blocked") return 1
  if (job.status === "running") return 0.6
  if (job.stage === "input_saved") return 0.25
  return 0.1
}

export function formatTime(value?: number) {
  if (!value) return "-"
  return new Date(value * 1000).toLocaleString()
}

export function shortPath(value?: string) {
  const text = String(value || "")
  if (!text) return "-"
  const parts = text.split("/")
  if (parts.length <= 3) return text
  return `.../${parts.slice(-3).join("/")}`
}

export function qaChecks(job?: Job): QaCheck[] {
  const checks = job?.qa?.checks
  return Array.isArray(checks) ? checks : []
}

export function qaSummary(job?: Job) {
  const checks = qaChecks(job)
  if (checks.length === 0) return "0/0"
  const passed = checks.filter((check) => check.ok).length
  return `${passed}/${checks.length}`
}

export function workflowStages(workflows: Workflow[]) {
  const stages: Record<string, Workflow[]> = {}
  workflows.forEach((workflow) => {
    const stage = workflow.stage || "other"
    stages[stage] = stages[stage] || []
    stages[stage].push(workflow)
  })
  return Object.entries(stages).map(([stage, items]) => ({ stage, workflows: items }))
}
