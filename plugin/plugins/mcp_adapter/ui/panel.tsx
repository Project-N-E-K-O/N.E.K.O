import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Tip,
  Warning,
  StatCard,
  StatusBadge,
  DataTable,
  ActionButton,
  ActionForm,
  ButtonGroup,
  KeyValue,
  Divider,
  Textarea,
  Button,
  RefreshButton,
  Toolbar,
  ToolbarGroup,
  EmptyState,
  Alert,
  Progress,
  CodeBlock,
  Steps,
  Step,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type McpServerView = {
  name?: string
  transport?: string
  connected?: boolean
  tools_count?: number
  error?: string | null
}

type McpPanelState = {
  connected_servers?: number
  total_servers?: number
  total_tools?: number
  servers?: McpServerView[]
}

type PluginEntryView = {
  id?: string
  name?: string
  description?: string
}

const defaultImportJson = `{
  "name": "example",
  "transport": "stdio",
  "command": "uvx",
  "args": ["mcp-server-example"],
  "enabled": true,
  "auto_connect": true
}`

export default function McpAdapterPanel(props: PluginSurfaceProps<McpPanelState>) {
  const { plugin, state, entries, actions } = props
  const { t } = props
  const safePlugin = plugin || {}
  const safeState = state || {}
  const safeEntries = Array.isArray(entries) ? entries as PluginEntryView[] : []
  const safeActions = Array.isArray(actions) ? actions as HostedAction[] : []
  const servers = Array.isArray(safeState.servers) ? safeState.servers : []
  const connectedServers = servers.filter((server) => server.connected)
  const disconnectedServers = servers.filter((server) => !server.connected)
  const errorServers = servers.filter((server) => server.error)
  const addServer = safeActions.find((action) => action.id === "add_server")
  const connectServer = safeActions.find((action) => action.id === "connect_server")
  const disconnectServer = safeActions.find((action) => action.id === "disconnect_server")
  const removeServers = safeActions.find((action) => action.id === "remove_servers")
  const firstServer = servers[0]
  const [selectedServerName, setSelectedServerName] = props.useLocalState("selectedServerName", firstServer?.name || "")
  const effectiveSelectedServerName = selectedServerName || firstServer?.name || ""
  const importErrorId = "mcp-adapter-import-error"
  const configExample = `[mcp_servers.example]
transport = "stdio"
command = "uvx"
args = ["mcp-server-example"]
enabled = true
auto_connect = true`
  const [importJson, setImportJson] = props.useLocalState("importJson", defaultImportJson)

  const setImportError = (message) => {
    const node = document.getElementById(importErrorId)
    if (!node) return
    node.textContent = message || ""
    node.hidden = !message
  }

  const importServer = async () => {
    setImportError("")
    if (!addServer) {
      setImportError(t("panel.errors.addServerUnavailable"))
      return
    }
    try {
      const payload = JSON.parse(importJson)
      await props.api.call("add_server", payload)
      await props.api.refresh()
    } catch (error) {
      setImportError(error && error.message ? error.message : String(error))
    }
  }

  return (
    <Page
      title={safePlugin.name || "MCP Adapter"}
      subtitle={t("panel.subtitle")}
    >
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={connectedServers.length > 0 ? "success" : "warning"}>
            {connectedServers.length > 0 ? t("panel.status.gatewayOnline") : t("panel.status.waiting")}
          </StatusBadge>
          {errorServers.length > 0 ? <StatusBadge tone="danger">{t("panel.status.errorCount", { count: errorServers.length })}</StatusBadge> : null}
        </ToolbarGroup>
        <ToolbarGroup>
          <RefreshButton>{t("panel.actions.refresh")}</RefreshButton>
        </ToolbarGroup>
      </Toolbar>

      <Grid cols={4}>
        <StatCard label={t("panel.stats.configuredServers")} value={safeState.total_servers || 0} />
        <StatCard label={t("panel.stats.connectedServers")} value={safeState.connected_servers || 0} />
        <StatCard label={t("panel.stats.discoveredTools")} value={safeState.total_tools || 0} />
        <StatCard label={t("panel.stats.pluginEntries")} value={safeEntries.length} />
      </Grid>

      {errorServers.length > 0 ? (
        <Alert tone="danger">
          {t("panel.alerts.serverErrors")}
        </Alert>
      ) : null}

      <Grid cols={2}>
        <Card title={t("panel.gateway.title")}>
          <Stack>
            <Progress label={t("panel.gateway.connectionRate")} value={servers.length > 0 ? Math.round((connectedServers.length / servers.length) * 100) : 0} />
            <KeyValue
              items={[
                { key: "online", label: t("panel.gateway.online"), value: connectedServers.length },
                { key: "offline", label: t("panel.gateway.offline"), value: disconnectedServers.length },
                { key: "errors", label: t("panel.gateway.errors"), value: errorServers.length },
                { key: "adapter", label: "Adapter", value: safePlugin.id || "mcp_adapter" },
              ]}
            />
          </Stack>
        </Card>

        <Card title={t("panel.flow.title")}>
          <Steps>
            <Step index="1" title={t("panel.flow.addServer.title")}>
              <Text>{t("panel.flow.addServer.body")}</Text>
            </Step>
            <Step index="2" title={t("panel.flow.connect.title")}>
              <Text>{t("panel.flow.connect.body")}</Text>
            </Step>
            <Step index="3" title={t("panel.flow.invoke.title")}>
              <Text>{t("panel.flow.invoke.body")}</Text>
            </Step>
          </Steps>
        </Card>
      </Grid>

      <Card title="MCP Servers">
        {servers.length > 0 ? (
          <Stack>
            <DataTable
              data={servers}
              rowKey="name"
              selectedKey={effectiveSelectedServerName}
              onSelect={(server) => {
                setSelectedServerName(server?.name || "")
              }}
              columns={[
                { key: "name", label: "Server" },
                { key: "transport", label: "Transport" },
                { key: "connected", label: "Connected" },
                { key: "tools_count", label: "Tools" },
                { key: "error", label: "Error" },
              ]}
            />
            <ButtonGroup>
              {connectServer && effectiveSelectedServerName ? (
                <ActionButton action={connectServer} values={{ server_name: effectiveSelectedServerName }} />
              ) : null}
              {disconnectServer && effectiveSelectedServerName ? (
                <ActionButton action={disconnectServer} values={{ server_name: effectiveSelectedServerName }} />
              ) : null}
              {removeServers && effectiveSelectedServerName ? (
                <ActionButton action={removeServers} values={{ server_names: [effectiveSelectedServerName] }} />
              ) : null}
            </ButtonGroup>
            <Text>{t("panel.servers.selectionHint")}</Text>
          </Stack>
        ) : (
          <EmptyState
            title={t("panel.servers.empty.title")}
            description={t("panel.servers.empty.description")}
          />
        )}
      </Card>

      <Grid cols={2}>
        {addServer ? (
          <Card title={t("panel.addServer.title")}>
            <ActionForm action={addServer} submitLabel={t("panel.addServer.submit")} />
          </Card>
        ) : (
          <Card title={t("panel.addServer.title")}>
            <Alert tone="warning">{t("panel.errors.addServerFormUnavailable")}</Alert>
          </Card>
        )}

        <Card title={t("panel.import.title")}>
          <Stack>
            <Text>{t("panel.import.description")}</Text>
            <Textarea
              value={importJson}
              onChange={(value) => {
                setImportJson(value)
                setImportError("")
              }}
            />
            <p id={importErrorId} className="neko-action-error" hidden></p>
            <Button tone="success" onClick={importServer}>{t("panel.import.submit")}</Button>
          </Stack>
        </Card>
      </Grid>

      <Grid cols={2}>
        <Card title={t("panel.examples.minimalConfig")}>
          <CodeBlock>{configExample}</CodeBlock>
        </Card>

        <Card title={t("panel.transport.title")}>
          <KeyValue
            items={[
              { key: "stdio", label: "stdio", value: t("panel.transport.stdio") },
              { key: "sse", label: "sse", value: t("panel.transport.sse") },
              { key: "streamable-http", label: "streamable-http", value: t("panel.transport.streamableHttp") },
              { key: "security", label: t("panel.transport.security"), value: t("panel.transport.securityDescription") },
            ]}
          />
        </Card>
      </Grid>

      <Card title={t("panel.entries.title")}>
        <DataTable
          data={safeEntries.slice(0, 12)}
          columns={[
            { key: "id", label: t("panel.entries.columns.id") },
            { key: "name", label: t("panel.entries.columns.name") },
            { key: "description", label: t("panel.entries.columns.description") },
          ]}
        />
        <Divider />
        <Tip>{t("panel.entries.tip")}</Tip>
      </Card>

      <Warning>
        {t("panel.warnings.stdio")}
      </Warning>
    </Page>
  )
}
