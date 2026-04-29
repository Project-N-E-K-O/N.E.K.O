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
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type McpGuideState = {
  connected_servers?: number
  total_servers?: number
  total_tools?: number
}

export default function QuickstartGuide(props: PluginSurfaceProps<McpGuideState>) {
  const { plugin, state } = props
  const { t } = props
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
      title={t("quickstart.title")}
      subtitle={t("quickstart.subtitle")}
    >
      <Grid cols={3}>
        <Card title={t("quickstart.cards.connect.title")}>
          <Stack>
            <StatusBadge tone="info">MCP Server</StatusBadge>
            <Text>{t("quickstart.cards.connect.body")}</Text>
          </Stack>
        </Card>
        <Card title={t("quickstart.cards.discover.title")}>
          <Stack>
            <StatusBadge tone="primary">Tool Discovery</StatusBadge>
            <Text>{t("quickstart.cards.discover.body")}</Text>
          </Stack>
        </Card>
        <Card title={t("quickstart.cards.publish.title")}>
          <Stack>
            <StatusBadge tone="success">N.E.K.O Entry</StatusBadge>
            <Text>{t("quickstart.cards.publish.body")}</Text>
          </Stack>
        </Card>
      </Grid>

      <Grid cols={3}>
        <StatCard label={t("quickstart.stats.connected")} value={connected} />
        <StatCard label={t("quickstart.stats.configured")} value={total} />
        <StatCard label={t("quickstart.stats.tools")} value={tools} />
      </Grid>

      <Alert tone={total > 0 ? "success" : "warning"}>
        {total > 0
          ? t("quickstart.alert.configured")
          : t("quickstart.alert.empty")}
      </Alert>

      <Card title={t("quickstart.path.title")}>
        <Steps>
          <Step index="1" title={t("quickstart.path.serverType.title")}>
            <Text>{t("quickstart.path.serverType.body")}</Text>
          </Step>
          <Step index="2" title={t("quickstart.path.prepare.title")}>
            <Text>{t("quickstart.path.prepare.body")}</Text>
          </Step>
          <Step index="3" title={t("quickstart.path.add.title")}>
            <Text>{t("quickstart.path.add.body")}</Text>
          </Step>
          <Step index="4" title={t("quickstart.path.verify.title")}>
            <Text>{t("quickstart.path.verify.body")}</Text>
          </Step>
          <Step index="5" title={t("quickstart.path.troubleshoot.title")}>
            <Text>{t("quickstart.path.troubleshoot.body")}</Text>
          </Step>
        </Steps>
      </Card>

      <Grid cols={2}>
        <Card title={t("quickstart.examples.stdio")}>
          <CodeBlock>{stdioExample}</CodeBlock>
        </Card>

        <Card title={t("quickstart.examples.remote")}>
          <CodeBlock>{remoteExample}</CodeBlock>
        </Card>
      </Grid>

      <Card title={t("quickstart.jsonImport.title")}>
        <Text>{t("quickstart.jsonImport.body")}</Text>
        <CodeBlock>{jsonExample}</CodeBlock>
      </Card>

      <Grid cols={2}>
        <Card title={t("quickstart.fields.title")}>
          <KeyValue
            items={[
              { key: "name", label: "name", value: t("quickstart.fields.name") },
              { key: "transport", label: "transport", value: "stdio | sse | streamable-http" },
              { key: "command", label: "command", value: t("quickstart.fields.command") },
              { key: "args", label: "args", value: t("quickstart.fields.args") },
              { key: "url", label: "url", value: t("quickstart.fields.url") },
              { key: "env", label: "env", value: t("quickstart.fields.env") },
            ]}
          />
        </Card>

        <Card title={t("quickstart.faq.title")}>
          <Stack>
            <Tip>{t("quickstart.faq.noEntries")}</Tip>
            <Tip>{t("quickstart.faq.stdioFailed")}</Tip>
            <Tip>{t("quickstart.faq.remoteFailed")}</Tip>
          </Stack>
        </Card>
      </Grid>

      <Card title={t("quickstart.next.title")}>
        <Stack>
          <Text>{t("quickstart.next.body")}</Text>
          <Divider />
          <KeyValue
            items={[
              { key: "plugin", label: t("quickstart.next.currentPlugin"), value: safePlugin.id || "mcp_adapter" },
              { key: "panel", label: t("quickstart.next.panelEntry"), value: t("quickstart.next.panelPath") },
              { key: "logs", label: t("quickstart.next.logsEntry"), value: t("quickstart.next.logsPath") },
            ]}
          />
        </Stack>
      </Card>

      <Warning>
        {t("quickstart.warning.stdio")}
      </Warning>
    </Page>
  )
}
