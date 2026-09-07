# HTML 聊天卡片

插件可以推送 HTML/CSS 卡片，用声明式按钮调用已有的 `@ui.action`。不需要 TSX、前端 JavaScript、panel 声明或 `@ui.context`。

## 创建与更新

```python
card = await self.ctx.create_card(
    html="""
        <section>
            <strong>找到歌曲：夜曲</strong>
            <button data-neko-action="play">播放</button>
        </section>
    """,
    css="section { padding: 12px; } button { margin-left: 8px; }",
    actions={
        "play": {"entry": "play_track", "args": {"track_id": "123"}},
    },
    summary="找到歌曲：夜曲",
    # target_lanlan="角色名",  # 可选；角色相关任务建议明确传入
)

await card.update(summary="正在播放：夜曲")
await card.update(html="<p>播放结束</p>", actions={})
```

- `card.id` 是稳定 ID。句柄保留创建时的目标；未指定目标的卡片由主服务在首次创建时选择聊天，并在本次运行内为后续更新保留投递目标。
- `update()` 只替换提供的字段；省略的字段保留，`actions={}` 清空动作绑定，`css=""` 清空自定义样式。
- 创建必须提供 `html` 和 `summary`；`css`、`actions` 和 `target_lanlan` 可选。
- `await` 表示本地消息提交完成，不是用户已看到的回执。提交失败抛出 `CardSubmissionError`（可从 `plugin.sdk.plugin` 导入）。
- 卡片是插件来源的显示消息，不触发 AI 回复，也不把 HTML/CSS 注入模型。

## 按钮动作

```python
from plugin.sdk.plugin import plugin_entry, ui, Ok

@ui.action(id="play_track", label="播放歌曲")
@plugin_entry(id="play_track")
async def play_track(self, track_id: str, _ctx: dict):
    await self.player.play(track_id)  # 插件自己的业务逻辑

    card = self.ctx.get_card(
        _ctx["card_id"],
        target_lanlan=_ctx["lanlan_name"],
    )
    await card.update(html="<p>正在播放</p>", summary="正在播放", actions={})
    return Ok({"message": "已开始播放"})
```

HTML 中的 `data-neko-action` 对应 `actions` 字典的键；`entry` 对应本插件的 UI action/entry ID。`args` 是固定的 JSON 参数。`_ctx` 由宿主提供，包含 `card_id`、`lanlan_name`、`run_id`。不需要更新卡片的动作可以不接收 `_ctx`。

按钮点击时显示执行状态；正常返回值中的 `message` 字符串可作为完成提示。`Err(...)`、异常或 HTTP 错误显示为失败。重复点击同一个执行中的按钮会被忽略；这不是业务幂等保证，跨窗口等重复操作仍由业务自行处理。超时可能发生在业务完成之后，不代表回滚。

## 展示约定

HTML/CSS 由已安装插件提供，运行于禁止脚本的 iframe；父页面绑定按钮事件。插件不用写 `onclick`、`script` 或请求代码，这些脚本不会执行。按钮请使用 `<button data-neko-action="...">`。不提供通用表单、数据绑定或 JavaScript 运行时。

样式仅作用于卡片，内容超过最大高度时在卡片内滚动。网址由宿主按现有外链规则打开。图片可使用浏览器可访问的 URL；上传到插件媒体存储的图片可使用同源 `/media/<id>`，该存储为临时缓存，不能保证历史长期可用。

复制、导出和导出预览使用 `summary`，不执行卡片动作，也不承诺原样截图任意 HTML/CSS。普通聊天里的卡片保持可交互。

这是在线、尽力投递的功能：不持久化、不离线补发。已从当前聊天清除或被消息上限淘汰的卡片，不会因一条更新重新出现；需要重新展示时调用 `create_card()`。

## AgentHUD 插件内容

需要独立于聊天消息显示的内容时，使用 `create_view()`。它复用现有 AgentHUD 的插件内容页签，不创建新的原生窗口，也不需要 `plugin.toml` panel 声明或 `@ui.context`。

```python
view = await self.ctx.create_view(
    title="下载任务",
    html='<p>文件已准备好</p><button data-neko-action="download">下载</button>',
    actions={"download": {"entry": "download_file", "args": {"file_id": "123"}}},
    target_lanlan=_ctx["lanlan_name"],
)
@ui.action(id="download_file")
@plugin_entry(id="download_file")
async def download_file(self, file_id: str, _ctx: dict):
    view = self.ctx.get_view(
        _ctx["view_id"], target_lanlan=_ctx["lanlan_name"],
    )
    await view.update(summary="正在下载")
    await self.download(file_id)
    await view.update(html="<p>下载完成</p>", summary="下载完成", actions={})
    return Ok({"message": "下载完成"})
```

`create_view()` 返回可从 `plugin.sdk.plugin` 导入的 `PluginView`。创建必须提供 `title` 和 `html`；`css`、`actions`、`summary` 和 `target_lanlan` 可选，初始 `summary` 默认采用标题。`update()` 可替换标题和卡片的四个内容字段；省略或 `None` 保留，空字符串/空动作字典清空对应字段。`get_view()` 仅恢复发送句柄，不读取内容。回调中的 `view_id`、`card_id`、`lanlan_name` 和 `run_id` 由宿主提供；恢复句柄时显式传入回调角色，避免并发任务改变默认角色。

每个插件在每个角色下保留一个活动视图。重新创建使用新 ID，替换旧实例；旧实例的更新和关闭不会影响新实例。用户关闭或 `await view.close()` 结束该显示实例，后续更新不会重新打开或抢焦点；需要再次显示时重新创建。关闭视图不自动取消插件业务。按钮沿用现有同源动作代理、脚本隔离和错误反馈。

窗口内直接以视图标题显示页签，与“任务”并列。第一个视图会打开内容页；后续创建保留用户当前页签和折叠状态，以圆点标记未查看的新内容。更新不切换页签，关闭当前页时切到相邻视图，最后一个关闭后回到任务。

视图不进入聊天消息、复制/导出或 AI 上下文。投递沿用当前角色活动连接，不承诺多浏览器同步；不持久化、不离线补发。`await` 仍只确认本地提交，失败抛出 `CardSubmissionError`。
