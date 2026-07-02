import {
  Button,
  Card,
  DataTable,
  Field,
  JsonView,
  Stack,
} from "@neko/plugin-ui"
import type { DashboardState } from "../types"

type Props = {
  state?: DashboardState
  onCheckComfyUI: () => void | Promise<void>
}

export function SettingsDiagnostics({ state, onCheckComfyUI }: Props) {
  const rows = [
    { key: "ComfyUI", value: state?.comfyui_url || "-" },
    { key: "Jobs dir", value: state?.jobs_dir || "-" },
    { key: "Default recipe", value: state?.default_mode || "-" },
    { key: "Max image bytes", value: state?.config?.max_image_bytes || "-" },
    { key: "Keep completed", value: state?.config?.keep_completed_jobs || "-" },
  ]

  return (
    <Stack>
      <Card title="Settings">
        <Stack>
          <DataTable
            data={rows}
            rowKey="key"
            columns={[
              { key: "key", label: "Setting" },
              { key: "value", label: "Value" },
            ]}
          />
          <Button onClick={onCheckComfyUI}>Check ComfyUI</Button>
        </Stack>
      </Card>
      <Card title="Debug">
        <Stack>
          <Field label="Dashboard state">
            <JsonView data={state || {}} />
          </Field>
        </Stack>
      </Card>
    </Stack>
  )
}
