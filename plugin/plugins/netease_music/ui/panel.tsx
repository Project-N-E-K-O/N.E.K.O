import {
  Alert,
  Button,
  Card,
  Field,
  Inline,
  Page,
  PasswordInput,
  Stack,
  StatusBadge,
  Text,
  Tip,
  useConfirm,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type CredentialState = {
  cookie_configured?: boolean
  nmtid_configured?: boolean
  cookie_count?: number
  storage?: string
  action_token?: string
}

function hasAction(actions: HostedAction[], id: string): boolean {
  return actions.some((action) => action.id === id || action.entry_id === id)
}

export default function NeteaseMusicPanel(
  props: PluginSurfaceProps<CredentialState>,
) {
  const configured = !!props.state?.cookie_configured
  const nmtidConfigured = !!props.state?.nmtid_configured
  const actionToken = props.state?.action_token || ""
  const [musicU, setMusicU] = props.useLocalState("music_u", "")
  const [nmtid, setNmtid] = props.useLocalState("nmtid", "")
  const [saving, setSaving] = useState(false)
  const toast = useToast()
  const confirm = useConfirm()
  const canSave = !!actionToken && hasAction(props.actions || [], "save_music_u")
  const canClear = !!actionToken && hasAction(props.actions || [], "clear_music_u")

  async function saveCookie() {
    const value = musicU.trim()
    if (!value) {
      toast.error("请输入 MUSIC_U 或包含 MUSIC_U 的完整凭据字符串。")
      return
    }
    setSaving(true)
    try {
      await props.api.call("save_music_u", {
        music_u: value,
        nmtid: nmtid.trim(),
        ui_token: actionToken,
      })
      setMusicU("")
      setNmtid("")
      await props.api.refresh()
      toast.success("凭据已加密保存，后续播放会立即使用。")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSaving(false)
    }
  }

  async function clearCookie() {
    const accepted = await confirm({
      title: "清除网易云凭据",
      message: "这会删除插件私有目录中保存的全部网易云 Cookie，并恢复匿名播放。",
      tone: "danger",
      confirmLabel: "清除",
      cancelLabel: "取消",
    })
    if (!accepted) return
    try {
      await props.api.call("clear_music_u", { ui_token: actionToken })
      setMusicU("")
      setNmtid("")
      await props.api.refresh()
      toast.success("凭据已清除。")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <Page title="网易云音乐" subtitle="插件内登录凭据配置">
      <Stack>
        <Card title="登录凭据">
          <Stack>
            <Inline align="center" justify="space-between">
              <Text>MUSIC_U</Text>
              <StatusBadge
                tone={configured ? "success" : "warning"}
                label={configured ? "已配置" : "未配置"}
              />
            </Inline>

            <Alert tone="info">
              登录凭据仅加密保存在本插件的私有数据目录，不读取或写入 NEKO
              全局凭证，也不会在面板中回显。
            </Alert>

            {!canSave ? (
              <Alert tone="warning">
                配置动作尚未可用。仍可先填写 Cookie；请启用并启动插件后再保存。
              </Alert>
            ) : null}

            <Field
              required
              label="MUSIC_U"
              help="可以粘贴单独的值，也可以粘贴包含 MUSIC_U=... 的完整 Cookie 字符串。"
            >
              <PasswordInput
                value={musicU}
                placeholder={configured ? "输入新值以替换已保存的凭据" : "粘贴 MUSIC_U"}
                disabled={saving}
                onChange={setMusicU}
              />
            </Field>

            <Field
              label="NMTID（可选）"
              help="与内置网易云登录配置一致；如果完整 Cookie 中已经包含 NMTID，可以留空。"
            >
              <PasswordInput
                value={nmtid}
                placeholder={nmtidConfigured ? "输入新值以替换已保存的 NMTID" : "粘贴 NMTID"}
                disabled={saving}
                onChange={setNmtid}
              />
            </Field>

            <Inline justify="end">
              <Button
                tone="danger"
                disabled={!configured || !canClear || saving}
                onClick={clearCookie}
              >
                清除凭据
              </Button>
              <Button
                tone="success"
                disabled={!canSave || saving || !musicU.trim()}
                onClick={saveCookie}
              >
                {saving ? "保存中…" : "保存凭据"}
              </Button>
            </Inline>
          </Stack>
        </Card>

        <Card title="如何获取 Cookie">
          <Stack>
            <Text>1. 在浏览器打开 music.163.com，登录后确认右上角显示账号头像。</Text>
            <Text>2. 按 F12 打开开发者工具，切换到 Application（应用程序）面板。</Text>
            <Text>3. 在 Storage（存储）中展开 Cookies，选择 music.163.com。</Text>
            <Text>
              4. 找到 MUSIC_U（必填）和 NMTID（可选），复制各自的 Value；也可以复制包含
              MUSIC_U 的完整 Cookie 字符串。
            </Text>
            <Alert tone="warning">
              Cookie 等同于登录身份，请勿分享给他人，只在本机的插件面板中粘贴。
            </Alert>
          </Stack>
        </Card>

        <Tip>
          保存后无需重启插件。插件只保留 MUSIC_U、MUSIC_A、NMTID 和 __csrf，
          并在请求音源前同步到 pyncm 会话；登录态失效时会自动尝试公开音源。
        </Tip>
      </Stack>
    </Page>
  )
}
