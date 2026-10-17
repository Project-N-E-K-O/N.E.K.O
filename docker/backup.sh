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

DRY_RUN=0
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
        --dry-run)   DRY_RUN=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "backup: unknown argument '$1' (see --help)" >&2; exit 2 ;;
    esac
done

case "$KEEP" in ''|*[!0-9]*) echo "backup: --keep must be a non-negative integer" >&2; exit 2 ;; esac

# 相对路径按 docker/ 解析，与 Compose 对 ./$ 的解析一致
case "$home_dir" in /*) ;; *) home_dir=$script_dir/$home_dir ;; esac
case "$out_dir"  in /*) ;; *) out_dir=$script_dir/$out_dir ;; esac

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
MUST_LIST="
config
characters
memory
plugins
.neko-plugin-installations
.neko-plugin-packages
.neko-package-profiles
state
cloudsave
cloudsave_staging
cloudsave_backups
instance_access.key
root_state.json
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

# ── 排除清单：可以从镜像 / 网络重建，别占备份体积 ───────────────────────────
# 插件 vendor/ 由 uv pip install 重建（需要网络）；缓存与字节码纯属噪声。
EXCLUDES="
--exclude=*/.neko-plugin-installations/*/vendor
--exclude=*/__pycache__
--exclude=*.pyc
--exclude=.DS_Store
"

if [ "$DRY_RUN" = "1" ]; then
    echo "backup: --dry-run，未写入任何文件"
    echo "backup: 若正式执行，将排除:"
    printf '%s\n' "$EXCLUDES" | sed '/^$/d;s/^/    /'
    exit 0
fi

mkdir -p "$out_dir"
[ ! -L "$out_dir" ] || { echo "backup: 输出目录不能是符号链接: $out_dir" >&2; exit 1; }

# 输出目录不能落在数据目录里面，否则备份会被下一次备份打进去
resolved_home=$(cd -- "$home_dir" && pwd -P)
resolved_out=$(cd -- "$out_dir" && pwd -P)
case "$resolved_out/" in
    "$resolved_home"/*) echo "backup: 输出目录不能位于数据目录内部" >&2; exit 1 ;;
esac

stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$out_dir/neko-backup-$stamp.tar.gz"

# ── 冷备：先停容器，退出时恢复原来的运行状态 ────────────────────────────────
container_was_running=0
if [ "$STOP_CONTAINER" = "1" ]; then
    if command -v docker >/dev/null 2>&1; then
        if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)" = "true" ]; then
            container_was_running=1
            echo "backup: 停止容器 $CONTAINER（冷备，保证 SQLite 一致）"
            docker stop "$CONTAINER" >/dev/null
        else
            echo "backup: 容器 $CONTAINER 未在运行，按冷备继续"
        fi
    else
        echo "backup: 未找到 docker CLI，无法确认容器状态；按冷备继续"
    fi
else
    echo "backup: ⚠️  --no-stop：容器仍在写入。memory/time_indexed.db 等 SQLite 可能在打包"
    echo "backup:    过程中被改写到不一致状态，**本次备份不保证可恢复**。"
fi

restart_container() {
    if [ "$container_was_running" = "1" ]; then
        echo "backup: 重新启动容器 $CONTAINER"
        docker start "$CONTAINER" >/dev/null || echo "backup: ⚠️ 容器启动失败，请手动检查" >&2
    fi
}
trap restart_container EXIT INT TERM

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
# shellcheck disable=SC2086
if ! tar -czf "$archive" $EXCLUDES -C "$home_dir" $pack_list; then
    # GNU tar 把"读取时文件被改动"也算非零；真正的判定在下面的校验
    echo "backup: ⚠️  tar 返回非零（见上方警告），继续做完整性校验"
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
# 校验和单独算：恢复方可以用 sha256sum -c 验证传输/存放过程没损坏
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$out_dir" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
fi

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$out_dir" && sha256sum "$(basename "$archive")" > "$archive.sha256")
    echo "backup: 校验和 $archive.sha256"
fi
echo "backup: 完成，$entry_count 个条目，$(du -h "$archive" | cut -f1)"

# 恢复要点直接印出来，避免出事时还要去翻文档
echo "backup: 恢复步骤:"
echo "backup:   1) docker compose down"
echo "backup:   2) 先把现有 neko-home 改名留档（万一恢复的是错的）"
echo "backup:   3) tar -xzf $archive -C <neko-home 的父目录>"
echo "backup:   4) docker compose up -d，然后在 Web UI 确认角色/记忆/插件都在"
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
