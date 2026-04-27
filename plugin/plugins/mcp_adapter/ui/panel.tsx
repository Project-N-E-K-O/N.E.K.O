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
} from "@neko/plugin-ui"

export default function McpAdapterPanel({ plugin, state, entries, actions }) {
  const safePlugin = plugin || {}
  const safeState = state || {}
  const safeEntries = Array.isArray(entries) ? entries : []
  const safeActions = Array.isArray(actions) ? actions : []
  const servers = Array.isArray(safeState.servers) ? safeState.servers : []
  const connectedServers = servers.filter((server) => server.connected)
  const addServer = safeActions.find((action) => action.id === "add_server")
  const connectServer = safeActions.find((action) => action.id === "connect_server")
  const disconnectServer = safeActions.find((action) => action.id === "disconnect_server")
  const removeServers = safeActions.find((action) => action.id === "remove_servers")
  const firstServer = servers[0]
  let selectedServerName = firstServer?.name || ""
  let importJson = `{
  "name": "example",
  "transport": "stdio",
  "command": "uvx",
  "args": ["mcp-server-example"],
  "enabled": true,
  "auto_connect": true
}`

  const importServer = async () => {
    if (!addServer) {
      alert("add_server action is unavailable")
      return
    }
    try {
      const payload = JSON.parse(importJson)
      await api.call("add_server", payload)
      await api.refresh()
    } catch (error) {
      alert(error && error.message ? error.message : String(error))
    }
  }

  return (
    <Page
      title={safePlugin.name || "MCP Adapter"}
      subtitle="连接 MCP servers，并把它们的 tools 暴露为 N.E.K.O 插件入口。"
    >
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={connectedServers.length > 0 ? "success" : "warning"}>
            {connectedServers.length > 0 ? "运行中" : "待连接"}
          </StatusBadge>
        </ToolbarGroup>
        <ToolbarGroup>
          <RefreshButton>刷新状态</RefreshButton>
        </ToolbarGroup>
      </Toolbar>

      <Grid cols={4}>
        <StatCard label="已配置 Server" value={safeState.total_servers || 0} />
        <StatCard label="已连接 Server" value={safeState.connected_servers || 0} />
        <StatCard label="已发现 Tools" value={safeState.total_tools || 0} />
        <StatCard label="插件入口" value={safeEntries.length} />
      </Grid>

      <Grid cols={2}>
        <Card title="连接状态">
          <Stack>
            <StatusBadge tone={connectedServers.length > 0 ? "success" : "warning"}>
              {connectedServers.length > 0 ? "已有 MCP Server 在线" : "暂无在线 MCP Server"}
            </StatusBadge>
            <Text>
              配置 MCP Server 后，点击连接即可发现 tools。发现到的 tools 会通过 Adapter 网关注册为可调用入口。
            </Text>
            <Progress label="连接率" value={servers.length > 0 ? Math.round((connectedServers.length / servers.length) * 100) : 0} />
          </Stack>
        </Card>

        <Card title="当前插件">
          <Stack>
            <KeyValue
              data={{
                ID: safePlugin.id || "mcp_adapter",
                Version: safePlugin.version || "-",
                Type: safePlugin.type || "adapter",
                Entries: safeEntries.length,
              }}
            />
          </Stack>
        </Card>
      </Grid>

      <Card title="MCP Servers">
        {servers.length > 0 ? (
          <Stack>
            <DataTable
              data={servers}
              rowKey="name"
              selectedKey={selectedServerName}
              onSelect={(server) => {
                selectedServerName = server?.name || ""
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
              {connectServer && selectedServerName ? (
                <ActionButton action={connectServer} values={{ server_name: selectedServerName }} />
              ) : null}
              {disconnectServer && selectedServerName ? (
                <ActionButton action={disconnectServer} values={{ server_name: selectedServerName }} />
              ) : null}
              {removeServers && selectedServerName ? (
                <ActionButton action={removeServers} values={{ server_names: [selectedServerName] }} />
              ) : null}
            </ButtonGroup>
            <Text>点击表格行选择 Server，再执行连接、断开或移除操作。</Text>
          </Stack>
        ) : (
          <EmptyState
            title="暂无 MCP Server"
            description="使用下方表单或 JSON 导入添加第一个 MCP Server。"
          />
        )}
      </Card>

      {addServer ? (
        <Card title="添加 MCP Server">
          <ActionForm action={addServer} submitLabel="添加并连接" />
        </Card>
      ) : (
        <Alert tone="warning">当前上下文没有暴露 add_server 动作，无法使用自动表单。</Alert>
      )}

      <Card title="从 JSON 导入 Server">
        <Stack>
          <Text>粘贴一个 MCP server 配置 JSON，字段会直接传给 add_server 入口。</Text>
          <Textarea
            value={importJson}
            onChange={(value) => {
              importJson = value
            }}
          />
          <Button tone="success" onClick={importServer}>导入 JSON</Button>
        </Stack>
      </Card>

      <Card title="插件入口预览">
        <DataTable
          data={safeEntries.slice(0, 8)}
          columns={[
            { key: "id", label: "入口 ID" },
            { key: "name", label: "名称" },
            { key: "description", label: "描述" },
          ]}
        />
        <Divider />
        <Tip>连接 MCP Server 后，发现到的 tools 会动态注册为入口，并出现在这里。</Tip>
      </Card>
    </Page>
  )
}
