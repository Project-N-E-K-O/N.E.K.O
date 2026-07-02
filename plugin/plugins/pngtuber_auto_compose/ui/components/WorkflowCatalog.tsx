import {
  Card,
  DataTable,
  Stack,
  StatusBadge,
} from "@neko/plugin-ui"
import type { Workflow } from "../types"
import { toneForWorkflow, workflowStages } from "../lib/status"

type Props = {
  workflows: Workflow[]
}

export function WorkflowCatalog({ workflows }: Props) {
  const stages = workflowStages(workflows)

  return (
    <Stack>
      {stages.length === 0 ? (
        <Card title="Workflows">No workflows</Card>
      ) : stages.map((stage) => (
        <Card key={stage.stage} title={stage.stage}>
          <DataTable
            data={stage.workflows}
            rowKey="id"
            emptyText="No workflows"
            columns={[
              { key: "name", label: "Workflow" },
              {
                key: "status",
                label: "Status",
                render: (workflow) => <StatusBadge tone={toneForWorkflow(workflow)} label={workflow.status || "planned"} />,
              },
              { key: "inputs", label: "Inputs", render: (workflow) => Array.isArray(workflow.inputs) ? workflow.inputs.map((item) => item.id).join(", ") : "" },
              { key: "outputs", label: "Outputs", render: (workflow) => Array.isArray(workflow.outputs) ? workflow.outputs.map((item) => item.id).join(", ") : "" },
              { key: "next", label: "Next", render: (workflow) => Array.isArray(workflow.next) ? workflow.next.join(", ") : "" },
            ]}
          />
        </Card>
      ))}
    </Stack>
  )
}
