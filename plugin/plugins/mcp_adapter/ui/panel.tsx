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
  ButtonGroup,
  KeyValue,
  Divider,
  Textarea,
  Button,
  Field,
  Input,
  Select,
  Switch,
  RefreshButton,
  Toolbar,
  ToolbarGroup,
  EmptyState,
  Alert,
  Progress,
  CodeBlock,
  Steps,
  Step,
  useForm,
  useToast,
  useConfirm,
  useDebouncedState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type McpServerView = {
  name?: string
  transport?: string
  connected?: boolean
  tools_count?: number
  error?: string | null
  tools?: Array<{ name?: string; description?: string }>
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

type ImportServerConfig = {
  name?: unknown
  transport?: unknown
  type?: unknown
  command?: unknown
  args?: unknown
  url?: unknown
  env?: unknown
  enabled?: unknown
  autoConnect?: unknown
}

const defaultImportJson = `{
  "name": "example",
  "transport": "stdio",
  "command": "uvx",
  "args": ["mcp-server-example"],
  "enabled": true,
  "auto_connect": true
}`

const emptyServerForm = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  url: "",
  env: "",
  autoConnect: true,
}
type ServerFormValues = typeof emptyServerForm

const transportOptions = [
  { value: "stdio", label: "stdio" },
  { value: "sse", label: "sse" },
  { value: "streamable-http", label: "streamable-http" },
]

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
  const [selectedServerNames, setSelectedServerNames] = props.useLocalState<string[]>("selectedServerNames", [])
  const effectiveSelectedServerName = selectedServerName || firstServer?.name || ""
  const importErrorId = "mcp-adapter-import-error"
  const configExample = `[mcp_servers.example]
transport = "stdio"
command = "uvx"
args = ["mcp-server-example"]
enabled = true
auto_connect = true`
  const [importJson, setImportJson] = props.useLocalState("importJson", defaultImportJson)
  const [importAutoConnect, setImportAutoConnect] = props.useLocalState("importAutoConnect", true)
  const serverForm = useForm(emptyServerForm)
  const [formMessage, setFormMessage] = props.useLocalState("serverFormMessage", "")
  const [formMessageTone, setFormMessageTone] = props.useLocalState("serverFormMessageTone", "danger")
  const [filterText, setFilterText, debouncedFilter] = useDebouncedState("", 180)
  const toast = useToast()
  const confirm = useConfirm()

  const selectedNames = selectedServerNames.filter((name) => servers.some((server) => server.name === name))
  const effectiveActionServerName = selectedNames[0] || effectiveSelectedServerName
  const normalizedFilter = String(debouncedFilter || "").trim().toLowerCase()
  const filteredEntries = normalizedFilter
    ? safeEntries.filter((entry) => [entry.id, entry.name, entry.description].some((value) => String(value || "").toLowerCase().includes(normalizedFilter)))
    : safeEntries
  const visibleServers = normalizedFilter
    ? servers.filter((server) => [
        server.name,
        server.transport,
        server.error,
        ...(Array.isArray(server.tools) ? server.tools.map((tool) => `${tool.name || ""} ${tool.description || ""}`) : []),
      ].some((value) => String(value || "").toLowerCase().includes(normalizedFilter)))
    : servers

  const updateServerForm = <K extends keyof ServerFormValues>(patch: Pick<ServerFormValues, K>) => {
    (Object.keys(patch) as K[]).forEach((key) => serverForm.setField(key, patch[key]))
    setFormMessage("")
  }

  const parseArgs = (value) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean)

  const buildServerPayload = (server: Partial<ServerFormValues> | ImportServerConfig, autoConnectOverride?: boolean) => {
    const name = String(server.name || "").trim()
    const transport = String(server.transport || "stdio").trim() || "stdio"
    if (!name) throw new Error(t("panel.form.errors.nameRequired"))
    const payload: Record<string, any> = {
      name,
      transport,
      auto_connect: autoConnectOverride === undefined ? !!server.autoConnect : !!autoConnectOverride,
    }
    if ("enabled" in server && server.enabled !== undefined) payload.enabled = !!server.enabled
    if (transport === "stdio") {
      const command = String(server.command || "").trim()
      if (!command) throw new Error(t("panel.form.errors.commandRequired"))
      payload.command = command
      const args = Array.isArray(server.args) ? server.args : parseArgs(server.args)
      if (args.length > 0) payload.args = args
    } else {
      const url = String(server.url || "").trim()
      if (!url) throw new Error(t("panel.form.errors.urlRequired"))
      payload.url = url
    }
    if (server.env && typeof server.env === "object" && !Array.isArray(server.env)) {
      payload.env = server.env
    } else if (String(server.env || "").trim()) {
      payload.env = JSON.parse(String(server.env))
    }
    return payload
  }

  const parseMcpConfig = (jsonText: string): ImportServerConfig[] => {
    const data = JSON.parse(jsonText)
    const serverList = Array.isArray(data)
      ? data
      : (data && typeof data === "object" && typeof data.name === "string")
        ? [data]
        : Object.entries((data && data.mcpServers) || data || {}).map(([name, config]) => ({
            ...(config && typeof config === "object" ? config : {}),
            name,
          }))

    return serverList
      .filter((server): server is ImportServerConfig => !!server && typeof server === "object")
      .map((server) => {
        const type = String(server.type || server.transport || "").trim()
        const typeMap = {
          stdio: "stdio",
          sse: "sse",
          streamable_http: "streamable-http",
          "streamable-http": "streamable-http",
          http: "streamable-http",
        }
        const transport = typeMap[type] || (server.url ? (String(server.url).includes("/sse") ? "sse" : "streamable-http") : "stdio")
        return {
          name: server.name,
          transport,
          command: server.command,
          args: server.args,
          url: server.url,
          env: server.env,
          enabled: server.enabled,
        }
      })
  }

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
      const serversToImport = parseMcpConfig(importJson)
      if (serversToImport.length === 0) {
        setImportError(t("panel.import.noServers"))
        return
      }
      const succeeded: string[] = []
      const failed: Array<{ name: string; error: string }> = []
      for (const server of serversToImport) {
        try {
          const payload = buildServerPayload(server, importAutoConnect)
          await props.api.call("add_server", payload)
          succeeded.push(payload.name)
        } catch (error) {
          failed.push({
            name: String(server.name || t("panel.import.unknownServer")),
            error: error && error.message ? error.message : String(error),
          })
        }
      }
      await props.api.refresh()
      setImportError(t("panel.import.result", { success: succeeded.length, failed: failed.length }) + (failed.length > 0 ? `\n${failed.map((item) => `- ${item.name}: ${item.error}`).join("\n")}` : ""))
    } catch (error) {
      setImportError(error && error.message ? error.message : String(error))
    }
  }

  const addServerFromForm = async () => {
    if (!addServer) {
      setFormMessageTone("danger")
      setFormMessage(t("panel.errors.addServerUnavailable"))
      return
    }
    try {
      const payload = buildServerPayload(serverForm.values)
      await props.api.call("add_server", payload)
      await props.api.refresh()
      serverForm.reset()
      setFormMessageTone("success")
      setFormMessage(t("panel.form.added", { name: payload.name }))
      toast.success(t("panel.form.added", { name: payload.name }))
    } catch (error) {
      setFormMessageTone("danger")
      setFormMessage(error && error.message ? error.message : String(error))
      toast.error(error && error.message ? error.message : String(error))
    }
  }

  const toggleSelectedServer = (serverName) => {
    if (!serverName) return
    setSelectedServerName(serverName)
    setSelectedServerNames((previous) => {
      const set = new Set(previous)
      if (set.has(serverName)) set.delete(serverName)
      else set.add(serverName)
      return Array.from(set)
    })
  }

  const removeSelectedServers = async () => {
    if (!removeServers || selectedNames.length === 0) return
    const accepted = await confirm({
      title: t("panel.servers.removeConfirmTitle"),
      message: t("panel.servers.removeConfirmMessage", { count: selectedNames.length }),
      tone: "danger",
      confirmLabel: t("panel.servers.removeConfirmAction"),
      cancelLabel: t("panel.servers.removeConfirmCancel"),
    })
    if (!accepted) return
    await props.api.call("remove_servers", { server_names: selectedNames })
    setSelectedServerNames([])
    await props.api.refresh()
    toast.success(t("panel.servers.removed", { count: selectedNames.length }))
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

      <Card title={t("panel.filter.title")}>
        <Field label={t("panel.filter.label")} help={t("panel.filter.help")}>
          <Input value={filterText} placeholder={t("panel.filter.placeholder")} onChange={setFilterText} />
        </Field>
      </Card>

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
        {visibleServers.length > 0 ? (
          <Stack>
            <DataTable
              data={visibleServers}
              rowKey="name"
              selectedKey={effectiveActionServerName}
              onSelect={(server) => {
                setSelectedServerName(server?.name || "")
              }}
              columns={[
                {
                  key: "selected",
                  label: t("panel.servers.columns.selected"),
                  render: (server) => (
                    <Switch
                      checked={selectedNames.includes(server?.name || "")}
                      onChange={() => toggleSelectedServer(server?.name || "")}
                    />
                  ),
                },
                { key: "name", label: "Server" },
                { key: "transport", label: "Transport" },
                { key: "connected", label: "Connected" },
                { key: "tools_count", label: "Tools" },
                {
                  key: "tools",
                  label: t("panel.servers.columns.toolNames"),
                  render: (server) => {
                    const tools = Array.isArray(server?.tools) ? server.tools : []
                    return tools.length > 0
                      ? tools.slice(0, 6).map((tool) => tool?.name || "").filter(Boolean).join(", ")
                      : ""
                  },
                },
                { key: "error", label: "Error" },
              ]}
            />
            <ButtonGroup>
              {connectServer && effectiveActionServerName ? (
                <ActionButton action={connectServer} values={{ server_name: effectiveActionServerName }} />
              ) : null}
              {disconnectServer && effectiveActionServerName ? (
                <ActionButton action={disconnectServer} values={{ server_name: effectiveActionServerName }} />
              ) : null}
              {removeServers && effectiveActionServerName ? (
                <ActionButton action={removeServers} values={{ server_names: [effectiveActionServerName] }} />
              ) : null}
              {removeServers && selectedNames.length > 0 ? (
                <Button tone="danger" onClick={removeSelectedServers}>{t("panel.servers.removeSelected", { count: selectedNames.length })}</Button>
              ) : null}
            </ButtonGroup>
            <Text>{normalizedFilter ? t("panel.filter.serverResult", { count: visibleServers.length }) : t("panel.servers.selectionHint")}</Text>
          </Stack>
        ) : (
          <EmptyState
            title={t("panel.servers.empty.title")}
            description={t("panel.servers.empty.description")}
          />
        )}
      </Card>

      <Grid cols={2}>
        <Card title={t("panel.addServer.title")}>
          {formMessage ? <Alert tone={formMessageTone === "success" ? "success" : "danger"}>{formMessage}</Alert> : null}
          {addServer ? (
            <Stack>
              <Field label={t("panel.form.name")} required>
                <Input value={serverForm.values.name} placeholder="my_server" onChange={(value) => updateServerForm({ name: value })} />
              </Field>
              <Field label={t("panel.form.transport")} required>
                <Select value={serverForm.values.transport} options={transportOptions} onChange={(value) => updateServerForm({ transport: value })} />
              </Field>
              {serverForm.values.transport === "stdio" ? (
                <>
                  <Field label={t("panel.form.command")} required>
                    <Input value={serverForm.values.command} placeholder="uvx" onChange={(value) => updateServerForm({ command: value })} />
                  </Field>
                  <Field label={t("panel.form.args")} help={t("panel.form.argsHelp")}>
                    <Input value={serverForm.values.args} placeholder="mcp-server-example, /tmp" onChange={(value) => updateServerForm({ args: value })} />
                  </Field>
                </>
              ) : (
                <Field label={t("panel.form.url")} required>
                  <Input value={serverForm.values.url} placeholder="https://example.com/mcp" onChange={(value) => updateServerForm({ url: value })} />
                </Field>
              )}
              <Field label={t("panel.form.env")} help={t("panel.form.envHelp")}>
                <Textarea value={serverForm.values.env} placeholder='{"TOKEN":"..."}' onChange={(value) => updateServerForm({ env: value })} />
              </Field>
              <Switch checked={serverForm.values.autoConnect} label={t("panel.form.autoConnect")} onChange={(value) => updateServerForm({ autoConnect: value })} />
              <Button tone="success" onClick={addServerFromForm}>{t("panel.addServer.submit")}</Button>
            </Stack>
          ) : (
            <Alert tone="warning">{t("panel.errors.addServerFormUnavailable")}</Alert>
          )}
        </Card>

        <Card title={t("panel.import.title")}>
          <Stack>
            <Text>{t("panel.import.description")}</Text>
            <Switch checked={importAutoConnect} label={t("panel.import.autoConnect")} onChange={setImportAutoConnect} />
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
          data={filteredEntries.slice(0, 12)}
          columns={[
            { key: "id", label: t("panel.entries.columns.id") },
            { key: "name", label: t("panel.entries.columns.name") },
            { key: "description", label: t("panel.entries.columns.description") },
          ]}
        />
        <Divider />
        <Tip>{normalizedFilter ? t("panel.filter.entryResult", { count: filteredEntries.length }) : t("panel.entries.tip")}</Tip>
      </Card>

      <Warning>
        {t("panel.warnings.stdio")}
      </Warning>
    </Page>
  )
}
