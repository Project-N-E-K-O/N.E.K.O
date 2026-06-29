import {
  Page,
  Card,
  Stack,
  Text,
  Button,
  StatusBadge,
  useRef,
  useEffect,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type HistoryItem = {
  type: "input" | "output"
  content: string
  timestamp?: number
}

type TerminalState = {
  history?: HistoryItem[]
  current_agent?: string
  is_running?: boolean
}

function actionById(actions: HostedAction[], id: string): HostedAction | undefined {
  return actions.find((action) => action.id === id || action.entry_id === id)
}

export default function TerminalPanel(props: PluginSurfaceProps<TerminalState>) {
  const { state, actions, t } = props
  const safeState = state || {}
  const history = Array.isArray(safeState.history) ? safeState.history : []
  const [command, setCommand] = useState("")
  const [commandHistory, setCommandHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const historyRef = useRef<HTMLDivElement>(null)
  const toast = useToast()

  const executeAction = actionById(actions || [], "execute_command")
  const clearAction = actionById(actions || [], "clear_terminal")

  useEffect(() => {
    if (historyRef.current) {
      historyRef.current.scrollTop = historyRef.current.scrollHeight
    }
  }, [history])

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus()
    }
  }, [])

  async function handleExecute() {
    if (!command.trim() || !executeAction) {
      return
    }
    
    try {
      setCommandHistory((prev) => [...prev, command])
      await props.api.call("execute_command", { command: command.trim() })
      await props.api.refresh()
      setCommand("")
      setHistoryIndex(-1)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter") {
      handleExecute()
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      if (commandHistory.length > 0) {
        const newIndex = historyIndex < commandHistory.length - 1 ? historyIndex + 1 : historyIndex
        setHistoryIndex(newIndex)
        setCommand(commandHistory[commandHistory.length - 1 - newIndex] || "")
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault()
      if (historyIndex > 0) {
        const newIndex = historyIndex - 1
        setHistoryIndex(newIndex)
        setCommand(commandHistory[commandHistory.length - 1 - newIndex] || "")
      } else if (historyIndex === 0) {
        setHistoryIndex(-1)
        setCommand("")
      }
    }
  }

  async function handleClear() {
    if (!clearAction) return
    try {
      await props.api.call("clear_terminal")
      await props.api.refresh()
      setCommandHistory([])
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Card title={
        <Stack inline spacing="sm">
          <Text>Terminal</Text>
          {safeState.is_running && safeState.current_agent ? (
            <StatusBadge tone="success" label={t("panel.status.running", { agent: safeState.current_agent })} />
          ) : (
            <StatusBadge tone="default" label={t("panel.status.idle")} />
          )}
        </Stack>
      }>
        <div 
          ref={historyRef}
          className="terminal-history"
          style={{
            backgroundColor: "#0d1117",
            color: "#c9d1d9",
            fontFamily: '"JetBrains Mono", "Fira Code", "Consolas", monospace',
            padding: "12px",
            minHeight: "300px",
            maxHeight: "400px",
            overflowY: "auto",
            borderRadius: "8px",
            border: "1px solid #30363d",
            marginBottom: "12px",
          }}
        >
          {history.length === 0 ? (
            <Text style={{ opacity: 0.6 }}>
              {t("panel.welcome")}
            </Text>
          ) : (
            history.map((item, index) => (
              <div key={index} style={{ marginBottom: "8px" }}>
                {item.type === "input" ? (
                  <div style={{ display: "flex" }}>
                    <span style={{ color: "#58a6ff", marginRight: "8px" }}>$</span>
                    <span style={{ color: "#d2a8ff" }}>{item.content}</span>
                  </div>
                ) : (
                  <div style={{ 
                    whiteSpace: "pre-wrap",
                    color: "#8b949e",
                    paddingLeft: "20px",
                  }}>
                    {item.content}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <div style={{ display: "flex", gap: "8px" }}>
          <span style={{ 
            color: "#58a6ff", 
            fontFamily: '"JetBrains Mono", monospace',
            padding: "8px 0",
          }}>$</span>
          {/* hosted ui-kit 的 Input 不透传 onKeyDown/ref，回车执行与历史翻页必须用原生 input */}
          <input
            ref={inputRef}
            className="neko-input"
            value={command}
            onInput={(e: Event) => setCommand((e.target as HTMLInputElement).value)}
            onCompositionEnd={(e: Event) => setCommand((e.target as HTMLInputElement).value)}
            onKeyDown={handleKeyDown}
            placeholder={t("panel.placeholder")}
            style={{ flex: 1 }}
          />
          <Button tone="primary" onClick={handleExecute} disabled={!command.trim()}>
            {t("panel.actions.execute")}
          </Button>
        </div>

        <div style={{ marginTop: "16px" }}>
          <Button tone="default" onClick={handleClear} disabled={history.length === 0}>
            {t("panel.actions.clear")}
          </Button>
        </div>

        <div style={{ 
          marginTop: "16px",
          padding: "12px",
          backgroundColor: "#161b22",
          borderRadius: "8px",
          border: "1px solid #30363d",
        }}>
          <Text style={{ fontSize: "12px", color: "#8b949e" }}>
            <strong style={{ color: "#58a6ff" }}>{t("panel.help.title")}</strong>
          </Text>
          <div style={{ marginTop: "8px", fontSize: "12px", color: "#8b949e" }}>
            <div><code style={{ color: "#a5d6ff" }}>claude</code> - {t("panel.help.claude")}</div>
            <div><code style={{ color: "#a5d6ff" }}>copilot</code> - {t("panel.help.copilot")}</div>
            <div><code style={{ color: "#a5d6ff" }}>stop</code> - {t("panel.help.stop")}</div>
            <div><code style={{ color: "#a5d6ff" }}>status</code> - {t("panel.help.status")}</div>
            <div><code style={{ color: "#a5d6ff" }}>help</code> - {t("panel.help.help")}</div>
            <div><code style={{ color: "#a5d6ff" }}>clear</code> - {t("panel.help.clear")}</div>
          </div>
        </div>
      </Card>
    </Page>
  )
}