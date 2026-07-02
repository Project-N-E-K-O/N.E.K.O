import {
  Button,
  Grid,
  StatCard,
} from "@neko/plugin-ui"
import type { DashboardState, Job, Workflow } from "../types"

type Props = {
  state?: DashboardState
  jobs: Job[]
  workflows: Workflow[]
  selectedJob?: Job
  onCheckComfyUI: () => void | Promise<void>
}

export function TopStatusBar({ state, jobs, workflows, selectedJob, onCheckComfyUI }: Props) {
  const activeJobs = jobs.filter((job) => job.status === "queued" || job.status === "running").length
  const readyWorkflows = workflows.filter((workflow) => workflow.status === "ready").length

  return (
    <Grid cols={5}>
      <StatCard label="Jobs" value={jobs.length} />
      <StatCard label="Active" value={activeJobs} />
      <StatCard label="Workflows" value={`${readyWorkflows}/${workflows.length}`} />
      <StatCard label="Selected" value={selectedJob?.job_id || "-"} />
      <StatCard
        label="ComfyUI"
        value={<Button onClick={onCheckComfyUI}>{state?.comfyui_url ? "Check" : "Configure"}</Button>}
      />
    </Grid>
  )
}
