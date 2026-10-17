"""Contract tests for the Docker deployment surface.

Every assertion here pins something that fails *silently* at deploy time:

  * .env never reaching the container -> a configured API key stays unconfigured
  * a port changed on one side only -> the reverse proxy hits the old port (502)
  * a malformed healthcheck -> the probe is never healthy and an orchestrator
    keeps restarting a perfectly healthy container
  * a build-identity ENV placed too early -> every commit invalidates all the
    heavy layers below it
  * a wrong .dockerignore ordering -> docs the runtime reads fall back to
    built-in stubs without any error

See docker/README_Docker.md for the deployment contract itself.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "docker-compose.yml"
SERVICE = "neko-main"
DOCKERFILES = ("Dockerfile", "Dockerfile.full")
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "docker-multi-arch.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _service() -> dict:
    return _compose()["services"][SERVICE]


def test_dotenv_actually_reaches_the_container():
    # Compose 默认只把 .env 用于 ${} 插值：没有被 environment / env_file 引用的
    # 变量不会进容器。API Key 就属于这一类，症状是"按文档配了却显示未配置"。
    env_file = _service()["env_file"]
    assert env_file, "compose 没有 env_file，.env 里的 API Key 不会进容器"
    entries = env_file if isinstance(env_file, list) else [env_file]
    paths = []
    for entry in entries:
        paths.append(entry if isinstance(entry, str) else entry.get("path"))
    assert ".env" in paths, f"env_file 未指向 .env：{paths}"
    # required: false 让尚未创建 .env 的部署照常启动（否则 compose 直接报错）
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") == ".env":
            assert entry.get("required") is False, "env_file .env 必须 required: false"


def test_service_ports_are_forwarded_so_nginx_and_backends_agree():
    # 这三个端口同时决定"服务绑哪里"和"nginx 转发到哪"。只透传一部分会让
    # 反代打旧端口 —— 表现是 /memory/ /agent/ 502，很难查。
    env = _service()["environment"]
    keys = {item.split("=", 1)[0] for item in env if isinstance(item, str)}
    for key in (
        "NEKO_MEMORY_SERVER_PORT",
        "NEKO_TOOL_SERVER_PORT",
        "NEKO_USER_PLUGIN_SERVER_PORT",
    ):
        assert key in keys, f"{key} 没有透传：改了它 nginx 与后端会不一致"


def test_healthcheck_probes_backends_from_inside_the_container():
    healthcheck = _service()["healthcheck"]
    command = " ".join(healthcheck["test"])
    # 必须走容器内回环：从宿主打 /health 会撞实例门禁拿 401，探针永远不健康。
    assert "127.0.0.1" in command, "健康检查没有走容器内回环"
    for var in (
        "NEKO_MAIN_SERVER_PORT",
        "NEKO_MEMORY_SERVER_PORT",
        "NEKO_TOOL_SERVER_PORT",
    ):
        assert var in command, f"健康检查漏了 {var}：只探主服务发现不了「半死」"
    # $$ 是 Compose 转义：写成 $ 会被 Compose 在解析阶段吃掉
    assert "$$" in command, "shell 变量没有用 $$ 转义，会被 Compose 插值"
    assert healthcheck.get("start_period"), "缺少 start_period：启动期会被误判不健康"
    assert "healthcheck" in COMPOSE.read_text(encoding="utf-8")


def test_compose_is_parameterised_for_multiple_instances():
    # 中枢要常驻，且一个宿主可能跑多个实例：容器名 / 路径 / 宿主端口必须可覆盖，
    # 否则第二个实例会直接撞车。
    service = _service()
    assert "${NEKO_CONTAINER_NAME:-" in service["container_name"]
    volumes = " ".join(service["volumes"])
    assert "${NEKO_HOME_DIR:-" in volumes, "数据目录不可覆盖，多实例会共用同一份数据"
    assert "${NEKO_LOGS_DIR:-" in volumes
    ports = " ".join(service["ports"])
    assert "${NEKO_HTTP_PORT:-" in ports and "${NEKO_HTTPS_PORT:-" in ports


def test_build_identity_is_injected_after_the_heavy_layers():
    for name in DOCKERFILES:
        text = (ROOT / "docker" / name).read_text(encoding="utf-8")
        assert "ARG NEKO_BUILD_VERSION" in text, name
        assert "ARG NEKO_BUILD_REVISION" in text, name
        assert "ENV NEKO_BUILD_VERSION=" in text, name
        # ENV 的值每个 commit 都变。放在重活之前会让 apt / uv sync / 模型资产
        # 每次构建全量失效；必须排在这些步骤之后。
        heavy = text.index("uv sync --frozen")
        identity = text.index("ENV NEKO_BUILD_VERSION=")
        assert identity > heavy, f"{name}: 构建身份 ENV 排在重活之前，会让缓存全废"


def test_ci_passes_build_identity_to_both_images():
    workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("NEKO_BUILD_VERSION=") == 2, "两个 build 步骤都要传版本"
    assert workflow.count("NEKO_BUILD_REVISION=") == 2, "两个 build 步骤都要传 revision"


def test_dockerignore_keeps_the_docs_the_runtime_reads():
    # main_routers/agent_router.py 运行时要读 docs/<lang>/guide/openclaw_guide.md；
    # 被排除时不会报错，只会静默退回内置短桩文案。
    lines = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "docs/" in lines
    exclude_at = lines.index("docs/")
    negations = [line for line in lines if line.startswith("!docs/")]
    assert negations, ".dockerignore 里没有任何 docs 的否定规则：指南在镜像里不存在"
    # Docker 的规则是"最后命中的模式生效"，否定必须排在排除之后
    for pattern in negations:
        assert lines.index(pattern) > exclude_at, f"{pattern} 排在 docs/ 之前，不生效"
