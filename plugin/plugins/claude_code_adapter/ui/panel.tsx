import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Tip,
  Alert,
  CodeBlock,
  Steps,
  Step,
  Button,
  Divider,
  KeyValue,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type PluginState = {
  cli_available?: boolean
  cli_path?: string
  model?: string
  auto_inject_api_config?: boolean
}

export default function ClaudeCodeAdapterPanel(props: PluginSurfaceProps<PluginState>) {
  const { state } = props
  const { t } = props
  const safeState = state || {}
  const cliAvailable = safeState.cli_available || false
  const cliPath = safeState.cli_path || ""
  const model = safeState.model || ""
  const autoInject = safeState.auto_inject_api_config !== false

  return (
    <Page
      title={t("panel.title")}
      subtitle={t("panel.subtitle")}
    >
      {/* 状态概览 */}
      <Card title={t("panel.status.title")}>
        <Stack>
          <KeyValue
            items={[
              {
                key: "status",
                label: t("panel.status.cliStatus"),
                value: cliAvailable ? t("panel.status.installed") : t("panel.status.notInstalled"),
              },
              {
                key: "path",
                label: t("panel.status.cliPath"),
                value: cliPath || t("panel.status.unknown"),
              },
              {
                key: "model",
                label: t("panel.status.model"),
                value: model || t("panel.status.default"),
              },
              {
                key: "inject",
                label: t("panel.status.autoInject"),
                value: autoInject ? t("panel.status.enabled") : t("panel.status.disabled"),
              },
            ]}
          />
          {!cliAvailable && (
            <Alert tone="warning">
              {t("panel.status.installRequired")}
            </Alert>
          )}
        </Stack>
      </Card>

      {/* 安装指南 */}
      <Card title={t("panel.install.title")}>
        <Steps>
          <Step index="1" title={t("panel.install.step1.title")}>
            <Stack>
              <Text>{t("panel.install.step1.desc")}</Text>
              <Tip>{t("panel.install.step1.tip")}</Tip>
              <Button
                tone="primary"
                onClick={() => window.open("https://nodejs.org/zh-cn", "_blank")}
              >
                {t("panel.install.step1.button")}
              </Button>
            </Stack>
          </Step>

          <Step index="2" title={t("panel.install.step2.title")}>
            <Stack>
              <Text>{t("panel.install.step2.desc")}</Text>
              <CodeBlock language="bash">
                {t("panel.install.step2.command")}
              </CodeBlock>
              <Tip>{t("panel.install.step2.tip")}</Tip>
            </Stack>
          </Step>

          <Step index="3" title={t("panel.install.step3.title")}>
            <Stack>
              <Text>{t("panel.install.step3.desc")}</Text>
              <CodeBlock language="bash">
                {t("panel.install.step3.command")}
              </CodeBlock>
              <Tip>{t("panel.install.step3.tip")}</Tip>
            </Stack>
          </Step>

          <Step index="4" title={t("panel.install.step4.title")}>
            <Stack>
              <Text>{t("panel.install.step4.desc")}</Text>
              <CodeBlock language="bash">
                {t("panel.install.step4.command")}
              </CodeBlock>
              <Tip>{t("panel.install.step4.tip")}</Tip>
            </Stack>
          </Step>
        </Steps>
      </Card>

      {/* 配置说明 */}
      <Card title={t("panel.config.title")}>
        <Stack>
          <Text>{t("panel.config.desc1")}</Text>
          <Divider />
          <Text>{t("panel.config.desc2")}</Text>
          <CodeBlock language="json">
            {`{
  "api_key": "sk-ant-xxx",
  "base_url": "https://api.anthropic.com"
}`}
          </CodeBlock>
          <Tip>{t("panel.config.tip1")}</Tip>
          <Divider />
          <Text>{t("panel.config.desc3")}</Text>
          <CodeBlock language="bash">
            {`# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-xxx"
$env:ANTHROPIC_BASE_URL="https://api.anthropic.com"

# macOS/Linux
export ANTHROPIC_API_KEY="sk-ant-xxx"
export ANTHROPIC_BASE_URL="https://api.anthropic.com"`}
          </CodeBlock>
          <Tip>{t("panel.config.tip2")}</Tip>
        </Stack>
      </Card>

      {/* 使用示例 */}
      <Card title={t("panel.usage.title")}>
        <Stack>
          <Text>{t("panel.usage.desc")}</Text>
          <Divider />
          <Text>{t("panel.usage.example1.title")}</Text>
          <CodeBlock>
            {t("panel.usage.example1.text")}
          </CodeBlock>
          <Divider />
          <Text>{t("panel.usage.example2.title")}</Text>
          <CodeBlock>
            {t("panel.usage.example2.text")}
          </CodeBlock>
        </Stack>
      </Card>

      {/* 常见问题 */}
      <Card title={t("panel.faq.title")}>
        <Stack>
          <Text>
            <strong>Q: {t("panel.faq.q1")}</strong>
          </Text>
          <Text>{t("panel.faq.a1")}</Text>
          <Divider />
          <Text>
            <strong>Q: {t("panel.faq.q2")}</strong>
          </Text>
          <Text>{t("panel.faq.a2")}</Text>
          <Divider />
          <Text>
            <strong>Q: {t("panel.faq.q3")}</strong>
          </Text>
          <Text>{t("panel.faq.a3")}</Text>
          <Divider />
          <Text>
            <strong>Q: {t("panel.faq.q4")}</strong>
          </Text>
          <Text>{t("panel.faq.a4")}</Text>
        </Stack>
      </Card>

      {/* 温馨提示 */}
      <Tip>
        {t("panel.tip")}
      </Tip>
    </Page>
  )
}
