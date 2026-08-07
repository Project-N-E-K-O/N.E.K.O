import {
  ErrorBoundary,
  Inline,
  Page,
  RefreshButton,
  Stack,
  Tabs,
  useEffect,
  useRef,
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

const AUTO_REFRESH_INTERVAL_MS = 2000

export default function NekoWowsPanel(props: PluginSurfaceProps<DashboardState>) {
  const state = props.state || {}
  const [busy, setBusy] = useState(false)
  const refreshInFlight = useRef(false)

  useEffect(() => {
    let disposed = false
    const refresh = async () => {
      if (disposed || refreshInFlight.current) return
      refreshInFlight.current = true
      try {
        await props.api.refresh()
      } catch {
        // A transient panel refresh failure must not stop future polling.
      } finally {
        refreshInFlight.current = false
      }
    }
    const timer = globalThis.setInterval(
      () => void refresh(), AUTO_REFRESH_INTERVAL_MS)
    return () => {
      disposed = true
      globalThis.clearInterval(timer)
    }
  }, [props.api])

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
      title={props.t("panel.title")}
      subtitle={props.t("panel.subtitle")}
    >
      <ErrorBoundary title={props.t("panel.renderError")}>
        <Stack gap={12}>
          <Inline gap={8} justify="end">
            <RefreshButton />
          </Inline>

          <Tabs
            id="neko-wows-panel"
            items={[
              {
                id: "overview",
                label: props.t("panel.tabs.overview"),
                content: (
                  <OverviewSection state={state} t={props.t} locale={props.locale} />
                ),
              },
              {
                id: "timeline",
                label: props.t("panel.tabs.timeline"),
                content: (
                  <TimelineSection
                    entries={state.timeline || []}
                    t={props.t}
                    locale={props.locale}
                  />
                ),
              },
              {
                id: "documents",
                label: props.t("panel.tabs.documents"),
                content: (
                  <DocumentsSection
                    documents={state.documents || {}}
                    actions={props.actions || []}
                    busy={busy}
                    t={props.t}
                    locale={props.locale}
                  />
                ),
              },
              {
                id: "prompts",
                label: props.t("panel.tabs.prompts"),
                content: (
                  <PromptsSection
                    prompts={state.prompts || {}}
                    actions={props.actions || []}
                    busy={busy}
                    t={props.t}
                    locale={props.locale}
                  />
                ),
              },
              {
                id: "preferences",
                label: props.t("panel.tabs.preferences"),
                content: (
                  <PreferencesSection
                    config={state.config || {}}
                    arbiter={state.arbiter || {}}
                    runtimeNow={state.runtime_now || 0}
                    categories={state.categories || []}
                    lanes={state.lanes || []}
                    busy={busy}
                    t={props.t}
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
                label: props.t("panel.tabs.diagnostics"),
                content: (
                  <DiagnosticsSection
                    state={state}
                    actions={props.actions || []}
                    busy={busy}
                    t={props.t}
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
