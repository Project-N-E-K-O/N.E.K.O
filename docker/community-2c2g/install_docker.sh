#!/usr/bin/env bash
# ============================================================================
# N.E.K.O - 社区 2C2G 方案：Ubuntu Docker 自动安装与环境配置脚本
# ----------------------------------------------------------------------------
# 针对 docker/community-2c2g 这套"2核2G 极限生存部署"专门编写：
#
#   1. 环境检测   - 发行版 / 架构 / root / 已有 Docker / Compose V2 版本
#   2. 环境补全   - 补装必备依赖 (bash curl timeout flock)、docker 与 compose v2
#   3. 内存优化   - 可选安装 ZRAM + Swapfile + swappiness=10（针对 2G 内存）
#   4. 启动服务   - 可选一键拉起 community-2c2g/docker-compose.yaml
#
# 依据：community-2c2g/README.md「2.1 前置条件」「5. ZRAM 与 Swap」
#
# 用法（在 community-2c2g 目录下）：
#   sudo bash install_docker.sh                  # 仅安装/配置 Docker + 必备依赖
#   sudo bash install_docker.sh --optimize       # 额外应用 ZRAM/Swap 内存优化
#   sudo bash install_docker.sh --start          # 安装后拉起 2C2G 容器
#   sudo bash install_docker.sh --start --optimize
#   sudo bash install_docker.sh --stop           # 停止容器
# ============================================================================

set -Eeuo pipefail

# ---------------------------------------------------------------------------
# 颜色与日志
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo; echo -e "${BLUE}==== $* ====${NC}"; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yaml"

MODE="install-only"        # install-only | start | stop
DO_OPTIMIZE="false"

for arg in "$@"; do
    case "${arg}" in
        --start)    MODE="start" ;;
        --stop)     MODE="stop" ;;
        --install-only) MODE="install-only" ;;
        --optimize) DO_OPTIMIZE="true" ;;
        -h|--help)
            echo "用法: sudo bash $0 [--start|--stop|--install-only] [--optimize]"
            echo "  --start      安装后拉起 community-2c2g 容器"
            echo "  --stop       停止容器（不安装）"
            echo "  --optimize   额外配置 ZRAM + Swapfile + swappiness=10"
            exit 0
            ;;
        *) log_error "未知参数: ${arg}；--help 查看帮助"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# 1. 环境检测
# ---------------------------------------------------------------------------
log_step "环境检测"

[[ "${EUID}" -eq 0 ]] || { log_error "请使用 root 运行：sudo bash $0"; exit 1; }
log_info "检测到 root 权限。"

[[ -f /etc/os-release ]] || { log_error "未找到 /etc/os-release，仅支持 Ubuntu/Debian。"; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
log_info "发行版：${PRETTY_NAME:-${NAME} ${VERSION}}"
case "${ID}" in
    ubuntu|debian) : ;;
    *) log_error "当前发行版 '${ID}' 非 ubuntu/debian，本脚本不支持。"; exit 1 ;;
esac

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "${ARCH}" in
    amd64|x86_64)  DOCKER_ARCH="amd64" ;;
    arm64|aarch64) DOCKER_ARCH="arm64" ;;
    armhf|armv7l)  DOCKER_ARCH="armhf" ;;
    *) log_error "不支持的架构：${ARCH}"; exit 1 ;;
esac
log_info "架构：${ARCH} (Docker 源使用 ${DOCKER_ARCH})"

# 内存检测（决定是否提示开启 --optimize）
MEM_KB="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
MEM_GB=$(( MEM_KB / 1024 / 1024 ))
log_info "物理内存约 ${MEM_GB} GB。"
if [[ "${MEM_GB}" -le 2 && "${DO_OPTIMIZE}" != "true" ]]; then
    log_warn "检测到 ≤2G 内存（本方案面向 2C2G），强烈建议追加 --optimize 开启 ZRAM/Swap。"
fi

# ---------------------------------------------------------------------------
# 2. 必备依赖检测（README 2.1：bash curl timeout flock）
# ---------------------------------------------------------------------------
log_step "必备依赖检测 (bash/curl/timeout/flock)"
NEED_APT=""
for dep in bash curl timeout flock; do
    if command -v "${dep}" >/dev/null 2>&1; then
        log_info "${dep}: 已安装 ($(command -v "${dep}"))"
    else
        log_warn "${dep}: 缺失，将在下方补装。"
        NEED_APT="${NEED_APT} ${dep}"
    fi
done

# ---------------------------------------------------------------------------
# 3. Docker 与 Compose V2 检测/安装
# ---------------------------------------------------------------------------
log_step "Docker 与 Compose V2 检测"

install_docker="true"
if command -v docker >/dev/null 2>&1; then
    log_info "已检测到 Docker：$(docker --version 2>/dev/null || echo unknown)"
    if docker info >/dev/null 2>&1; then
        install_docker="false"
        log_info "Docker 守护进程可用，跳过重装。"
    else
        log_warn "Docker 已装但守护进程不可用，将尝试修复。"
    fi
fi

# Compose V2：必须用新版 `docker compose`，不能用旧版 python 的 docker-compose v1
compose_v2_ok="false"
if docker compose version >/dev/null 2>&1; then
    compose_v2_ok="true"
    log_info "Compose V2 已就绪：$(docker compose version 2>/dev/null)"
elif command -v docker-compose >/dev/null 2>&1; then
    log_warn "检测到旧版 docker-compose v1，本方案要求 V2，将补装官方 compose 插件。"
else
    log_warn "未检测到 Compose V2，将补装 docker-compose-plugin。"
fi

# ---------------------------------------------------------------------------
# 4. 环境补全 + 安装
# ---------------------------------------------------------------------------
if [[ "${install_docker}" == "true" || "${compose_v2_ok}" != "true" || -n "${NEED_APT}" ]]; then
    log_step "安装/补全依赖与 Docker"

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y \
        ca-certificates curl gnupg lsb-release apt-transport-https \
        software-properties-common coreutils util-linux

    # 已缺失的依赖再次确认（coreutils/util-linux 已覆盖 timeout/flock）
    for dep in bash curl timeout flock; do :; done

    install -m 0755 -d /etc/apt/keyrings
    DOCKER_APT_BASE=""
    for src in \
        "https://mirrors.aliyun.com/docker-ce" \
        "https://mirrors.ustc.edu.cn/docker-ce" \
        "https://download.docker.com" \
        "https://mirror.tuna.tsinghua.edu.cn/docker-ce"; do
        if curl -fsSL --max-time 10 "${src}/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.gpg 2>/dev/null; then
            DOCKER_APT_BASE="${src}"; log_info "使用 Docker 软件源：${src}"; break
        fi
    done
    [[ -z "${DOCKER_APT_BASE}" ]] && { log_error "无法获取任何 Docker 源 GPG 密钥，请检查网络。"; exit 1; }
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=${DOCKER_ARCH} signed-by=/etc/apt/keyrings/docker.gpg] ${DOCKER_APT_BASE}/linux/${ID} ${VERSION_CODENAME} stable" \
        > "/etc/apt/sources.list.d/docker.list"
    apt-get update -y

    if [[ "${install_docker}" == "true" ]]; then
        log_info "安装 docker-ce / cli / containerd / buildx / compose 插件..."
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    else
        log_info "Docker 已装，仅补装 buildx / compose 插件..."
        apt-get install -y docker-buildx-plugin docker-compose-plugin 2>/dev/null || true
    fi
fi

# 补验 Compose V2（docker-compose 命令不保证指向 V2，统一以 `docker compose` 为准）
if ! docker compose version >/dev/null 2>&1; then
    log_error "Compose V2 仍不可用。请检查：docker compose version"
    exit 1
fi
log_info "Compose V2 确认：$(docker compose version)"

# ---------------------------------------------------------------------------
# 5. daemon.json 日志限制（README 6：10m × 3，追加而非覆盖）
# ---------------------------------------------------------------------------
log_step "配置 Docker 日志限制 (10m × 3)"
_daemon=/etc/docker/daemon.json
if [[ -f "${_daemon}" ]] && command -v python3 >/dev/null 2>&1; then
    python3 - "${_daemon}" <<'PY'
import json,sys
p=sys.argv[1]
try:
    d=json.load(open(p))
except Exception:
    d={}
d.setdefault("log-driver","json-file")
d.setdefault("log-opts",{}).update({"max-size":"10m","max-file":"3"})
json.dump(d,open(p,"w"),indent=2)
print("merged log options into",p)
PY
else
    grep -q "log-opts" "${_daemon}" 2>/dev/null || {
        cp "${_daemon}" "${_daemon}.bak.$(date +%s)" 2>/dev/null || true
        cat > "${_daemon}" <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
    }
fi

# 当前用户加入 docker 组
if [[ -n "${SUDO_USER:-}" ]] && ! id -nG "${SUDO_USER}" | grep -qw docker; then
    usermod -aG docker "${SUDO_USER}" || true
    log_warn "已将用户 ${SUDO_USER} 加入 docker 组，请注销重登（或 newgrp docker）后免 sudo 生效。"
fi

log_info "启动 Docker 并设为开机自启..."
systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true
sleep 2
docker info >/dev/null 2>&1 || { log_error "Docker 未能启动，请检查：journalctl -u docker"; exit 1; }
log_info "Docker 就绪：$(docker --version) | $(docker compose version)"

# ---------------------------------------------------------------------------
# 6. 内存优化（--optimize | README 5：ZRAM + Swapfile + swappiness）
# ---------------------------------------------------------------------------
if [[ "${DO_OPTIMIZE}" == "true" ]]; then
    log_step "应用 2C2G 内存优化 (ZRAM + Swapfile)"

    apt-get install -y zram-tools 2>/dev/null || log_warn "安装 zram-tools 失败，将尝试保留现有 swap。"

    # ZRAM 配置：lz4 + 物理内存 50% + 最高优先级
    if [[ -f /etc/default/zramswap ]]; then
        log_info "写入 /etc/default/zramswap ..."
        cat > /etc/default/zramswap <<EOF
# Managed by install_docker.sh (N.E.K.O. 2C2G)
ALGO=lz4
PERCENT=50
PRIORITY=100
EOF
        systemctl restart zramswap 2>/dev/null || service zramswap restart 2>/dev/null || true
    fi

    # Swapfile 兜底（保留 2G-4G）
    if ! swapon --show 2>/dev/null | grep -q swap; then
        if [[ ! -f /swapfile ]]; then
            log_info "创建 /swapfile (2G) ..."
            fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
            chmod 600 /swapfile
            mkswap /swapfile >/dev/null
        fi
    fi
    # 真正挂载 swapfile（若未挂载）
    if [[ -f /swapfile ]] && ! swapon --show 2>/dev/null | grep -q '/swapfile'; then
        swapon /swapfile 2>/dev/null || true
        grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi

    # swappiness=10 永久生效
    sysctl vm.swappiness=10 >/dev/null 2>&1 || true
    grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
    log_info "内存优化完成：swapon --show 查看详情。"
fi

# ---------------------------------------------------------------------------
# 自动测速：为辅助镜像 (alpine) 挑选最快可用的镜像源，写入 .env 供 compose 使用
#   背景：主镜像走 gh-proxy；而 neko-init / neko-cron-install 的 alpine 若直连
#   Docker Hub，内地 ECS 上可能拉取超时并使整组容器瘫痪（neko-main depends_on
#   neko-init）。这里在 compose up 前：探测各候选注册表端点往返耗时（快→优，
#   不整张下载），对最快者真实 docker pull 验证一次，再把结果写进 .env 的
#   NEKO_INIT_IMAGE。候选源可用 NEKO_INIT_IMAGE_CANDIDATES 覆盖（空格分隔主机名）。
# ---------------------------------------------------------------------------
select_alpine_mirror() {
    local candidates=("docker.gh-proxy.org" "docker.1ms.run" "docker.m.daocloud.io" "dockerproxy.net" "")
    if [[ -n "${NEKO_INIT_IMAGE_CANDIDATES:-}" ]]; then
        # shellcheck disable=SC2206
        candidates=(${NEKO_INIT_IMAGE_CANDIDATES})
    fi

    log_step "自动测速：探测最快的 Alpine 镜像源"
    local host url out code t best="" best_t=999.0 chosen="" envfile="${SCRIPT_DIR}/.env"

    for host in "${candidates[@]}"; do
        if [[ -z "${host}" ]]; then
            log_info "  (跳过 Docker Hub 直连——留作最终候选)"
            continue
        fi
        url="https://${host}/v2/library/alpine/manifests/3.20"
        # 用 -L 跟随重定向并按最终状态码分类：有些源(如 gh-proxy.org)返回 301 但最终 404，
        # 不跟随会被误判为"可用"而抢先选中，导致白试一次。最终 401 = 需 token 但路由正确（可达）。
        out="$(curl -sSL -o /dev/null -m 5 -w '%{http_code} %{time_total}' \
            -H 'Accept: application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json' \
            "${url}" 2>/dev/null || true)"
        code="${out%% *}"; t="${out##* }"; code="${code:-000}"; t="${t:-99.0}"
        case "${code}" in
            200|202|203|204|206|304|401)
                if awk -v c="${t}" -v b="${best_t}" 'BEGIN{exit !(c+0 < b+0)}'; then
                    best_t="${t}"; best="${host}"
                    log_info "  ✓ ${host} HTTP ${code} 用时 ${t}s —— 当前最快"
                else
                    log_info "    ${host} HTTP ${code} 用时 ${t}s（保留更快者：${best}）"
                fi
                ;;
            *) log_warn "    ${host}: 不可用（HTTP ${code}）" ;;
        esac
    done

    # 对最快者做真实 docker pull 验证；失败退回 Docker Hub 直连
    if [[ -n "${best}" ]] && timeout 120 docker pull "${best}/library/alpine:3.20" >/dev/null 2>&1; then
        chosen="${best}/library/alpine:3.20"
    fi
    if [[ -z "${chosen}" ]] && timeout 120 docker pull "alpine:3.20" >/dev/null 2>&1; then
        chosen="alpine:3.20"
    fi

    if [[ -n "${chosen}" ]]; then
        # 幂等写入 .env（替换已有 NEKO_INIT_IMAGE），保证 compose 读取
        if [[ -f "${envfile}" ]] && grep -q '^[[:space:]]*NEKO_INIT_IMAGE=' "${envfile}"; then
            sed -i '/^[[:space:]]*NEKO_INIT_IMAGE=/d' "${envfile}"
        fi
        printf 'NEKO_INIT_IMAGE=%s\n' "${chosen}" >> "${envfile}"
        export NEKO_INIT_IMAGE="${chosen}"
        log_info "✅ 选定 Alpine 源：${chosen}（已写入 ${envfile}）"
    else
        log_warn "所有候选镜像源都无法拉取 alpine:3.20；请手动在 ${envfile} 配置 NEKO_INIT_IMAGE。"
    fi
}

# ---------------------------------------------------------------------------
# 7. 启动 / 停止服务
# ---------------------------------------------------------------------------
if [[ "${MODE}" == "start" ]]; then
    log_step "启动 community-2c2g 容器"
    [[ -f "${COMPOSE_FILE}" ]] || { log_error "未找到 ${COMPOSE_FILE}"; exit 1; }
    if [[ ! -f "${SCRIPT_DIR}/.env" ]] && [[ -f "${SCRIPT_DIR}/../env.template" ]]; then
        cp "${SCRIPT_DIR}/../env.template" "${SCRIPT_DIR}/.env"
        log_warn "已从 env.template 生成 .env，请按需填写后重新执行 --start。"
    fi
    # 自动测速选最快的 Alpine 镜像源（失败不影响主镜像，仅告警）
    command -v curl >/dev/null 2>&1 && select_alpine_mirror || log_warn "未找到 curl，跳过 Alpine 镜像测速。"
    docker compose -f "${COMPOSE_FILE}" config --quiet && echo "compose 语法预检通过"
    docker compose -f "${COMPOSE_FILE}" up -d
    log_info "已启动。首次访问：https://127.0.0.1:48912 （可 SSH 转发：ssh -L 48912:127.0.0.1:48912 ...）"
    log_info "查看看门狗：/opt/neko/watchdog.sh；日志：docker compose -f ${COMPOSE_FILE} logs -f"

elif [[ "${MODE}" == "stop" ]]; then
    log_step "停止 community-2c2g 容器"
    if [[ -f "${COMPOSE_FILE}" ]]; then
        docker compose -f "${COMPOSE_FILE}" down || true
        log_info "已停止。"
    else
        log_warn "未找到 ${COMPOSE_FILE}，跳过。"
    fi
fi

log_step "完成"
log_info "提示：看门狗由 neko-cron-install 容器安装到 /opt/neko；维护时先 sudo touch /opt/neko/disabled 暂停。"
