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
  Steps,
  Step,
  CodeBlock,
  StatusBadge,
  StatCard,
  KeyValue,
  Divider,
  Alert,
} from "@neko/plugin-ui"

export default function QuickstartGuide({ plugin, state }) {
  const safePlugin = plugin || {}
  const safeState = state || {}
  const connected = safeState.connected_servers || 0
  const total = safeState.total_servers || 0
  const tools = safeState.total_tools || 0
  const stdioExample = `[mcp_servers.filesystem]
transport = "stdio"
command = "uvx"
args = ["mcp-server-filesystem", "/tmp"]
enabled = true
auto_connect = true`
  const remoteExample = `[mcp_servers.remote_docs]
transport = "streamable-http"
url = "https://example.com/mcp"
enabled = true
auto_connect = true`
  const jsonExample = `{
  "name": "filesystem",
  "transport": "stdio",
  "command": "uvx",
  "args": ["mcp-server-filesystem", "/tmp"],
  "enabled": true,
  "auto_connect": true
}`

  return (
    <Page
      title="MCP Adapter 快速开始"
      subtitle="从 0 到 1 接入 MCP Server：理解用途、选择 transport、保存配置、发现 tools。"
    >
      <Grid cols={3}>
        <Card title="1. 连接外部能力">
          <Stack>
            <StatusBadge tone="info">MCP Server</StatusBadge>
            <Text>Adapter 负责连接 stdio、SSE 或 streamable-http 类型的 MCP Server。</Text>
          </Stack>
        </Card>
        <Card title="2. 发现 Tools">
          <Stack>
            <StatusBadge tone="primary">Tool Discovery</StatusBadge>
            <Text>连接成功后会读取 server 暴露的 tools、参数 schema 和描述。</Text>
          </Stack>
        </Card>
        <Card title="3. 发布入口">
          <Stack>
            <StatusBadge tone="success">N.E.K.O Entry</StatusBadge>
            <Text>每个 MCP tool 会被适配成 N.E.K.O 可调用的动态插件入口。</Text>
          </Stack>
        </Card>
      </Grid>

      <Grid cols={3}>
        <StatCard label="当前已连接" value={connected} />
        <StatCard label="当前已配置" value={total} />
        <StatCard label="当前已发现 Tools" value={tools} />
      </Grid>

      <Alert tone={total > 0 ? "success" : "warning"}>
        {total > 0
          ? "你已经配置了 MCP Server。前往 MCP Adapter 面板可以连接、断开、移除或导入更多 Server。"
          : "还没有配置 MCP Server。阅读下面的步骤后，前往 MCP Adapter 面板添加第一个 Server。"}
      </Alert>

      <Card title="推荐接入路径">
        <Steps>
          <Step index="1" title="确认 MCP Server 类型">
            <Text>本地命令使用 stdio，远端服务优先使用 streamable-http；旧服务可能仍使用 SSE。</Text>
          </Step>
          <Step index="2" title="准备配置">
            <Text>为 server 取一个稳定名称，填写启动命令或远端 URL，并确认是否自动连接。</Text>
          </Step>
          <Step index="3" title="在面板添加 Server">
            <Text>打开 MCP Adapter 面板，使用表单或 JSON 导入保存配置。</Text>
          </Step>
          <Step index="4" title="检查工具发现结果">
            <Text>连接成功后，在面板的“已发布插件入口”区域确认 tools 是否已经注册。</Text>
          </Step>
          <Step index="5" title="排查错误">
            <Text>如果连接失败，查看面板表格的 Error 列，并切到日志页查看详细终端输出。</Text>
          </Step>
        </Steps>
      </Card>

      <Grid cols={2}>
        <Card title="本地 stdio 示例">
          <CodeBlock>{stdioExample}</CodeBlock>
        </Card>

        <Card title="远端 streamable-http 示例">
          <CodeBlock>{remoteExample}</CodeBlock>
        </Card>
      </Grid>

      <Card title="JSON 导入示例">
        <Text>如果你从其他工具复制 MCP 配置，可以在面板的 JSON 导入区域粘贴类似结构。</Text>
        <CodeBlock>{jsonExample}</CodeBlock>
      </Card>

      <Grid cols={2}>
        <Card title="关键字段">
          <KeyValue
            data={{
              name: "Server 唯一标识，后续连接/断开会使用它",
              transport: "stdio | sse | streamable-http",
              command: "stdio 模式启动命令，例如 uvx",
              args: "stdio 命令参数数组",
              url: "远端 MCP 服务地址",
              env: "需要注入给本地进程的环境变量",
            }}
          />
        </Card>

        <Card title="常见问题">
          <Stack>
            <Tip>连接后没有入口：确认 server 已连接，并且 tools_count 大于 0。</Tip>
            <Tip>stdio 启动失败：确认命令在当前环境可执行，参数路径存在。</Tip>
            <Tip>远端连接失败：确认 URL、网络访问和服务端 MCP 协议版本。</Tip>
          </Stack>
        </Card>
      </Grid>

      <Card title="下一步">
        <Stack>
          <Text>教程页只提供说明和配置参考；所有连接、导入、移除等操作都在 MCP Adapter 面板完成。</Text>
          <Divider />
          <KeyValue
            data={{
              当前插件: safePlugin.id || "mcp_adapter",
              管理入口: "插件详情 -> 面板 -> MCP Adapter",
              日志入口: "插件详情 -> 日志",
            }}
          />
        </Stack>
      </Card>

      <Warning>
        不要导入来源不明的 stdio 配置。stdio transport 会启动本地进程，命令、参数和环境变量都应来自可信来源。
      </Warning>
    </Page>
  )
}
