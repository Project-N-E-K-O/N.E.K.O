/// <reference path="../../../sdk/plugin/ui_types/neko-plugin-ui.d.ts" />
/** @jsx h */
/** @jsxFrag Fragment */
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

export default function McpAdapterPanel({ plugin, state, entries, actions }) {
  const safePlugin = plugin || {}
  const safeState = state || {}
  const safeEntries = Array.isArray(entries) ? entries : []
  const safeActions = Array.isArray(actions) ? actions : []
  const servers = Array.isArray(safeState.servers) ? safeState.servers : []
  const connectedServers = servers.filter((server) => server.connected)
  const disconnectedServers = servers.filter((server) => !server.connected)
  const errorServers = servers.filter((server) => server.error)
  const addServer = safeActions.find((action) => action.id === "add_server")
  const connectServer = safeActions.find((action) => action.id === "connect_server")
  const disconnectServer = safeActions.find((action) => action.id === "disconnect_server")
  const removeServers = safeActions.find((action) => action.id === "remove_servers")
  const firstServer = servers[0]
  let selectedServerName = firstServer?.name || ""
  const importErrorId = "mcp-adapter-import-error"
  const configExample = `[mcp_servers.example]
transport = "stdio"
command = "uvx"
args = ["mcp-server-example"]
enabled = true
auto_connect = true`
  let importJson = `{
  "name": "example",
  "transport": "stdio",
  "command": "uvx",
  "args": ["mcp-server-example"],
  "enabled": true,
  "auto_connect": true
}`

  const setImportError = (message) => {
    const node = document.getElementById(importErrorId)
    if (!node) return
    node.textContent = message || ""
    node.hidden = !message
  }

  const importServer = async () => {
    setImportError("")
    if (!addServer) {
      setImportError("add_server action is unavailable")
      return
    }
    try {
      const payload = JSON.parse(importJson)
      await api.call("add_server", payload)
      await api.refresh()
    } catch (error) {
      setImportError(error && error.message ? error.message : String(error))
    }
  }

  return (
    <Page
      title={safePlugin.name || "MCP Adapter"}
      subtitle="管理 MCP Server 连接、发现 tools，并把外部能力发布为 N.E.K.O 插件入口。"
    >
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={connectedServers.length > 0 ? "success" : "warning"}>
            {connectedServers.length > 0 ? "Gateway 在线" : "等待连接"}
          </StatusBadge>
          {errorServers.length > 0 ? <StatusBadge tone="danger">{errorServers.length} 个异常</StatusBadge> : null}
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

      {errorServers.length > 0 ? (
        <Alert tone="danger">
          有 MCP Server 处于异常状态，请在下方表格查看 Error 列，必要时断开后重新连接。
        </Alert>
      ) : null}

      <Grid cols={2}>
        <Card title="Gateway 状态">
          <Stack>
            <Progress label="连接率" value={servers.length > 0 ? Math.round((connectedServers.length / servers.length) * 100) : 0} />
            <KeyValue
              data={{
                在线: connectedServers.length,
                离线: disconnectedServers.length,
                异常: errorServers.length,
                Adapter: safePlugin.id || "mcp_adapter",
              }}
            />
          </Stack>
        </Card>

        <Card title="接入流程">
          <Steps>
            <Step index="1" title="添加 Server">
              <Text>使用表单或 JSON 导入保存 MCP Server 配置。</Text>
            </Step>
            <Step index="2" title="连接并发现 Tools">
              <Text>连接成功后，Adapter 会发现 tools 并注册为动态插件入口。</Text>
            </Step>
            <Step index="3" title="在入口列表调用">
              <Text>发现到的 tools 会出现在插件入口中，可被工作流或其他插件调用。</Text>
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

      <Grid cols={2}>
        {addServer ? (
          <Card title="添加 MCP Server">
            <ActionForm action={addServer} submitLabel="添加并连接" />
          </Card>
        ) : (
          <Card title="添加 MCP Server">
            <Alert tone="warning">当前上下文没有暴露 add_server 动作，无法使用自动表单。</Alert>
          </Card>
        )}

        <Card title="从 JSON 导入">
          <Stack>
            <Text>适合粘贴完整 server 配置；字段会直接传给 add_server 入口。</Text>
            <Textarea
              value={importJson}
              onChange={(value) => {
                importJson = value
                setImportError("")
              }}
            />
            <p id={importErrorId} className="neko-action-error" hidden></p>
            <Button tone="success" onClick={importServer}>导入 JSON</Button>
          </Stack>
        </Card>
      </Grid>

      <Grid cols={2}>
        <Card title="最小配置示例">
          <CodeBlock>{configExample}</CodeBlock>
        </Card>

        <Card title="Transport 选择">
          <KeyValue
            data={{
              stdio: "本地命令或 uvx/npmx 启动的 MCP Server",
              sse: "远端 SSE 服务",
              "streamable-http": "支持流式 HTTP 的远端服务",
              安全: "不要导入不可信命令或环境变量",
            }}
          />
        </Card>
      </Grid>

      <Card title="已发布插件入口">
        <DataTable
          data={safeEntries.slice(0, 12)}
          columns={[
            { key: "id", label: "入口 ID" },
            { key: "name", label: "名称" },
            { key: "description", label: "描述" },
          ]}
        />
        <Divider />
        <Tip>连接 MCP Server 后，发现到的 tools 会动态注册为入口，并出现在这里。</Tip>
      </Card>

      <Warning>
        stdio transport 会启动本地进程。导入配置前请确认命令来源可靠，避免执行不可信 MCP Server。
      </Warning>
    </Page>
  )
}
