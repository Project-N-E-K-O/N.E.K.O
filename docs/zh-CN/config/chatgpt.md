# ChatGPT 订阅接入

N.E.K.O. 支持通过 **Continue with ChatGPT** 登录，在获得授权后使用账号的 ChatGPT 订阅额度，无需填写 OpenAI API Key。可用模型与额度以当前账号为准。此功能使用 OAuth 和公开 Responses API，不启动 Codex CLI 或编码代理。

## 使用方法

1. 在运行 Neko 的电脑上，用 `localhost` 或 `127.0.0.1` 打开 API 设置。
2. 展开「API 管理簿」和「按功能单独指定模型（进阶）」之间的 **订阅与套餐** 区域，在 ChatGPT 卡片上点击 **Continue with ChatGPT**，在系统浏览器中登录并允许使用订阅额度。添加其他账号时点击“添加账号”；账号选择器中的勾号表示当前生效的注册。
3. 在同一张卡片上点「从这个账号拉取模型列表」，勾选要交给 ChatGPT 的功能并选好模型，再打开「使用这个订阅」。不需要开启「按功能单独指定模型」，也不需要点「保存设置」。关掉开关，这些功能回到原来的设置。详见[订阅与套餐](/zh-CN/config/subscriptions)。

可选的功能：聊天模型、记忆整理（大模型）、识别模型（小模型）、使魔模型·通用、使魔模型·小型、使魔模型·高级、小游戏聊天模型、小游戏记忆模型、小游戏识别模型；看图由聊天模型负责。工具调用仍由 Neko 执行。语音识别、实时语音（本地）、语音合成、图片生成和决策模型（专用接口）继续使用各自的服务商。

喵能力的任务代理可以使用交给 ChatGPT 的使魔模型档位（dsh 不接这类模型，任务改由内置运行环境执行）；插件模型槽位跟随到 ChatGPT 档位时会提示不支持，插件需要配置自己的 API。

切换账号后，后续 ChatGPT 请求使用新账号，需要重新拉取模型列表。退出登录会清除所选注册的凭证，ChatGPT 订阅改用仍然登录的账号；最后一个账号退出时，ChatGPT 订阅自动关闭，相关功能回到原来的设置。如果远程撤销未得到确认，可到 [ChatGPT 用量设置](https://chatgpt.com/settings/usage) 中断开此应用。

## 凭证与运行方式

凭证保存在 Neko 本机 state 目录的 `chatgpt_oauth.json`，不进入 API Key 配置、前端存储或云存档。Unix 文件权限为 `0600`，更新采用原子替换，跨进程文件锁保证 refresh token 轮换不会发生竞争。

首次登录使用动态注册，之后使用签发的 client ID。回调只监听 `127.0.0.1` 上的空闲端口，五分钟后过期。登录会验证 PKCE、state、ID token 签名、issuer、audience、有效期、nonce 和账号身份，并检查订阅使用权限。

推理使用 `https://api.openai.com/v1/responses`，强制关闭服务端存储并启用流式传输。工具调用只在收到 `response.completed` 后交付。为兼容上游响应，当服务端返回 `response.incomplete` 且 `incomplete_details.reason` 为 `max_output_tokens` 时，已有文字会以 `finish_reason="length"` 返回，不交付工具调用；没有文字时，普通调用报告输出限制错误，连接测试则将这类有效响应视为连通。其他不完整、失败或断流仍会报错。达到订阅限制后会提示检查用量，不会自动切换账号或改用按量计费的 API Key。

喵能力的任务代理使用同一 OAuth/Responses 客户端。截断的工具调用不会执行；认证或订阅额度的终止错误会结束任务，不会再通过另一种输出模式重试。登录弹窗被拦截时，可以点击页面上的 Continue with ChatGPT 链接继续当前授权；状态请求暂时失败后会自动重试，返回设置页面时会重新检查状态。

目前提供本机登录流程。远程服务器登录、从其他应用导入凭证和多账号自动轮换均不在本次实现范围内。现有调用方的输入预算和请求超时保持不变。根据 OpenAI 的[预览限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)，ChatGPT 订阅接口不支持 `max_output_tokens` 请求字段；适配器不会发送该字段，也不提供调用方指定的输出 token 数硬上限。

## 发布前验证

自动化测试使用模拟服务，覆盖认证校验、刷新、账号隔离、配置保存、模型发现、流式转换和工具往返。发布前还需要真实账号授权，验证模型列表、文字和图片对话、工具调用、重启后的续用以及退出登录。真实验证会使用账号额度。

技术依据见[英文实现说明](/config/chatgpt)及[OpenAI 开源接入文档](https://developers.openai.com/siwc/token-sharing-open-source)。
