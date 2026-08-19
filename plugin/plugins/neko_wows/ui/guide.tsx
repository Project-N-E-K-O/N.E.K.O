import {
  Card,
  Stack,
  Step,
  Steps,
  Text,
  Tip,
  Warning,
} from "@neko/plugin-ui"

import type { Translate } from "./types"

export function GuideSection(props: { t: Translate }) {
  const { t } = props

  return (
    <Stack gap={12}>
      <Text>{t("guide.intro")}</Text>

      <Card title={t("guide.what.title")}>
        <Text>{t("guide.what.body")}</Text>
      </Card>

      <Card title={t("guide.install.title")}>
        <Steps>
          <Step index="1" title={t("guide.install.loader.title")}>
            <Text>{t("guide.install.loader.body")}</Text>
          </Step>
          <Step index="2" title={t("guide.install.copy.title")}>
            <Text>{t("guide.install.copy.body")}</Text>
          </Step>
          <Step index="3" title={t("guide.install.config.title")}>
            <Text>{t("guide.install.config.body")}</Text>
          </Step>
          <Step index="4" title={t("guide.install.verify.title")}>
            <Text>{t("guide.install.verify.body")}</Text>
          </Step>
        </Steps>
      </Card>

      <Card title={t("guide.connect.title")}>
        <Text>{t("guide.connect.body")}</Text>
      </Card>

      <Card title={t("guide.trouble.title")}>
        <Stack gap={8}>
          <Tip>{t("guide.trouble.noState")}</Tip>
          <Tip>{t("guide.trouble.update")}</Tip>
          <Tip>{t("guide.trouble.port")}</Tip>
        </Stack>
      </Card>

      <Warning>{t("guide.disclaimer")}</Warning>
    </Stack>
  )
}
