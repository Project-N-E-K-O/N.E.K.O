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

  return (
    <Page
      title={safePlugin.name || "MCP Adapter"}
      subtitle="连接 MCP servers，并把它们的 tools 暴露为 N.E.K.O 插件入口。"
    >
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
          </Stack>
        </Card>

        <Card title="当前插件">
          <KeyValue
            data={{
              ID: safePlugin.id || "mcp_adapter",
              Version: safePlugin.version || "-",
              Type: safePlugin.type || "adapter",
              Entries: safeEntries.length,
            }}
          />
        </Card>
      </Grid>

      <Card title="MCP Servers">
        {servers.length > 0 ? (
          <Stack>
            <DataTable
              data={servers}
              columns={[
                { key: "name", label: "Server" },
                { key: "transport", label: "Transport" },
                { key: "connected", label: "Connected" },
                { key: "tools_count", label: "Tools" },
                { key: "error", label: "Error" },
              ]}
            />
            <ButtonGroup>
              {connectServer && firstServer ? (
                <ActionButton action={connectServer} values={{ server_name: firstServer.name }} />
              ) : null}
              {disconnectServer && firstServer ? (
                <ActionButton action={disconnectServer} values={{ server_name: firstServer.name }} />
              ) : null}
              {removeServers && firstServer ? (
                <ActionButton action={removeServers} values={{ server_names: [firstServer.name] }} />
              ) : null}
            </ButtonGroup>
            <Text>快捷操作默认作用于第一条 Server；完整多选操作后续可再扩展。</Text>
          </Stack>
        ) : (
          <Stack>
            <Warning>当前还没有配置 MCP Server。你可以用下面的表单添加一个。</Warning>
          </Stack>
        )}
      </Card>

      {addServer ? (
        <Card title="添加 MCP Server">
          <ActionForm action={addServer} submitLabel="添加并连接" />
        </Card>
      ) : null}

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
