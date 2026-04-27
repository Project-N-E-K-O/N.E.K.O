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
  DataTable,
} from "@neko/plugin-ui"

export default function QuickstartGuide({ plugin, entries, actions }) {
  const configExample = `[mcp_servers.example]
transport = "stdio"
command = "uvx"
args = ["mcp-server-example"]
enabled = true`

  return (
    <Page
      title="MCP Adapter 快速开始"
      subtitle="把外部 MCP Server 的 tools 接入 N.E.K.O，并自动暴露成插件入口。"
    >
      <Grid cols={3}>
        <Card title="用途">
          <Text>统一连接 stdio、SSE 或 streamable-http 类型的 MCP Server。</Text>
        </Card>
        <Card title="入口">
          <Text>发现到的 MCP tools 会被适配为 N.E.K.O 可调用的动态入口。</Text>
        </Card>
        <Card title="状态">
          <StatusBadge tone="success">Adapter: {plugin.id}</StatusBadge>
        </Card>
      </Grid>

      <Grid cols={2}>
        <StatCard label="已声明入口" value={entries.length} />
        <StatCard label="右键动作" value={actions.length} />
      </Grid>

      <Card title="推荐配置流程">
        <Steps>
          <Step index="1" title="添加 MCP Server">
            <Text>在 plugin.toml 的 [mcp_servers] 下添加一个 server 配置。</Text>
          </Step>
          <Step index="2" title="选择 transport">
            <Text>本地命令优先使用 stdio，远端服务可使用 sse 或 streamable-http。</Text>
          </Step>
          <Step index="3" title="重载插件">
            <Text>保存配置后重载 MCP Adapter，它会重新连接 server 并发现 tools。</Text>
          </Step>
          <Step index="4" title="在插件列表查看入口">
            <Text>发现成功后，工具会出现在插件详情的入口列表中。</Text>
          </Step>
        </Steps>
      </Card>

      <Card title="最小配置示例">
        <CodeBlock>{configExample}</CodeBlock>
      </Card>

      <Card title="关键字段">
        <KeyValue
          data={{
            transport: "stdio | sse | streamable-http",
            command: "stdio 模式启动命令",
            args: "stdio 模式命令参数",
            url: "sse / streamable-http 服务地址",
            enabled: "是否启用该 server",
          }}
        />
      </Card>

      <Card title="当前可见入口示例">
        <DataTable
          data={entries.slice(0, 6)}
          columns={[
            { key: "id", label: "入口 ID" },
            { key: "name", label: "名称" },
            { key: "description", label: "描述" },
          ]}
        />
      </Card>

      <Tip>
        如果某个 MCP tool 返回 HTML，Adapter 会尝试提取并转换内容，必要时可使用 raw 模式保留原始结果。
      </Tip>

      <Warning>
        不要在配置里写入不可信命令。stdio transport 会启动本地进程，请确认命令来源可靠。
      </Warning>
    </Page>
  )
}
