import {
  ErrorBoundary,
  Inline,
  Page,
  RefreshButton,
  Stack,
  Tabs,
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

import { DiagnosticsSection } from "./diagnostics"
import { DocumentsSection } from "./documents"
import { OverviewSection } from "./overview"
import { PreferencesSection } from "./preferences"
import { PromptsSection } from "./prompts"
import { TimelineSection } from "./timeline"
import type { DashboardState } from "./types"

export default function NekoWowsPanel(props: PluginSurfaceProps<DashboardState>) {
  const state = props.state || {}
  const [busy, setBusy] = useState(false)

  const call = async (actionId: string, args?: Record<string, any>) => {
    setBusy(true)
    try {
      const result = await props.api.call(actionId, args, { userInitiated: true })
      await props.api.refresh()
      return result
    } finally {
      setBusy(false)
    }
  }

  return (
    <Page
      title="战舰世界猫娘陪玩"
      subtitle="只读 8111 遥测：把战局快照转成事件，仲裁后让猫娘自己组织措辞。"
    >
      <ErrorBoundary title="面板渲染出错">
        <Stack gap={12}>
          <Inline gap={8} justify="end">
            <RefreshButton onRefresh={() => props.api.refresh()} />
          </Inline>

          <Tabs
            id="neko-wows-panel"
            items={[
              {
                id: "overview",
                label: "概览",
                content: <OverviewSection state={state} />,
              },
              {
                id: "timeline",
                label: "实时战术链路",
                content: <TimelineSection entries={state.timeline || []} />,
              },
              {
                id: "documents",
                label: "战术文档",
                content: (
                  <DocumentsSection
                    documents={state.documents || {}}
                    actions={props.actions || []}
                    busy={busy}
                  />
                ),
              },
              {
                id: "prompts",
                label: "提示词实验室",
                content: (
                  <PromptsSection
                    prompts={state.prompts || {}}
                    actions={props.actions || []}
                    busy={busy}
                  />
                ),
              },
              {
                id: "preferences",
                label: "偏好",
                content: (
                  <PreferencesSection
                    config={state.config || {}}
                    arbiter={state.arbiter || {}}
                    categories={state.categories || []}
                    lanes={state.lanes || []}
                    busy={busy}
                    onSetChannelMode={(mode) =>
                      call("set_channel_mode", { mode })
                    }
                    onSetIntrusion={(mode, quietWindowSeconds) =>
                      call("set_intrusion_mode", {
                        mode,
                        quiet_window_seconds: quietWindowSeconds,
                      })
                    }
                    onSetCategory={(category, enabled) =>
                      call("set_category_enabled", { category, enabled })
                    }
                    onSetLane={(lane, enabled) =>
                      call("set_lane_enabled", { lane, enabled })
                    }
                    onSetLaneTiming={(lane, ttl, minGap) =>
                      call("set_lane_timing", {
                        lane,
                        ttl_seconds: ttl,
                        min_gap_seconds: minGap,
                      })
                    }
                  />
                ),
              },
              {
                id: "diagnostics",
                label: "安全诊断",
                content: (
                  <DiagnosticsSection
                    state={state}
                    actions={props.actions || []}
                    busy={busy}
                    onSetDryRun={(value) => call("set_dry_run", { value })}
                  />
                ),
              },
            ]}
          />
        </Stack>
      </ErrorBoundary>
    </Page>
  )
}
