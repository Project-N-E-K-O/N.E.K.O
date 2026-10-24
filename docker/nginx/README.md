# docker/nginx —— Nginx 配置分层

反代配置不再是 entrypoint 里的两个巨型 heredoc，而是 `docker/nginx/` 下的一组分层模板，
由 `render.sh` 在容器启动时渲染到 `/etc/nginx/`。

## 为什么要拆

拆分前 `docker/entrypoint.sh` 的 `setup_nginx_proxy()` 用两个 heredoc 分别生成
「HTTP-only」和「HTTP+HTTPS」两份完整 server 块。实测两份配置**除前导 12 行外，
其余 113 行逐字节重复**：

```bash
# 拆分前的等价性证据
diff <(sed -n '568,680p' docker/entrypoint.sh) <(sed -n '685,809p' docker/entrypoint.sh)
# 2a3,14  ← 只有「listen ssl + ssl_* + HSTS」这一小段是 TLS 模式独有的
```

代价是：任何一条路由、一个转发头的改动都要人肉同步两份（#3058 的
`api/model-config` 前缀、#3250 的 CSRF location 都是双改）。漏改一份**不会报错**，
只会在某一种部署模式下静默失效 —— 这是最难发现的一类回归。

## 分层

| 层 | 模板 | 渲染到 | 职责 | 与部署模式的关系 |
|---|---|---|---|---|
| L0 | `nginx.conf.template` | `/etc/nginx/nginx.conf` | 进程 / `http` 全局：worker、日志、mime、`include conf.d/*.conf` | 无关 |
| L1a | `server.http.conf.template`<br>`server.tls.conf.template` | `/etc/nginx/neko/listener.conf` | `listen`、证书、协议、HSTS | **唯一差异所在** |
| L1b | `neko-proxy.conf.template` | `/etc/nginx/conf.d/neko-proxy.conf` | `server` 骨架：组装 L1a + L1c | 无关 |
| L1c | `locations.conf.template` | `/etc/nginx/neko/locations.conf` | 路由表：按上游服务分组的 `location` | **两种模式共用一份** |
| S | `snippets/*.conf` | `/etc/nginx/neko/snippets/*.conf` | 公共转发头 / WebSocket / 超时 | 共用 |

### 依赖方向

```
L0  nginx.conf ──include──> conf.d/*.conf
L1b neko-proxy ──include──> L1a listener.conf
                └─include──> L1c locations.conf
L1c locations ──include──> S snippets/{proxy-websocket,proxy-headers-*,proxy-timeouts}
S   proxy-headers-extended ──include──> S proxy-headers-basic
```

单向、无环。每一层只认识自己下面的一层。

### 为什么用 nginx 的 include 而不是把内容拼进一个文件

- 每个语义单元独立成文件，`diff` / 评审时不会淹没在无关文本里；
- 模式差异（L1a）与路由（L1c）物理隔离后，「改路由影响两种模式」「改 TLS 只影响 HTTPS」
  变成**结构上的保证**，而不是靠人记得同步；
- nginx 的 `include` 在 `server` / `location` 上下文都合法，不引入任何运行时特性。

## 不变量

改这个目录之前，请先确认没有破坏下面任何一条 —— `test-render.sh` 会逐条断言：

1. 两种部署模式的差异**只能**出现在 `listener.conf`（T3）。
2. `locations.conf` 里不出现 `listen` / `ssl_*`，且两种模式共用同一份（T4）。
3. 公共转发头 / WebSocket / 超时只定义一次，位于 `snippets/`。
4. CSRF 引导（`location = /security/csrf-token`）的 `X-Forwarded-For` 是**覆盖式**
   （`$remote_addr`），刻意不复用 `proxy-headers-basic.conf` 的追加式语义。
   这是安全边界，见 `docs/design/security/local-mutation-auth.md`。
5. 渲染结果中不允许残留 `@@占位符@@`（`render.sh` 自检 + T1）。
6. 上游端口只能来自占位符，不能写字面量（T4 断言）。
7. `location = /health` 必须用**精确匹配**：前缀写法会被更早的插件正则抢先命中
   （nginx 先记最长前缀、再按顺序检查正则，正则优先，而插件正则里含 `health`），
   结果是这段配置永远走不到，`/health` 落到插件服务上、未授权探针拿到 401。

## 占位符

写作 `@@NAME@@` 而不是 `${NAME}`：nginx 配置里到处是 `$http_host` 这类变量，
用 `@@` 定界后模板中的 `$` 全部是字面量，不必像 heredoc 那样逐行写 `\$`。

| 占位符 | 来源 | 出现位置 |
|---|---|---|
| `@@NGINX_PORT@@` | 环境变量 | L1a |
| `@@NGINX_SSL_PORT@@` | 环境变量 | L1a |
| `@@NEKO_MAIN_SERVER_PORT@@` | 环境变量 | L1c |
| `@@NGINX_SSL_CERT@@` / `@@NGINX_SSL_KEY@@` | `render_nginx_configuration` 参数 | L1a |
| `@@NEKO_MEMORY_SERVER_PORT@@` / `@@NEKO_TOOL_SERVER_PORT@@` / `@@NEKO_USER_PLUGIN_SERVER_PORT@@` | 环境变量 | L1c |
| `@@NGINX_NEKO_DIR@@` | `NGINX_NEKO_DIR` | L1b / L1c / S |
| `@@NGINX_CONF_D_DIR@@` | `NGINX_CONF_D_DIR` | L0 |

上游端口全部走占位符，**模板里不允许出现字面量端口**：改了环境变量却只生效一半
（服务换端口、反代打旧端口）会表现为 `/memory/` `/agent/` 502，很难查。
`test-render.sh` 用非默认端口渲染并断言无硬编码残留，就是为了钉住这条。

**安全约定**：证书路径只放进未导出的 `NGINX_SSL_CERT_PATH` / `NGINX_SSL_KEY_PATH`。
不要改成导出 `SSL_CERT_FILE` —— 那是 OpenSSL 用来指定 CA 包的变量，污染它会让同容器内
所有 Python / curl 子进程的证书校验走偏。

## 运行时布局

```
/app/docker/nginx/            ← 模板（随 COPY . /app 进镜像）
/etc/nginx/nginx.conf         ← L0 渲染产物
/etc/nginx/conf.d/neko-proxy.conf  ← L1b 渲染产物
/etc/nginx/neko/listener.conf      ← L1a 渲染产物
/etc/nginx/neko/locations.conf     ← L1c 渲染产物
/etc/nginx/neko/snippets/*.conf    ← S 渲染产物
```

`/etc/nginx/neko/` **不能**放在 `conf.d/` 下：`nginx.conf` 的
`include conf.d/*.conf` 会把它当成 `http` 级配置再吃一次。

渲染入口：`render_nginx_configuration()`，由 `setup_nginx_proxy()` 在证书就绪后调用；
渲染完成紧接着 `nginx -t`。热重载器（`start_nginx_reloader`）监视的文件集合已扩展为
上述全部产物，覆盖 `snippets/` 也能触发重载。

## 怎么改

| 需求 | 只改这里 |
|---|---|
| 加一条路由 / 改上游端口 | `locations.conf.template` |
| 改 TLS 策略、证书、HSTS | `server.tls.conf.template` |
| 改请求体限制、server 级开关 | `neko-proxy.conf.template` |
| 改转发头 / WS 参数 / 超时 | 对应的 `snippets/*.conf` |
| 加一个环境变量占位符 | `render.sh` 的 sed 列表 + 上表 + `test-render.sh` 的 `render_mode()` |

## 测试

```bash
bash docker/nginx/test-render.sh
```

不需要容器、root 或网络：它把渲染目标指向临时 prefix，跑完即删。PATH 里有 nginx 时
额外执行 `nginx -t`（测试里会先生成一张一次性自签证书，否则 nginx 会因证书缺失拒绝加载）。

CI 里由 `.github/workflows/docker-config.yml` 的「Nginx 分层 + 宿主脚本」job 运行，
同一个 job 还会跑看门狗、宿主预检和备份三个 harness，以及 `docker compose config` 校验。
