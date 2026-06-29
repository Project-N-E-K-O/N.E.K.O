import {
  Button,
  Card,
  DataTable,
  Grid,
  Stack,
  StatusBadge,
  Toolbar,
  ToolbarGroup,
} from "@neko/plugin-ui"
import type { Job } from "../types"
import { formatTime, qaSummary, toneForStatus } from "../lib/status"

type Props = {
  jobs: Job[]
  selectedJobId: string
  onSelectJob: (job: Job) => void
  onBuildPackage: (job: Job) => void | Promise<void>
  onCancelJob: (job: Job) => void | Promise<void>
  onDeleteJob: (job: Job) => void | Promise<void>
  onClearJobs: () => void | Promise<void>
}

export function JobQueue({
  jobs,
  selectedJobId,
  onSelectJob,
  onBuildPackage,
  onCancelJob,
  onDeleteJob,
  onClearJobs,
}: Props) {
  return (
    <Card title="Queue">
      <Stack>
        <Toolbar>
          <ToolbarGroup>
            <Button tone="danger" disabled={jobs.length === 0} onClick={onClearJobs}>Clear jobs</Button>
          </ToolbarGroup>
        </Toolbar>
        <DataTable
          data={jobs}
          rowKey="job_id"
          selectedKey={selectedJobId}
          emptyText="No jobs"
          onSelect={onSelectJob}
          columns={[
            { key: "job_id", label: "Job" },
            {
              key: "status",
              label: "Status",
              render: (job) => <StatusBadge tone={toneForStatus(job.status)} label={job.status || "unknown"} />,
            },
            { key: "mode", label: "Recipe" },
            { key: "stage", label: "Stage" },
            { key: "source_filename", label: "Source" },
            { key: "qa", label: "QA", render: (job) => qaSummary(job) },
            { key: "updated_at", label: "Updated", render: (job) => formatTime(job.updated_at || job.created_at) },
            {
              key: "actions",
              label: "Actions",
              render: (job) => (
                <Grid cols={3}>
                  <Button tone="success" disabled={!job.job_id} onClick={() => onBuildPackage(job)}>Build</Button>
                  <Button tone="warning" disabled={!job.job_id || job.status === "succeeded"} onClick={() => onCancelJob(job)}>Cancel</Button>
                  <Button tone="danger" disabled={!job.job_id} onClick={() => onDeleteJob(job)}>Delete</Button>
                </Grid>
              ),
            },
          ]}
        />
      </Stack>
    </Card>
  )
}
