# ChatGPT 订阅接入

N.E.K.O. 支持通过 **Continue with ChatGPT** 登录，在获得授权后使用账号的 ChatGPT 订阅额度，无需填写 OpenAI API Key。可用模型与额度以当前账号为准。此功能使用 OAuth 和公开 Responses API，不启动 Codex CLI 或编码代理。

## 使用方法

1. 在运行 Neko 的电脑上，用 `localhost` 或 `127.0.0.1` 打开 API 设置。
2. 在 **ChatGPT / Codex** 区域点击 **Continue with ChatGPT**，在系统浏览器中登录并允许使用订阅额度。添加其他账号时点击“添加账号”；账号选择器中的勾号表示当前生效的注册。
3. 展开并启用“自定义 API 配置”，将文字或视觉模型的服务商选为 **ChatGPT (subscription)**。
4. 点击“拉取模型”，选择当前账号可用的模型，然后保存设置。该模型槽位无需填写 API Key。

可用于文本对话、摘要、纠错、情感分析、视觉理解、Agent 和小游戏模型。工具调用仍由 Neko 执行。语音识别、实时语音、语音合成和图像生成继续使用各自的服务商。

切换账号后，后续 ChatGPT 请求使用新账号，需要重新拉取模型列表。退出登录会清除所选注册的凭证；如果远程撤销未得到确认，可到 [ChatGPT 用量设置](https://chatgpt.com/settings/usage) 中断开此应用。

## 凭证与运行方式

凭证保存在 Neko 本机 state 目录的 `chatgpt_oauth.json`，不进入 API Key 配置、前端存储或云存档。Unix 文件权限为 `0600`，更新采用原子替换，跨进程文件锁保证 refresh token 轮换不会发生竞争。

首次登录使用动态注册，之后使用签发的 client ID。回调只监听 `127.0.0.1` 上的空闲端口，五分钟后过期。登录会验证 PKCE、state、ID token 签名、issuer、audience、有效期、nonce 和账号身份，并检查订阅使用权限。

推理使用 `https://api.openai.com/v1/responses`，强制关闭服务端存储并启用流式传输。只有收到 `response.completed` 才视为成功；中途失败不会触发未完成的工具调用。达到订阅限制后会提示检查用量，不会自动切换账号或改用按量计费的 API Key。

目前提供本机登录流程。远程服务器登录、从其他应用导入凭证和多账号自动轮换均不在本次实现范围内。现有 Neko 输入、输出预算继续生效。

## 发布前验证

自动化测试使用模拟服务，覆盖认证校验、刷新、账号隔离、配置保存、模型发现、流式转换和工具往返。发布前还需要真实账号授权，验证模型列表、文字和图片对话、工具调用、重启后的续用以及退出登录。真实验证会使用账号额度。

技术依据见[英文实现说明](/config/chatgpt)及[OpenAI 开源接入文档](https://developers.openai.com/siwc/token-sharing-open-source)。
