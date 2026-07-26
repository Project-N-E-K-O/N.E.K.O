# デプロイ概要

| 方法 | 用途 | Entry |
| --- | --- | --- |
| Desktop release | End user | Electron UI + packaged backend |
| Source launcher | Local development | `uv run python launcher.py` |
| Docker Compose | Headless/server | Nginx + Python services |
| Standalone modules | Isolation | memory/main/agent separately |

Cross-platform workflow は Windows、macOS、Linux を build します。Scheduled output は **nightly prerelease** で、stable promise ではありません。

Source は Python 3.11、`uv`、frontend lockfile に適合する Node（plugin manager は `^20.19.0 || >=22.12.0`）が必要です。Local vectors は optional CPU path で、無効でも BM25 は利用できます。

Source preferred ports は main 48911、memory 48912、agent/tool 48915、user-plugin 48916。Docker host 48911/48912 は Nginx HTTP/HTTPS mapping で、source process table とは異なります。

N.E.K.O. は主に local companion です。外部公開前に authentication、proxy headers、TLS、firewall と privacy impact を確認してください。
