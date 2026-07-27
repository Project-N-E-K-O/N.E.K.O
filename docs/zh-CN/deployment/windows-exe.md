# Windows 桌面构建

普通用户应从 [Steam 商店](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_deployment&utm_content=install_zh_cn) 安装并通过 Steam 启动，再在桌面/Web UI 配置 Provider。

独立 Python 后端产物不包含 Electron 窗口、托盘、Steam 集成、路由或更新器，不能与桌面产物混为一谈。

`.github/workflows/build-desktop.yml` 会构建 Windows x64 Electron 产物和单独的 Python 后端产物。定时任务仅在必要阶段成功时更新仓库 `nightly` 预发行版。

Nightly 是未签名测试构建，会被下一次运行替换，不是稳定或自动更新渠道。只从项目 GitHub Releases 下载，核对构建 commit，并先备份 N.E.K.O. 数据根。

桌面 workflow 组合指定 N.E.K.O.-PC revision 的 Electron 前端、本仓库 Nuitka standalone 后端，以及打包检查要求的 config/templates/static/plugins/embedding/tiktoken/browser 资源。端口占用时会回退，自动化不应写死 48911。
