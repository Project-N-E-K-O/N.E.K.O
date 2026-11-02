#!/bin/sh
# =============================================================================
# N.E.K.O. 宿主机备份脚本
# =============================================================================
#
#   sudo sh docker/backup.sh [选项]
#
# 只做三件**客观**的事，不做任何策略决定：
#   1. 按固定清单打包「不可重建的主副本」
#   2. 排除可以从镜像 / 网络重建的内容（插件 vendor/ 等），避免备份白涨体积
#   3. 生成校验和与文件清单，让恢复方**能验证**备份是完整的
#
# 频率、保留几份、能不能停机 —— 由调用方决定（--keep / --no-stop），脚本不猜。
#
# 默认冷备（先停容器再打包）。原因：memory/ 下有 SQLite（time_indexed.db），
# 运行中直接 tar 可能拷到**撕裂的数据库**，而症状要到恢复当天才出现。
# 确实不能停机时用 --no-stop，脚本会明确告诉你这次没有一致性保证。
#
# 选项：
#   --home DIR        数据目录，默认 docker/neko-home
#   --out DIR         备份输出目录，默认 docker/backups
#   --container NAME  容器名，默认 neko（配合 compose 的 NEKO_CONTAINER_NAME）
#   --keep N          只保留最近 N 份备份（默认 0 = 一份都不删）
#   --no-stop         不停容器（放弃一致性保证）
#   --dry-run         只列出将打包/排除的内容，不写任何文件
#   -h, --help        帮助
# =============================================================================
set -eu

# 备份里有 API Key（config/core_config.json）与实例访问密钥（instance_access.key），
# 还有 TLS 私钥。宿主上 sudo 默认 umask 022，会让输出目录 0755、归档 0644 ——
# 同机其它账号能直接读。在任何文件被创建之前先把 umask 收紧。
umask 077

DRY_RUN=0
SLIM=0
KEEP=0
STOP_CONTAINER=1
CONTAINER=${NEKO_CONTAINER_NAME:-neko}

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
home_dir=$script_dir/neko-home
out_dir=$script_dir/backups

usage() {
    sed -n '2,25s/^# \{0,1\}//p' "$0"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --home)      [ "$#" -ge 2 ] || { echo "backup: --home needs a value" >&2; exit 2; }; home_dir=$2; shift 2 ;;
        --out)       [ "$#" -ge 2 ] || { echo "backup: --out needs a value" >&2; exit 2; }; out_dir=$2; shift 2 ;;
        --container) [ "$#" -ge 2 ] || { echo "backup: --container needs a value" >&2; exit 2; }; CONTAINER=$2; shift 2 ;;
        --keep)      [ "$#" -ge 2 ] || { echo "backup: --keep needs a value" >&2; exit 2; }; KEEP=$2; shift 2 ;;
        --no-stop)   STOP_CONTAINER=0; shift ;;
        --slim)      SLIM=1; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "backup: unknown argument '$1' (see --help)" >&2; exit 2 ;;
    esac
done

case "$KEEP" in ''|*[!0-9]*) echo "backup: --keep must be a non-negative integer" >&2; exit 2 ;; esac

# 相对路径按 docker/ 解析，与 Compose 对 ./$ 的解析一致
case "$home_dir" in /*) ;; *) home_dir=$script_dir/$home_dir ;; esac
case "$out_dir"  in /*) ;; *) out_dir=$script_dir/$out_dir ;; esac

# ── 运行环境 ────────────────────────────────────────────────────────────────
# 本脚本面向 Linux 宿主：用到 GNU stat / tar / flock。BSD/macOS 上的 stat 不认 -c，
# 在 set -eu 下会以"命令失败"的形式静默退出，所以先明确报错。
stat -c '%u' . >/dev/null 2>&1 || {
    echo "backup: 需要 GNU stat（Linux 宿主）；BSD/macOS 请改用 docker run --volumes-from 方式备份" >&2
    exit 1
}

# ── 数据目录必须是真实目录：Docker 会挂载符号链接的目标 ──────────────────────
[ -d "$home_dir" ] || { echo "backup: 数据目录不存在: $home_dir" >&2; exit 1; }
[ ! -L "$home_dir" ] || {
    echo "backup: $home_dir 是符号链接。" >&2
    echo "  Docker 挂载的是链接目标，请对真实目录做备份，否则你备份的东西和容器用的不是同一个。" >&2
    exit 1
}

state=$home_dir/.local/share/N.E.K.O

# ── 主副本清单：丢了不可重建 ────────────────────────────────────────────────
# 每一项都对应一个明确的数据类别；缺失的项跳过并提示（全新安装本来就不全）。
# 名单必须与 utils/config_manager/storage_roots.py 里的持久化目录一致 ——
# 那里少一项，这里就少备份一类用户资产，而脚本照样报成功。
# 契约测试 test_docker_deployment_contract.py 会静态比对这两处。
MUST_LIST="
config
memory
knowledge
plugins
live2d
vrm
mmd
pngtuber
avatar_tools
workshop
character_cards
card_faces
state
cloudsave
.cloudsave_staging
cloudsave_backups
.neko-plugin-installations
.neko-plugin-packages
.neko-package-profiles
instance_access.key
"

found=''
missing=''
for item in $MUST_LIST; do
    [ -n "$item" ] || continue
    if [ -e "$state/$item" ]; then
        found="$found $item"
    else
        missing="$missing $item"
    fi
done

echo "backup: 数据目录 $home_dir"
echo "backup: 将打包:$found"
[ -n "$missing" ] && echo "backup: 不存在（跳过，全新安装属正常）:$missing"

# ssl/ 与 instance_access.key 决定"终端要不要重新配对"，单独点名提示
[ -e "$home_dir/ssl" ] || echo "backup: 注意：未找到 $home_dir/ssl（TLS 证书与私钥）"

# ── 排除清单 ────────────────────────────────────────────────────────────────
# 默认**不排除**插件的 vendor/：那是插件的 Python 依赖，容器启动时不会重建，
# 少了它恢复出来的插件会直接报「依赖缺失」并被跳过 —— 恢复了数据却少了能力。
# 想让备份瘦身，用 --slim 明确接受"恢复后要重新安装插件依赖"。
EXCLUDES="
--exclude=*/__pycache__
--exclude=*.pyc
--exclude=.DS_Store
"
if [ "$SLIM" = "1" ]; then
    EXCLUDES="--exclude=*/.neko-plugin-installations/*/vendor $EXCLUDES"
fi

if [ "$DRY_RUN" = "1" ]; then
    echo "backup: --dry-run，未写入任何文件"
    echo "backup: 若正式执行，将排除:"
    printf '%s\n' "$EXCLUDES" | sed '/^$/d;s/^/    /'
    exit 0
fi

mkdir -p "$out_dir"
[ ! -L "$out_dir" ] || { echo "backup: 输出目录不能是符号链接: $out_dir" >&2; exit 1; }
# 已存在的目录不会因为 umask 变紧而改变权限：显式收紧，否则早先以 022 建的
# 目录会继续把备份暴露给同机其它账号。
# 属主不符必须拒绝，不能只警告：下面的锁文件是 `exec 8>"$out_dir/.neko-backup.lock"`，
# shell 重定向会**跟随符号链接**。如果这个目录属于别的账户（或落在别人可写的位置），
# 对方可以先放一个同名符号链接，root 的备份就会把链接目标截断。
owner=$(stat -c '%u' "$out_dir")
if [ "$owner" != "$(id -u)" ]; then
    echo "backup: ❌ 输出目录 $out_dir 属于 uid $owner，不是当前用户" >&2
    echo "backup:    拒绝在这里创建备份与锁文件：锁文件通过 shell 重定向创建，" >&2
    echo "backup:    别人可写的目录里可以预置符号链接，把 root 的写入引到任意文件上。" >&2
    echo "backup:    换一个归属自己的 --out，或先把目录交还给正确的属主。" >&2
    exit 1
fi
chmod 700 "$out_dir" 2>/dev/null || \
    echo "backup: ⚠️  无法收紧 $out_dir 的权限，请确认它不对其他本机账号可读" >&2

# 输出目录不能落在数据目录里面，否则备份会被下一次备份打进去
resolved_home=$(cd -- "$home_dir" && pwd -P)
resolved_out=$(cd -- "$out_dir" && pwd -P)
case "$resolved_out/" in
    "$resolved_home"/*) echo "backup: 输出目录不能位于数据目录内部" >&2; exit 1 ;;
esac

# 同一秒内的两次调用（手动 + 定时撞车）会选中同一个文件名，第二次的 tar -czf
# 会截断第一份归档 —— 也就是悄悄毁掉一份可能是最后的完好备份。
# 名字里带 PID，输出目录上一把锁，双保险。
# 冷备必须能串行化：没有 flock 时两把锁都会被静默跳过，而冷备照做 ——
# 两次调用（不同 --out）就可能重叠：第一次结束时把容器拉起来，第二次还在读
# SQLite，结果是一份"报告成功但没有一致性保证"的备份。
# --no-stop 本来就不承诺一致性，那边只告警。
if ! command -v flock >/dev/null 2>&1; then
    if [ "$STOP_CONTAINER" = "1" ]; then
        echo "backup: ❌ 冷备需要 flock（util-linux）来串行化同一实例的备份。" >&2
        echo "backup:    没有它时两次备份可能重叠：第一次结束时把容器拉起来，" >&2
        echo "backup:    第二次还在读 SQLite —— 备份会报告成功，但一致性保证不成立。" >&2
        echo "backup:    装上 flock，或用 --no-stop 明确接受无锁。" >&2
        exit 1
    fi
    echo "backup: ⚠️  未找到 flock：本次不做并发保护（--no-stop 已明确接受风险）"
fi

if command -v flock >/dev/null 2>&1; then
    exec 8>"$out_dir/.neko-backup.lock"
    if ! flock -n 8; then
        echo "backup: 另一个备份正在进行（锁 $out_dir/.neko-backup.lock），本次退出" >&2
        exit 1
    fi
    # 输出目录锁只防"同一目录写冲突"，防不住"同一实例、不同 --out"的重叠：
    # 第二次冷备看到容器已停就开始拷，而第一次结束时会把容器拉起来，
    # 于是第二次正在读的 SQLite 又被写入。再按数据目录加一把实例锁。
    #
    # 锁目录不能放 /tmp：那是所有账号可写的公共目录，攻击者可以抢先在该路径上放一个
    # 符号链接，root 的 sudo 备份用 shell 重定向打开它时会把链接目标截断。
    # 用只有自己可写的目录，并校验属主。
    if [ -n "${NEKO_BACKUP_LOCK_DIR:-}" ]; then
        LOCK_DIR="$NEKO_BACKUP_LOCK_DIR"
    elif [ "$(id -u)" = "0" ]; then
        LOCK_DIR=/run/neko-backup
    else
        LOCK_DIR="${XDG_RUNTIME_DIR:-$HOME/.cache}/neko-backup"
    fi
    if ! mkdir -p "$LOCK_DIR" 2>/dev/null || [ -L "$LOCK_DIR" ] \
       || [ "$(stat -c '%u' "$LOCK_DIR" 2>/dev/null)" != "$(id -u)" ]; then
        echo "backup: 锁目录不安全或不可用: $LOCK_DIR" >&2
        echo "backup:    它必须是当前用户（root 时即 root）独占的普通目录；" >&2
        echo "backup:    可用 NEKO_BACKUP_LOCK_DIR 指定别的路径。" >&2
        exit 1
    fi
    chmod 700 "$LOCK_DIR" 2>/dev/null || true
    _instance_key=$(printf '%s' "$resolved_home" | cksum | cut -d' ' -f1)
    INSTANCE_LOCK="$LOCK_DIR/instance-$_instance_key.lock"
    if ! exec 7>"$INSTANCE_LOCK"; then
        echo "backup: 无法创建实例锁 $INSTANCE_LOCK" >&2
        exit 1
    fi
    if ! flock -n 7; then
        echo "backup: 同一实例已有备份在进行（锁 $INSTANCE_LOCK），本次退出" >&2
        exit 1
    fi
    unset _instance_key
fi
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$out_dir/neko-backup-$stamp-$$.tar.gz"

# ── 冷备：先停容器，退出时恢复原来的运行状态 ────────────────────────────────
# 冷备的前提是"确认容器已停"。无法确认时不能默默按冷备继续 —— 真实数据容器
# 可能仍在写 SQLite，而脚本不会给出一致性警告，这等于把最危险的场景伪装成安全。
# 注意别用 $state 这个名字：它上面是数据根（$home_dir/.local/share/N.E.K.O）。
container_state=''
if ! command -v docker >/dev/null 2>&1; then
    container_state='no-docker-cli'
elif ! container_state=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null); then
    container_state='inspect-failed'
fi

container_was_running=0

# 恢复钩子必须在 docker stop **之前**装好：stop 成功但随后的 inspect 失败时
# 会走 exit，如果那时还没装 trap，容器就永远停在停止状态 ——
# 一次失败的备份不该换来一段计划外停机。
restart_container() {
    if [ "$container_was_running" = "1" ]; then
        echo "backup: 重新启动容器 $CONTAINER"
        docker start "$CONTAINER" >/dev/null || echo "backup: ⚠️ 容器启动失败，请手动检查" >&2
    fi
}
trap restart_container EXIT
# INT/TERM 不能直接挂 restart_container：它只把容器拉起来、不退出脚本，
# 后面的打包会继续 —— 而容器此时正在重新写 SQLite，冷备的一致性就没了。
# 让信号先退出，再由 EXIT trap 负责恢复运行状态。
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$STOP_CONTAINER" = "1" ]; then
    case "$container_state" in
        true)
            container_was_running=1
            echo "backup: 停止容器 $CONTAINER（冷备，保证 SQLite 一致）"
            docker stop "$CONTAINER" >/dev/null
            if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo unknown)" != "false" ]; then
                echo "backup: ❌ 无法确认容器 $CONTAINER 已停止" >&2
                exit 1
            fi
            ;;
        false)
            echo "backup: 容器 $CONTAINER 未在运行，按冷备继续"
            ;;
        no-docker-cli)
            # 两条不可确认的分支共用同一句开场，既有助排障，也让回归测试
            # 不必猜是哪条分支（CI 上有 docker、本地没有，正好各走一条）。
            echo "backup: ❌ 无法确认容器 $CONTAINER 的状态：未找到 docker CLI。" >&2
            echo "backup:    这不是"容器没跑"的证据，可能正对着一个仍在写入的数据目录。" >&2
            echo "backup:    确认容器已停后重试，或显式接受风险：--no-stop" >&2
            exit 1
            ;;
        inspect-failed)
            echo "backup: ❌ 无法确认容器 $CONTAINER 的状态（不存在、daemon 不可用或名字写错）。" >&2
            echo "backup:    因此不能按冷备继续 —— 它可能仍在写 SQLite。" >&2
            echo "backup:    修正 --container，或显式接受风险：--no-stop" >&2
            exit 1
            ;;
    esac
else
    echo "backup: ⚠️  --no-stop：容器仍在写入。memory/time_indexed.db 等 SQLite 可能在打包"
    echo "backup:    过程中被改写到不一致状态，**本次备份不保证可恢复**。"
fi

# ── 打包 ────────────────────────────────────────────────────────────────────
# 只带清单里的子树，不整包 home：/home/neko 是挂载点，用户完全可能往里再挂别的
# 东西（备份不该把那部分一起打走，也不该因为读不到而整包失败）。
pack_list=''
for item in $MUST_LIST; do
    [ -n "$item" ] || continue
    [ -e "$state/$item" ] && pack_list="$pack_list .local/share/N.E.K.O/$item"
done
[ -e "$home_dir/ssl" ] && pack_list="$pack_list ssl"

if [ -z "$pack_list" ]; then
    echo "backup: ❌ 清单里一个数据项都不存在，没有任何可备份的内容" >&2
    echo "backup:    确认 --home 指向的是容器挂载的目录（默认 docker/neko-home）" >&2
    exit 1
fi

echo "backup: 写入 $archive"
#.tar 的非零退出码必须当失败处理：源文件读不到或打包中消失时，tar 仍可能留下
# 一个"可解压、非空"的残缺归档，只靠后面的 tar -tzf 是发现不了的 ——
# 那样脚本会报成功，还可能借着 --keep 把上一份完好备份删掉。
# shellcheck disable=SC2086
if ! tar -czf "$archive" $EXCLUDES -C "$home_dir" $pack_list; then
    echo "backup: ❌ tar 执行失败，本次备份作废（不保留残缺归档）" >&2
    rm -f "$archive" "$archive.manifest.txt"
    exit 1
fi

# ── 校验：重新读一遍归档，确认可解、条目数非零 ──────────────────────────────
if ! tar -tzf "$archive" > "$archive.manifest.txt" 2>/dev/null; then
    echo "backup: ❌ 归档无法读取，备份失败" >&2
    rm -f "$archive"
    exit 1
fi
entry_count=$(wc -l < "$archive.manifest.txt" | tr -d ' ')
if [ "$entry_count" -eq 0 ]; then
    echo "backup: ❌ 归档为空，备份失败" >&2
    rm -f "$archive" "$archive.manifest.txt"
    exit 1
fi
# 校验和单独算：恢复方可以用 sha256sum -c 验证传输或存放过程没损坏。
# 只算一次 —— 归档可能有几百 MB，冷备期间容器是停着的，别白白多读一遍。
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$out_dir" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
    echo "backup: 校验和 $archive.sha256"
fi
echo "backup: 完成，$entry_count 个条目，$(du -h "$archive" | cut -f1)"

# 恢复要点直接印出来，避免出事时还要去翻文档
# 这里刻意不打印裸的 "docker compose up"：多实例部署下它会操作**默认项目**，
# 而目标容器可能仍在往要解压的目录里写。把命令前缀留给操作者填。
echo "backup: 恢复步骤（把 <COMPOSE> 换成该实例启动时的命令前缀，多实例还要带上 -p 与同一组 NEKO_* 覆盖）:"
echo "backup:   1) <COMPOSE> down"
echo "backup:   2) 先把现有 neko-home 改名留档（万一恢复的是错的）"
echo "backup:   3) tar -xzf $archive -C <neko-home>   # 归档内是 .local/ 与 ssl/，解到数据目录本身"
echo "backup:   4) <COMPOSE> up -d，然后在 Web UI 确认角色/记忆/插件都在"
echo "backup:   5) 实例访问密钥若一并恢复，终端不需要重新配对"

# ── 轮转（只在调用方明确要求时执行，且只删本脚本自己命名的文件）─────────────
if [ "$KEEP" -gt 0 ]; then
    stale=$(ls -1t "$out_dir"/neko-backup-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" || true)
    [ -n "$stale" ] || stale=''
    if [ -n "$stale" ]; then
        printf '%s\n' "$stale" | while IFS= read -r old; do
            [ -n "$old" ] || continue
            echo "backup: 轮转删除 $(basename "$old")（保留最近 $KEEP 份）"
            rm -f "$old" "$old.sha256" "$old.manifest.txt"
        done
    fi
fi
