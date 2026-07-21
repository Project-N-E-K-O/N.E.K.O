# 部署概览

| 方式 | 场景 | 实际入口 |
| --- | --- | --- |
| 桌面发行版 | 普通用户 | Electron UI + 打包 Python 后端 |
| 源码 launcher | 本地开发 | `uv run python launcher.py` |
| Docker Compose | 无头/服务器 | Nginx 代理 Python 服务 |
| 独立模块 | 隔离服务问题 | 分别启动 memory/main/agent |

跨平台 workflow 构建 Windows、macOS、Linux 产物；定时输出是 **nightly 预发行版**，不是稳定版承诺。

源码要求 Python 3.11、`uv`，以及满足前端 lockfile 的 Node（plugin manager 要求 `^20.19.0 || >=22.12.0`）。

本地向量是可选的 CPU 能力；禁用时 BM25 仍可用。详见[本地嵌入模型资源](./embedding-models)。

源码首选端口为主服务 48911、记忆 48912、Agent/工具 48915、用户插件 48916。Docker 的宿主 48911/48912 是 Nginx HTTP/HTTPS 映射，不能与源码进程端口表混用。

N.E.K.O. 主要面向本机。对外暴露前必须评估认证、代理头、TLS、防火墙，以及配置、记忆、屏幕、浏览器和插件能力的隐私影响。
