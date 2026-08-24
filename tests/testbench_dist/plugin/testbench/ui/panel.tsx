import {
  Alert,
  Button,
  Card,
  KeyValue,
  Page,
  Stack,
  StatusBadge,
  Text,
  Tip,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps, Tone } from "@neko/plugin-ui"

type DriverState = {
  running?: boolean
  mode?: string | null
  ui?: string | null
  pid?: number | null
  port?: number | null
  url?: string | null
  data_dir?: string
  neko_root?: string | null
  code_dir?: string | null
  last_error?: string | null
  can_spawn_python?: boolean
  hint?: string
  open_external_url?: string | null
}

function actionById(actions: HostedAction[] | undefined, id: string): HostedAction | undefined {
  if (!actions) return undefined
  return actions.find((action) => action.id === id || action.entry_id === id)
}

function openExternalUrl(url: string): void {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      { type: "neko-hosted-surface-open-external", payload: { url } },
      "*",
    )
    return
  }
  window.open(url, "_blank", "noopener,noreferrer")
}

export default function TestbenchPanel(props: PluginSurfaceProps<DriverState>) {
  const { state, actions, api } = props
  const safe = state || {}
  const running = !!safe.running
  const tone: Tone = running ? "success" : safe.last_error ? "danger" : "warning"
  const startAction = actionById(actions, "start")
  const stopAction = actionById(actions, "stop")
  const statusAction = actionById(actions, "status")
  const openAction = actionById(actions, "open")

  async function call(id: string) {
    const result = await api.call(id, {})
    const payload = (result && (result as { data?: DriverState }).data) || result
    const url =
      payload && typeof payload === "object"
        ? (payload as DriverState).open_external_url || (payload as DriverState).url
        : null
    if (id === "open" && url) {
      openExternalUrl(String(url))
    }
  }

  return (
    <Page title="N.E.K.O. Testbench">
      <Stack gap="md">
        <Alert tone={running ? "success" : safe.last_error ? "danger" : "info"}>
          {safe.hint || "启动后将优先打开独立 WebView 窗口。"}
        </Alert>

        <Card title="运行状态">
          <Stack gap="sm">
            <StatusBadge tone={tone} label={running ? "运行中" : "未运行"} />
            <KeyValue
              items={[
                { label: "模式", value: safe.mode || (safe.can_spawn_python ? "A(预期)" : "B(预期)") },
                { label: "UI", value: safe.ui || "-" },
                { label: "URL", value: safe.url || "-" },
                { label: "端口", value: safe.port != null ? String(safe.port) : "-" },
                { label: "PID", value: running && safe.pid ? String(safe.pid) : "-" },
                { label: "数据目录", value: safe.data_dir || "-" },
                { label: "上次错误", value: safe.last_error || "-" },
              ]}
            />
          </Stack>
        </Card>

        <Card title="操作">
          <Stack gap="sm" direction="row">
            <Button tone="primary" disabled={!startAction || running} onClick={() => call("start")}>
              启动 Testbench
            </Button>
            <Button tone="danger" disabled={!stopAction || !running} onClick={() => call("stop")}>
              停止
            </Button>
            <Button
              tone="secondary"
              disabled={!openAction || !running || !safe.url}
              onClick={() => call("open")}
            >
              打开窗口
            </Button>
            <Button tone="secondary" disabled={!statusAction} onClick={() => call("status")}>
              刷新状态
            </Button>
          </Stack>
        </Card>

        <Tip>
          <Text>
            能力允许时使用独立 WebView；否则用「打开窗口」走系统浏览器。功能问题请用
            <code>uv run python tests/testbench/run_testbench.py</code> 复现。
          </Text>
        </Tip>
      </Stack>
    </Page>
  )
}
