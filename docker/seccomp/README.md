# 浏览器控制用的 seccomp 配置

`chromium.json` 是给 full 镜像用的 seccomp 配置。full 镜像里的 Chromium 靠自己的沙箱隔离网页，沙箱要创建用户命名空间；Docker 默认的 seccomp 配置不允许没有 `CAP_SYS_ADMIN` 的进程这么做，Chromium 一启动就退出，浏览器工具报告浏览器启动不了。换上这份配置之后，Chromium 带着沙箱启动，不需要 `--no-sandbox`，也不需要给容器加 `--cap-add=SYS_ADMIN`。

standard 镜像没有浏览器，用不上这份配置，不要给它加。

## 它和 Docker 默认配置差在哪

只差一条规则，加在 `syscalls` 数组的最后：

```json
{
	"names": ["clone", "unshare"],
	"action": "SCMP_ACT_ALLOW"
}
```

其余内容和 [`moby/profiles`](https://github.com/moby/profiles) 的 `seccomp/default.json`（`seccomp/v0.2.4`，Docker 29.9 自带的默认配置，Apache-2.0）逐字节相同。也就是：

- 多允许的：没有 `CAP_SYS_ADMIN` 的进程创建命名空间（用户、PID、网络等）。
- 没有多允许的：`mount`、`setns`、`sethostname`、`bpf`、`perf_event_open` 这些在默认配置里要 `CAP_SYS_ADMIN` 才放行的调用，照旧不放行。进程在自己建的用户命名空间里是 root，内核本来会让它改主机名，这份配置不让（`test-chromium-profile.sh` 有一条检查盯着这一点）。

代价要说清楚：Docker 默认不允许建用户命名空间，是因为它让容器里的普通进程多碰到一块内核功能，历史上有一批内核提权漏洞要先建用户命名空间才能触发。换上这份配置，容器里所有进程（不只是 Chromium）都能建。和另外两个办法比：`--cap-add=SYS_ADMIN` 给容器的权限大得多；`--no-sandbox` 是让网页内容直接在没有隔离的浏览器进程里跑。

## 怎么用

Compose（在 `docker/` 目录下）：

```bash
docker compose -f docker-compose.yml -f compose.browser.yaml up -d
```

或者在 `docker/.env` 里写 `COMPOSE_FILE=docker-compose.yml:compose.browser.yaml`（Windows 宿主把冒号换成分号），之后照常 `docker compose up -d`。

`docker run`（在仓库根目录）：

```bash
docker run -d --name neko -p 48911:80 -p 48912:443 \
  --security-opt seccomp=docker/seccomp/chromium.json --shm-size=1g neko:full
```

`--shm-size=1g`（覆盖文件里是 `shm_size`）和 seccomp 无关，是 Chromium 在容器里的另一件事：它把页面用的共享内存放在 `/dev/shm`，Docker 默认只给 64MB，页面大的时候标签页会崩溃。1GB 是在容器里跑 Chromium 时通常用的数，没有在本项目里实测过；它是上限，不是预留。

宿主机要允许非特权用户命名空间：

- `kernel.unprivileged_userns_clone` 不能是 0（Debian 11 起默认是 1，别的发行版一般没有这个开关）。
- `user.max_user_namespaces` 不能是 0。
- Ubuntu 23.10 起还有一道 AppArmor 的限制，`kernel.apparmor_restrict_unprivileged_userns`，默认是 1。CI 用的 GitHub Ubuntu 26.04 运行机上，容器里的进程（Docker 默认的 AppArmor 配置下）不受它影响，Chromium 起得来。别的宿主机没有试过；带着这份配置仍然建不了用户命名空间时，先看这个开关。

下面的脚本会把这三个开关在宿主机上的值打出来。

## 在自己的宿主机上确认

```bash
bash docker/seccomp/test-chromium-profile.sh --quick   # 只试配置本身，十几秒
bash docker/seccomp/test-chromium-profile.sh           # 再装一个 Chromium 真的启动一次，一两分钟
```

脚本不发布端口、不碰现有部署的容器和目录，跑完会清掉自己建的东西。它检查：不带配置时建用户命名空间被拒绝；带配置时可以；带配置时 `sethostname` 仍被拒绝；按上面 `.env` 的写法经 Compose 起的容器带上了配置；Debian 的 Chromium 以 `neko` 用户、带沙箱启动并渲染出一个页面。CI 的 `Docker Config` 工作流在 `docker/` 有改动时跑这个脚本。

## 更新到新的默认配置

`chromium.json` 不手工改。改 `update.sh` 里的 `UPSTREAM_TAG` 和 `UPSTREAM_COMMIT`，然后：

```bash
bash docker/seccomp/update.sh            # 从上游重新生成
bash docker/seccomp/update.sh --check    # 核对仓库里的这份就是「上游 + 那一条规则」（CI 也跑）
bash docker/seccomp/test-chromium-profile.sh
```
