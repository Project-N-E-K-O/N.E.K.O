"""Internal plugin source repository validation helpers."""

from __future__ import annotations

import ast
import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import TypeAdapter, ValidationError

from plugin._types.entry_metadata import (
    ENTRY_CONTRACT_BOOL_FIELDS,
    ENTRY_CONTRACT_FIELDS,
    ENTRY_CONTRACT_MAPPING_FIELDS,
)
from plugin._types.plugin_types import (
    SUPPORTED_PLUGIN_TYPES,
    format_plugin_type_choice_error,
    format_removed_plugin_host,
)
from plugin.config.schema import PluginModelRequirementSchema, PluginModelUsageId
from plugin.core.entry_points import plugin_package_matches_directory
from plugin.sdk.shared.core.push_message_schema import (
    LEGACY_PUSH_MESSAGE_FIELD_MIGRATIONS,
    LEGACY_PUSH_MESSAGE_POSITIONAL_FIELDS,
    LEGACY_PUSH_MESSAGE_TRUTHY_ONLY_FIELDS,
    format_push_message_v1_static_diagnostic,
)
from plugin.sdk.shared.models.exceptions import InvalidArgumentError
from plugin.sdk.shared.storage.database import PluginDatabase
from plugin.sdk.shared.storage.state import PluginStatePersistence

from plugin.utils.source_paths import iter_plugin_python_files
from ..core.build_rules import reraise_walk_error
from ..core.plugin_source import load_plugin_source
from ..core.toml_utils import load_toml
from .deps_cmd import _is_link

_PLUGIN_RUNTIME_TIMEOUT_MAX = 300.0
_PLUGIN_ENTRIES_MAX = 1024
_PLUGIN_ENTRY_FIELDS_MAX = 64
_PLUGIN_ENTRY_KEY_LENGTH_MAX = 256
# Fields the registry coerces with bool(), so "false" would read as True.
_PLUGIN_ENTRY_BOOL_FIELDS = ENTRY_CONTRACT_BOOL_FIELDS
# Locales for new check messages: English first as the main line, the rest below it.
_MESSAGE_LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt")
_STORE_BACKEND_IGNORED = {
    "en": "[plugin.store].backend is not read by the runtime and has no effect",
    "zh-CN": "[plugin.store].backend 不会被运行时读取，写了没有效果",
    "zh-TW": "[plugin.store].backend 不會被執行環境讀取，寫了沒有效果",
    "ja": "[plugin.store].backend はランタイムで読み取られないため、効果はありません",
    "ko": "[plugin.store].backend는 런타임에서 읽지 않으므로 효과가 없습니다",
    "ru": "[plugin.store].backend не читается средой выполнения и ни на что не влияет",
    "es": "[plugin.store].backend no lo lee el entorno de ejecución y no tiene ningún efecto",
    "pt": "[plugin.store].backend não é lido pelo ambiente de execução e não tem efeito",
}


_DATABASE_NAME_INVALID = {
    "en": "[plugin.database].name must be a plain file name that also works on Windows: "
    "no folders, drive letters, \"..\", characters <>:\"/\\|?*, a trailing dot or space, or reserved names such as CON or NUL",
    "zh-CN": "[plugin.database].name 必须是在 Windows 上也能用的单纯文件名：不能带目录、盘符、\"..\"、"
    "字符 <>:\"/\\|?*，不能以点或空格结尾，也不能用 CON、NUL 这类保留名",
    "zh-TW": "[plugin.database].name 必須是在 Windows 上也能用的單純檔案名稱：不能帶目錄、磁碟代號、\"..\"、"
    "字元 <>:\"/\\|?*，不能以點或空格結尾，也不能用 CON、NUL 這類保留名稱",
    "ja": "[plugin.database].name は Windows でも使える単純なファイル名にしてください。フォルダー、ドライブ文字、"
    "\"..\"、文字 <>:\"/\\|?*、末尾のドットや空白、CON や NUL などの予約名は使えません",
    "ko": "[plugin.database].name은 Windows에서도 쓸 수 있는 파일 이름만 써야 합니다. 폴더, 드라이브 문자, "
    "\"..\", 문자 <>:\"/\\|?*, 끝의 마침표나 공백, CON·NUL 같은 예약 이름은 쓸 수 없습니다",
    "ru": "[plugin.database].name должно быть простым именем файла, допустимым и в Windows: без папок, букв дисков, "
    "\"..\", символов <>:\"/\\|?*, точки или пробела в конце и зарезервированных имён вроде CON или NUL",
    "es": "[plugin.database].name debe ser un nombre de archivo simple que también sirva en Windows: sin carpetas, "
    "letras de unidad, \"..\", caracteres <>:\"/\\|?*, punto o espacio final ni nombres reservados como CON o NUL",
    "pt": "[plugin.database].name deve ser um nome de arquivo simples que também funcione no Windows: sem pastas, "
    "letras de unidade, \"..\", caracteres <>:\"/\\|?*, ponto ou espaço no final nem nomes reservados como CON ou NUL",
}
# Windows refuses these in any file name, and these device names with any extension.
_WINDOWS_FORBIDDEN_FILENAME_CHARS = frozenset('<>:"/\\|?*') | {chr(code) for code in range(32)}
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{device}{digit}" for device in ("COM", "LPT") for digit in "123456789\u00b9\u00b2\u00b3"}
)
# Hint added under the persist_mode error for the old spellings; {new} / {old} are filled in.
_PERSIST_MODE_MIGRATION_HINT = {
    "en": 'use "{new}" instead of "{old}"',
    "zh-CN": '请把 "{old}" 改成 "{new}"',
    "zh-TW": '請把 "{old}" 改成 "{new}"',
    "ja": '"{old}" の代わりに "{new}" を使ってください',
    "ko": '"{old}" 대신 "{new}"를 사용하세요',
    "ru": 'используйте "{new}" вместо "{old}"',
    "es": 'usa "{new}" en lugar de "{old}"',
    "pt": 'use "{new}" em vez de "{old}"',
}


# [plugin.models.*] entries the server's model schema rejects; {label} is the
# plugin.toml path and {detail} the schema's own (English) reason.
_MODELS_REJECTED = {
    "en": "{label} is rejected by the plugin model schema: {detail}",
    "zh-CN": "{label} 不符合插件模型声明的格式，服务端会拒绝：{detail}",
    "zh-TW": "{label} 不符合外掛模型宣告的格式，伺服器端會拒絕：{detail}",
    "ja": "{label} はプラグインのモデル宣言の形式に合わず、サーバーに拒否されます：{detail}",
    "ko": "{label}은(는) 플러그인 모델 선언 형식에 맞지 않아 서버에서 거부됩니다: {detail}",
    "ru": "{label} не соответствует схеме объявления моделей плагина и будет отклонено сервером: {detail}",
    "es": "{label} no cumple el esquema de declaración de modelos del plugin y el servidor lo rechazará: {detail}",
    "pt": "{label} não segue o esquema de declaração de modelos do plugin e será rejeitado pelo servidor: {detail}",
}
_PLUGIN_MODELS_ADAPTER = TypeAdapter(dict[PluginModelUsageId, PluginModelRequirementSchema])


def _format_multilingual(texts: dict[str, str], *, head: str | None = None) -> str:
    """One message in all locales.

    Without ``head`` the English text is the main line and the other locales follow
    on indented lines. With ``head`` (an existing English message) that is the main
    line and every locale, English included, follows it.
    """
    lines = [texts["en"] if head is None else head]
    lines.extend(
        f"      {locale}: {texts[locale]}" for locale in _MESSAGE_LOCALES if head is not None or locale != "en"
    )
    return "\n".join(lines)


# Old persist_mode spellings check used to accept; the host never applied them.
_PLUGIN_STATE_PERSIST_MODE_MIGRATIONS = {"always": "auto", "disabled": "off"}


def validate_plugin_dir(
    plugin_dir: Path,
    *,
    strict: bool = False,
    require_matching_directory_name: bool = False,
) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    config_example_path = plugin_dir / "config.example.toml"
    config_example: dict[str, object] | None = None
    if config_example_path.is_file():
        try:
            config_example = load_toml(config_example_path)
        except Exception as exc:
            issues.append(
                (
                    "error",
                    "config.example.toml could not be read / "
                    "无法读取 config.example.toml / "
                    f"config.example.toml を読み取れません: {exc}",
                )
            )
            return issues
    try:
        source = load_plugin_source(plugin_dir)
    except Exception:
        plugin_toml_path = plugin_dir / "plugin.toml"
        try:
            config = load_toml(plugin_toml_path)
        except Exception as exc:
            issues.append(("error", f"plugin.toml could not be read: {exc}"))
            return issues
        plugin_table = config.get("plugin") if isinstance(config, dict) else {}
        plugin_id = plugin_table.get("id") if isinstance(plugin_table, dict) else plugin_dir.name
        plugin_id = plugin_id if isinstance(plugin_id, str) and plugin_id.strip() else plugin_dir.name
        _check_plugin_toml_schema(plugin_dir, config, plugin_id.strip(), issues)
        return issues
    plugin_table = source.plugin_table
    _check_plugin_toml_schema(plugin_dir, source.plugin_toml, source.plugin_id, issues)
    if require_matching_directory_name and not plugin_package_matches_directory(
        source.plugin_id, plugin_dir.name
    ):
        issues.append(
            (
                "warning",
                f"plugin.id '{source.plugin_id}' does not match directory name '{plugin_dir.name}' / "
                f"plugin.id '{source.plugin_id}' 与目录名 '{plugin_dir.name}' 不一致 / "
                f"plugin.id '{source.plugin_id}' がディレクトリ名 '{plugin_dir.name}' と一致しません",
            )
        )
    if config_example is not None:
        _check_config_example_schema(config_example, issues)
    elif "plugin_runtime" in source.plugin_toml or source.plugin_id in source.plugin_toml:
        issues.append(
            (
                "warning",
                "legacy runtime configuration in plugin.toml is supported; "
                "move mutable defaults to config.example.toml / "
                "仍兼容 plugin.toml 中的旧运行配置；请将可变默认值移到 config.example.toml / "
                "plugin.toml の従来の実行設定は互換対応です。可変の既定値を config.example.toml に移してください",
            )
        )

    entry = source.entry_point
    if not entry:
        issues.append(("error", "plugin.entry is missing"))
    else:
        expected_module = f"plugin.plugins.{source.plugin_id}"
        entry_module = entry.split(":", 1)[0].strip()
        if entry_module != expected_module and not entry_module.startswith(expected_module + "."):
            issues.append((
                "warning",
                f"plugin.entry should usually target '{expected_module}', got '{entry}'",
            ))
        _check_entry_target(plugin_dir, source.plugin_id, entry, source.package_type, issues)

    if not plugin_table.get("sdk"):
        issues.append(("warning", "[plugin.sdk] is missing"))

    _check_optional_file(plugin_dir / "README.md", "README.md", issues, strict=strict)
    _check_optional_file(plugin_dir / "tests" / "test_smoke.py", "tests/test_smoke.py", issues, strict=strict)
    _check_optional_file(plugin_dir / "pyproject.toml", "pyproject.toml", issues, strict=False)
    _check_requirements_file(plugin_dir / "requirements.txt", issues)
    _check_pyproject_dependency_layout(plugin_dir, source.pyproject_toml, source.package_type, issues)

    _check_json_file(plugin_dir / ".vscode" / "settings.json", ".vscode/settings.json", issues, strict=strict)
    _check_json_file(plugin_dir / ".vscode" / "tasks.json", ".vscode/tasks.json", issues, strict=strict)
    _check_optional_file(plugin_dir / ".github" / "workflows" / "verify.yml", ".github/workflows/verify.yml", issues, strict=strict)

    _check_gitignore(plugin_dir / ".gitignore", issues, strict=strict)
    _check_python_decorators(plugin_dir, issues)
    _check_symlink_tree(plugin_dir, issues)

    return issues


def _check_config_example_schema(
    config: dict[str, object],
    issues: list[tuple[str, str]],
) -> None:
    if "plugin" in config:
        issues.append(
            (
                "error",
                "config.example.toml must not contain [plugin] / "
                "config.example.toml 不能包含 [plugin] / "
                "config.example.toml に [plugin] を含めることはできません",
            )
        )
    _check_runtime_table(config.get("plugin_runtime"), issues)


def _check_plugin_toml_schema(
    plugin_dir: Path,
    config: dict[str, object],
    plugin_id: str,
    issues: list[tuple[str, str]],
) -> None:
    plugin_table = config.get("plugin")
    if not isinstance(plugin_table, dict):
        issues.append(("error", "[plugin] must be a table"))
        return

    allowed_plugin_keys = {
        "id",
        "name",
        "type",
        "description",
        "short_description",
        "keywords",
        "passive",
        "version",
        "entry",
        "entries",
        "author",
        "i18n",
        "sdk",
        "ui",
        "store",
        "host",
        "install",
        "safety",
        "config_profiles",
        "database",
        "models",
        "dependency",
        "dependencies",
        "previous_ids",
        "shared",
    }
    _warn_unknown_keys(plugin_table, allowed_plugin_keys, "[plugin]", issues)

    _require_string(plugin_table, "id", "[plugin].id", issues, pattern=r"^[A-Za-z0-9_-]+$")
    _require_string(plugin_table, "name", "[plugin].name", issues)
    _require_string(plugin_table, "version", "[plugin].version", issues, pattern=r"^\d+\.\d+\.\d+.*$")
    _require_string(plugin_table, "entry", "[plugin].entry", issues, pattern=r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")

    plugin_type = plugin_table.get("type", "plugin")
    if not isinstance(plugin_type, str) or plugin_type not in SUPPORTED_PLUGIN_TYPES:
        issues.append(("error", format_plugin_type_choice_error("[plugin].type")))
    if "host" in plugin_table:
        issues.append(("error", format_removed_plugin_host()))

    _check_optional_string(plugin_table, "description", "[plugin].description", issues)
    _check_optional_string(plugin_table, "short_description", "[plugin].short_description", issues)
    _check_optional_bool(plugin_table, "passive", "[plugin].passive", issues)
    _check_string_list(plugin_table.get("keywords"), "[plugin].keywords", issues, required=False)
    _check_plugin_dependency_id_list(plugin_table.get("dependencies"), "[plugin].dependencies", plugin_id, issues)
    _check_previous_plugin_ids(plugin_table.get("previous_ids"), plugin_id, issues)

    _check_author_table(plugin_table.get("author"), issues)
    _check_sdk_table(plugin_table.get("sdk"), issues)
    _check_store_table(plugin_table.get("store"), issues)
    _check_database_table(plugin_table.get("database"), issues)
    _check_models_table(plugin_table.get("models"), issues)
    _check_shared_table(plugin_table.get("shared"), issues)
    _check_i18n_table(plugin_dir, plugin_table.get("i18n"), issues)
    _check_install_table(plugin_dir, plugin_table.get("install"), issues)
    _check_safety_table(plugin_table.get("safety"), issues)
    _check_config_profiles_table(plugin_table.get("config_profiles"), issues)
    _check_dependency_tables(plugin_table.get("dependency"), issues)
    _check_entries(plugin_table.get("entries"), issues)
    _check_ui_table(plugin_dir, plugin_table.get("ui"), issues)
    _check_runtime_table(config.get("plugin_runtime"), issues)
    _check_plugin_state_table(config.get("plugin_state"), issues)

    adapter_table = config.get("adapter")
    if adapter_table is not None:
        if not isinstance(adapter_table, dict):
            issues.append(("error", "[adapter] must be a table"))
        else:
            _check_enum(adapter_table.get("mode", "gateway"), "[adapter].mode", {"gateway"}, issues)
            _check_optional_number(adapter_table, "priority", "[adapter].priority", issues, integer=True)


def _warn_unknown_keys(table: dict[str, object], allowed: set[str], label: str, issues: list[tuple[str, str]]) -> None:
    for key in sorted(set(table) - allowed):
        issues.append(("warning", f"{label}.{key} is not a recognized plugin.toml field"))


def _error_unknown_keys(table: dict[str, object], allowed: set[str], label: str, issues: list[tuple[str, str]]) -> None:
    for key in sorted(set(table) - allowed):
        issues.append(("error", f"{label}.{key} is not a recognized plugin.toml field"))


def _require_string(
    table: dict[str, object],
    key: str,
    label: str,
    issues: list[tuple[str, str]],
    *,
    pattern: str | None = None,
) -> None:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(("error", f"{label} must be a non-empty string"))
        return
    if value.strip() != value:
        issues.append(("warning", f"{label} contains leading/trailing whitespace"))
    if pattern and not re.fullmatch(pattern, value.strip()):
        issues.append(("error", f"{label} has invalid format: {value}"))


def _check_optional_string(table: dict[str, object], key: str, label: str, issues: list[tuple[str, str]]) -> None:
    value = table.get(key)
    if value is not None and not isinstance(value, str):
        issues.append(("error", f"{label} must be a string"))


def _check_optional_i18n_string(table: dict[str, object], key: str, label: str, issues: list[tuple[str, str]]) -> None:
    value = table.get(key)
    if value is None or isinstance(value, str):
        return
    if not isinstance(value, dict):
        issues.append(("error", f"{label} must be a string or $i18n table"))
        return
    _warn_unknown_keys(value, {"$i18n", "default"}, label, issues)
    ref = value.get("$i18n")
    if not isinstance(ref, str) or not ref.strip():
        issues.append(("error", f"{label}.$i18n must be a non-empty string"))
    default = value.get("default")
    if default is not None and not isinstance(default, str):
        issues.append(("error", f"{label}.default must be a string"))


def _check_optional_bool(table: dict[str, object], key: str, label: str, issues: list[tuple[str, str]]) -> None:
    value = table.get(key)
    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        issues.append(("warning", f"{label} uses string boolean '{value}'; prefer true/false"))
        return
    issues.append(("error", f"{label} must be a boolean"))


def _check_enum(value: object, label: str, allowed: set[str], issues: list[tuple[str, str]]) -> None:
    if not isinstance(value, str) or value not in allowed:
        issues.append(("error", f"{label} must be one of: {', '.join(sorted(allowed))}"))


def _check_string_list(value: object, label: str, issues: list[tuple[str, str]], *, required: bool) -> None:
    if value is None:
        if required:
            issues.append(("error", f"{label} is required"))
        return
    if not isinstance(value, list):
        issues.append(("error", f"{label} must be a list of strings"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(("error", f"{label}[{index}] must be a non-empty string"))


def _check_plugin_dependency_id_list(
    value: object,
    label: str,
    plugin_id: str,
    issues: list[tuple[str, str]],
) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(("error", f"{label} must be a list of plugin id strings"))
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(("error", f"{label}[{index}] must be a non-empty plugin id"))
            continue
        dependency_id = item.strip()
        if not re.fullmatch(r"^[A-Za-z0-9_-]+$", dependency_id):
            issues.append((
                "error",
                f"{label}[{index}] must be a plugin id, got '{dependency_id}'. "
                "Python packages belong in pyproject.toml [project].dependencies.",
            ))
            continue
        if dependency_id == plugin_id:
            issues.append(("error", f"{label}[{index}] must not reference the plugin itself"))


def _check_previous_plugin_ids(
    value: object,
    plugin_id: str,
    issues: list[tuple[str, str]],
) -> None:
    label = "[plugin].previous_ids"
    if value is None:
        return
    if not isinstance(value, list):
        issues.append(("error", f"{label} must be a list of plugin id strings"))
        return

    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(("error", f"{label}[{index}] must be a non-empty plugin id"))
            continue
        previous_id = item.strip()
        if not re.fullmatch(r"^[A-Za-z0-9_-]+$", previous_id):
            issues.append(("error", f"{label}[{index}] must be a plugin id, got '{previous_id}'"))
        elif previous_id == plugin_id:
            issues.append(("error", f"{label}[{index}] must not equal [plugin].id"))
        elif previous_id in seen:
            issues.append(("error", f"{label}[{index}] duplicates '{previous_id}'"))
        seen.add(previous_id)


def _check_optional_number(
    table: dict[str, object],
    key: str,
    label: str,
    issues: list[tuple[str, str]],
    *,
    integer: bool = False,
    minimum: float | None = None,
) -> None:
    value = table.get(key)
    if value is None:
        return
    valid_type = isinstance(value, int) if integer else isinstance(value, (int, float))
    if isinstance(value, bool) or not valid_type:
        issues.append(("error", f"{label} must be an {'integer' if integer else 'number'}"))
        return
    if minimum is not None and value < minimum:
        issues.append(("error", f"{label} must be >= {minimum:g}"))


def _check_author_table(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.author] must be a table"))
        return
    _warn_unknown_keys(value, {"name", "email", "url"}, "[plugin.author]", issues)
    for key in ("name", "email", "url"):
        _check_optional_string(value, key, f"[plugin.author].{key}", issues)


def _check_sdk_table(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.sdk] must be a table"))
        return
    _warn_unknown_keys(value, {"recommended", "supported", "untested", "conflicts"}, "[plugin.sdk]", issues)
    for key in ("recommended", "supported", "untested"):
        _check_optional_string(value, key, f"[plugin.sdk].{key}", issues)
    conflicts = value.get("conflicts")
    if conflicts is not None:
        _check_string_list(conflicts, "[plugin.sdk].conflicts", issues, required=False)


def _check_store_table(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.store] must be a table"))
        return
    _warn_unknown_keys(value, {"enabled", "backend"}, "[plugin.store]", issues)
    # The SDK applies bool() (resolve_store_enabled), so enabled = "false" would turn the store on.
    _check_entry_bool(value.get("enabled"), "[plugin.store].enabled", issues, reader="the SDK")
    if "backend" in value:
        # Still a known key so existing plugin.toml files keep passing, but the SDK
        # only reads [plugin.store].enabled (resolve_store_enabled); backend is never read.
        issues.append(("warning", _format_multilingual(_STORE_BACKEND_IGNORED)))


def _check_database_table(value: object, issues: list[tuple[str, str]]) -> None:
    """Check ``[plugin.database]``: the keys the SDK reads to open the plugin database."""
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.database] must be a table"))
        return
    _warn_unknown_keys(value, {"enabled", "name"}, "[plugin.database]", issues)
    # The SDK applies bool() (resolve_db_config), so enabled = "false" would turn the database on.
    _check_entry_bool(value.get("enabled"), "[plugin.database].enabled", issues, reader="the SDK")
    name = value.get("name")
    if name is None:
        return
    if not isinstance(name, str):
        # The SDK would str() it, but a non-string file name in TOML is almost always a typo.
        issues.append(("error", "[plugin.database].name must be a string"))
        return
    try:
        PluginDatabase._normalize_db_name(name)
        # The SDK checks with the host's own Path; a name like "C:notes.db" or "notes?.db"
        # passes on Linux/macOS but cannot be created on Windows, so check that too.
        valid = not name or (
            PureWindowsPath(name).name == name
            and not _WINDOWS_FORBIDDEN_FILENAME_CHARS.intersection(name)
            and name.split(".", 1)[0].strip().upper() not in _WINDOWS_RESERVED_FILENAMES
            # Windows drops a trailing dot or space, so the file would not match the name.
            and name[-1] not in ". "
        )
    except ValueError:
        valid = False
    if not valid:
        issues.append(("error", _format_multilingual(_DATABASE_NAME_INVALID)))


def _models_error_label(loc: tuple[object, ...]) -> str:
    if not loc:
        return "[plugin.models]"
    usage_id = str(loc[0])
    # Quote keys TOML would not accept bare, so "a.b" does not read as a nested table.
    key = usage_id if re.fullmatch(r"[A-Za-z0-9_-]+", usage_id) else json.dumps(usage_id, ensure_ascii=False)
    label = f"[plugin.models.{key}]"
    if loc[1:] == ("[key]",):
        return f"{label} (usage id)"
    for part in loc[1:]:
        label += f"[{part}]" if isinstance(part, int) else f".{part}"
    return label


def _check_models_table(value: object, issues: list[tuple[str, str]]) -> None:
    """Check ``[plugin.models.*]`` with the same schema the server reads model requirements with."""
    if value is None:
        return
    try:
        _PLUGIN_MODELS_ADAPTER.validate_python(value)
    except ValidationError as exc:
        for error in exc.errors(include_url=False, include_input=False):
            label = _models_error_label(tuple(error.get("loc", ())))
            detail = str(error.get("msg", ""))
            texts = {locale: text.format(label=label, detail=detail) for locale, text in _MODELS_REJECTED.items()}
            issues.append(("error", _format_multilingual(texts)))


def _check_shared_table(value: object, issues: list[tuple[str, str]]) -> None:
    """Check ``[plugin.shared]``: the plugins whose shared files this one reads."""
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.shared] must be a table"))
        return
    _warn_unknown_keys(value, {"reads"}, "[plugin.shared]", issues)
    reads = value.get("reads")
    if isinstance(reads, str):
        # One id may be written without the brackets; the plugin is run with it read so.
        reads = [reads]
    _check_string_list(reads, "[plugin.shared].reads", issues, required=False)


def _check_i18n_table(plugin_dir: Path, value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.i18n] must be a table"))
        return
    _warn_unknown_keys(value, {"default_locale", "locales_dir"}, "[plugin.i18n]", issues)
    _check_optional_string(value, "default_locale", "[plugin.i18n].default_locale", issues)
    locales_dir = value.get("locales_dir")
    if locales_dir is not None:
        if not isinstance(locales_dir, str) or not locales_dir.strip():
            issues.append(("error", "[plugin.i18n].locales_dir must be a non-empty string"))
        elif not (plugin_dir / locales_dir).is_dir():
            issues.append(("warning", f"[plugin.i18n].locales_dir does not exist: {locales_dir}"))


def _check_install_table(
    plugin_dir: Path,
    value: object,
    issues: list[tuple[str, str]],
) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.install] must be a table"))
        return

    _error_unknown_keys(
        value,
        {"enabled", "ui_i18n_dir", "tutorial_enabled", "kinds"},
        "[plugin.install]",
        issues,
    )
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        issues.append(("error", "[plugin.install].enabled must be a boolean"))

    tutorial_enabled = value.get("tutorial_enabled", False)
    if not isinstance(tutorial_enabled, bool):
        issues.append(("error", "[plugin.install].tutorial_enabled must be a boolean"))

    kinds = value.get("kinds", {})
    if not isinstance(kinds, dict):
        issues.append(("error", "[plugin.install].kinds must be a table"))
        kinds = {}
    else:
        for kind, declaration in kinds.items():
            label = f"[plugin.install.kinds.{kind}]"
            if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", kind):
                issues.append(
                    (
                        "error",
                        f"[plugin.install].kinds key {kind!r} must match ^[a-z][a-z0-9_]*$",
                    )
                )
            if not isinstance(declaration, dict):
                issues.append(("error", f"{label} must be a table"))
                continue
            _error_unknown_keys(
                declaration,
                {"entry_id", "label", "queued_message", "entry_timeout"},
                label,
                issues,
            )
            for field in ("entry_id", "label", "queued_message"):
                field_value = declaration.get(field)
                field_label = f"{label}.{field}"
                if not isinstance(field_value, str) or not field_value:
                    issues.append(("error", f"{field_label} must be a non-empty string"))
                elif field_value.strip() != field_value:
                    issues.append(
                        ("error", f"{field_label} must not contain leading/trailing whitespace")
                    )
            timeout = declaration.get("entry_timeout")
            timeout_is_valid = not isinstance(timeout, bool) and isinstance(
                timeout,
                (int, float),
            )
            if timeout_is_valid:
                try:
                    timeout_number = float(timeout)
                except (OverflowError, ValueError):
                    timeout_is_valid = False
                else:
                    timeout_is_valid = math.isfinite(timeout_number) and timeout_number > 0
            if not timeout_is_valid:
                issues.append(
                    (
                        "error",
                        f"{label}.entry_timeout must be a finite number greater than zero",
                    )
                )

    ui_i18n_dir = value.get("ui_i18n_dir")
    if ui_i18n_dir is not None:
        _check_install_i18n_dir(plugin_dir, ui_i18n_dir, issues)

    if enabled is False:
        if kinds:
            issues.append(("error", "disabled [plugin.install] must not define install kinds"))
        if tutorial_enabled is True:
            issues.append(("error", "disabled [plugin.install] must not enable tutorials"))
        if ui_i18n_dir is not None:
            issues.append(("error", "disabled [plugin.install] must not define ui_i18n_dir"))


def _check_install_i18n_dir(
    plugin_dir: Path,
    value: object,
    issues: list[tuple[str, str]],
) -> None:
    label = "[plugin.install].ui_i18n_dir"
    if not isinstance(value, str) or not value or value.strip() != value:
        issues.append(("error", f"{label} must be a non-empty relative path"))
        return

    relative = Path(value)
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        issues.append(("error", f"{label} must stay within the plugin directory"))
        return

    try:
        plugin_root = plugin_dir.resolve(strict=True)
        candidate = (plugin_root / relative).resolve(strict=True)
        candidate.relative_to(plugin_root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        issues.append(
            (
                "error",
                f"{label} must resolve to an existing directory inside the plugin directory",
            )
        )
        return

    current = plugin_root
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError:
            break
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if current.is_symlink() or bool(file_attributes & reparse_attribute):
            try:
                current.resolve(strict=True).relative_to(plugin_root)
            except (FileNotFoundError, OSError, RuntimeError, ValueError):
                issues.append(
                    ("error", f"{label} must not escape through a link or reparse point")
                )
                return

    if not candidate.is_dir():
        issues.append(("error", f"{label} must point to an existing directory"))


def _check_safety_table(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.safety] must be a table"))
        return
    _warn_unknown_keys(value, {"sync_call_in_handler"}, "[plugin.safety]", issues)
    setting = value.get("sync_call_in_handler")
    if setting is not None:
        _check_enum(setting, "[plugin.safety].sync_call_in_handler", {"warn", "reject"}, issues)


def _check_config_profiles_table(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.config_profiles] must be a table"))
        return
    _warn_unknown_keys(value, {"active", "files"}, "[plugin.config_profiles]", issues)
    _check_optional_string(value, "active", "[plugin.config_profiles].active", issues)
    files = value.get("files")
    if files is not None:
        if not isinstance(files, dict):
            issues.append(("error", "[plugin.config_profiles].files must be a table"))
        else:
            for key, item in files.items():
                if not isinstance(key, str) or not isinstance(item, str) or not item.strip():
                    issues.append(("error", "[plugin.config_profiles].files must map profile names to file paths"))


def _check_dependency_tables(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    deps = value if isinstance(value, list) else [value] if isinstance(value, dict) else None
    if deps is None:
        issues.append(("error", "[[plugin.dependency]] must be an array of tables"))
        return
    for index, dep in enumerate(deps):
        label = f"[[plugin.dependency]][{index}]"
        if not isinstance(dep, dict):
            issues.append(("error", f"{label} must be a table"))
            continue
        _warn_unknown_keys(dep, {"id", "entry", "custom_event", "providers", "recommended", "supported", "untested", "conflicts"}, label, issues)
        selectors = [name for name in ("id", "entry", "custom_event", "providers") if dep.get(name)]
        if not selectors:
            issues.append(("error", f"{label} must declare at least one of id, entry, custom_event, providers"))
        if dep.get("entry") and dep.get("custom_event"):
            issues.append(("error", f"{label} cannot declare both entry and custom_event"))
        if dep.get("conflicts") is True and not dep.get("id"):
            issues.append(("error", f"{label} with conflicts=true requires id"))
        if dep.get("conflicts") is not True and "untested" not in dep:
            issues.append(("error", f"{label} requires untested unless conflicts=true"))
        for key in ("id", "entry", "custom_event", "recommended", "supported", "untested"):
            _check_optional_string(dep, key, f"{label}.{key}", issues)
        providers = dep.get("providers")
        if providers is not None:
            _check_string_list(providers, f"{label}.providers", issues, required=False)
        conflicts = dep.get("conflicts")
        if conflicts is not None and conflicts is not True:
            if isinstance(conflicts, str):
                continue
            _check_string_list(conflicts, f"{label}.conflicts", issues, required=False)


def _check_entries(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    # Bound iteration, unknown-key sorting and diagnostic size before scanning.
    if isinstance(value, (list, dict)):
        if len(value) > _PLUGIN_ENTRIES_MAX:
            issues.append((
                "error",
                f"[plugin].entries exceeds {_PLUGIN_ENTRIES_MAX} entries / "
                f"[plugin].entries 超过 {_PLUGIN_ENTRIES_MAX} 个条目 / "
                f"[plugin].entries が {_PLUGIN_ENTRIES_MAX} 件を超えています",
            ))
            return
        if isinstance(value, dict) and any(len(key) > _PLUGIN_ENTRY_KEY_LENGTH_MAX for key in value):
            issues.append((
                "error",
                f"[plugin].entries keys must not exceed {_PLUGIN_ENTRY_KEY_LENGTH_MAX} characters / "
                f"[plugin].entries 键名不能超过 {_PLUGIN_ENTRY_KEY_LENGTH_MAX} 个字符 / "
                f"[plugin].entries のキーは {_PLUGIN_ENTRY_KEY_LENGTH_MAX} 文字以内にしてください",
            ))
            return
        for item in value.values() if isinstance(value, dict) else value:
            if not isinstance(item, dict):
                continue
            if len(item) > _PLUGIN_ENTRY_FIELDS_MAX:
                issues.append((
                    "error",
                    f"[plugin].entries tables must not exceed {_PLUGIN_ENTRY_FIELDS_MAX} fields / "
                    f"[plugin].entries 表不能超过 {_PLUGIN_ENTRY_FIELDS_MAX} 个字段 / "
                    f"[plugin].entries のテーブルは {_PLUGIN_ENTRY_FIELDS_MAX} フィールド以内にしてください",
                ))
                return
            if any(len(key) > _PLUGIN_ENTRY_KEY_LENGTH_MAX for key in item):
                issues.append((
                    "error",
                    f"[plugin].entries field names must not exceed {_PLUGIN_ENTRY_KEY_LENGTH_MAX} characters / "
                    f"[plugin].entries 字段名不能超过 {_PLUGIN_ENTRY_KEY_LENGTH_MAX} 个字符 / "
                    f"[plugin].entries のフィールド名は {_PLUGIN_ENTRY_KEY_LENGTH_MAX} 文字以内にしてください",
                ))
                return
    # id plus the display fields registry copies, union the shared contract tuple.
    allowed = {"id", "name", "description", "input_schema"} | set(ENTRY_CONTRACT_FIELDS)
    if isinstance(value, list):
        for index, item in enumerate(value):
            label = f"[plugin].entries[{index}]"
            # The registry also takes a bare string item as the entry id.
            if isinstance(item, str):
                if not item.strip():
                    issues.append((
                        "warning",
                        f"{label} is a blank entry id / "
                        f"{label} 是空的入口 id / {label} のエントリ id が空です",
                    ))
                continue
            if not isinstance(item, dict):
                issues.append((
                    "error",
                    f"{label} must be a table or entry id string / "
                    f"{label} 必须是表或入口 id 字符串 / "
                    f"{label} はテーブルまたはエントリ id 文字列である必要があります",
                ))
                continue
            _warn_unknown_keys(item, allowed, label, issues)
            _check_entry_field_types(item, label, issues)
            # The registry skips a list item whose id is missing or blank.
            # Truthiness avoids recursively stringifying a structured TOML value.
            entry_id = item.get("id")
            if not entry_id or (isinstance(entry_id, str) and not entry_id.strip()):
                issues.append((
                    "warning",
                    f"{label}.id is missing or blank / "
                    f"{label}.id 缺失或为空 / {label}.id が未指定または空です",
                ))
        return
    if isinstance(value, dict):
        # The registry iterates a keyed table by key, so the declared fields
        # never reach the entry. Flag it until the runtime normalizes it.
        if value:
            issues.append((
                "warning",
                "[plugin].entries as a keyed table is not applied at runtime; "
                "its declared fields are ignored. Use [[plugin.entries]] with id = \"...\" instead / "
                "[plugin].entries 的键控表形式在运行时不会生效，其声明字段会被忽略，"
                "请改用 [[plugin.entries]] 并填写 id / "
                "[plugin].entries のキー付きテーブル形式は実行時に反映されず、宣言したフィールドは無視されます。"
                "[[plugin.entries]] と id を使用してください",
            ))
        for entry_id, item in value.items():
            label = f"[plugin].entries.{entry_id}"
            if not isinstance(item, dict):
                issues.append((
                    "error",
                    f"{label} must be a table / {label} 必须是表 / {label} はテーブルである必要があります",
                ))
                continue
            _warn_unknown_keys(item, allowed, label, issues)
            _check_entry_field_types(item, label, issues, applied=False)
        return
    issues.append((
        "error",
        "[plugin].entries must be a table or array of tables / "
        "[plugin].entries 必须是表或表数组 / "
        "[plugin].entries はテーブルまたはテーブルの配列である必要があります",
    ))


def _check_entry_field_types(
    item: dict[str, object],
    label: str,
    issues: list[tuple[str, str]],
    *,
    applied: bool = True,
) -> None:
    for key in _PLUGIN_ENTRY_BOOL_FIELDS:
        _check_entry_bool(item.get(key), f"{label}.{key}", issues)
    _check_entry_timeout(item.get("timeout"), f"{label}.timeout", issues)
    for key in ENTRY_CONTRACT_MAPPING_FIELDS:
        if key in item and not isinstance(item[key], dict):
            # Warning, not error: these fields were never checked before.
            if applied:
                # The registry coerces a non-table to an empty mapping, silently.
                message = (
                    f"{label}.{key} must be a table; the registry replaces any other value with an empty table / "
                    f"{label}.{key} 必须是表，其他值会被注册表替换为空表 / "
                    f"{label}.{key} はテーブルである必要があります。それ以外の値は空のテーブルに置き換えられます"
                )
            else:
                message = (
                    f"{label}.{key} must be a table / {label}.{key} 必须是表 / "
                    f"{label}.{key} はテーブルである必要があります"
                )
            issues.append(("warning", message))
    # A keyed table is not applied at all (warned separately): no publish hint.
    for key in ("output_schema", "strict_output") if applied else ():
        if key in item:
            # The registry drops it: the output check runs from the decorator.
            issues.append((
                "warning",
                f"{label}.{key} is ignored; declare it on @plugin_entry instead / "
                f"{label}.{key} 会被忽略，请在 @plugin_entry 上声明 / "
                f"{label}.{key} は無視されます。@plugin_entry で宣言してください",
            ))


def _check_entry_bool(
    value: object,
    label: str,
    issues: list[tuple[str, str]],
    *,
    reader: str = "the registry",
) -> None:
    # Unlike _check_optional_bool, no string spelling is safe here: the reader
    # applies bool(), so "false", "no", "0" and "off" all read as True.
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        issues.append((
            "error",
            f"{label} must be true/false, not a string; {reader} reads any non-empty string as true",
        ))
        return
    issues.append(("error", f"{label} must be a boolean"))


def _check_entry_timeout(value: object, label: str, issues: list[tuple[str, str]]) -> None:
    # Mirrors resolve_entry_timeout(): it accepts anything float() reads, treats
    # <= 0 as "no per-entry timeout", and silently falls back on anything else.
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        issues.append(("error", f"{label} must be a number"))
        return
    try:
        number = float(value)
    except (OverflowError, ValueError):
        issues.append(("error", f"{label} must be a number"))
        return
    if not math.isfinite(number):
        issues.append(("error", f"{label} must be finite"))
        return
    if isinstance(value, str):
        issues.append(("warning", f"{label} uses string number '{value}'; prefer a number"))
    if number < 0:
        issues.append(("warning", f"{label} is negative, which disables the entry timeout; use 0 for that"))


def _check_ui_table(plugin_dir: Path, value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin.ui] must be a table"))
        return
    _warn_unknown_keys(
        value,
        {"enabled", "expose_legacy_static_panel", "panel", "guide", "docs", "warnings"},
        "[plugin.ui]",
        issues,
    )
    _check_optional_bool(value, "enabled", "[plugin.ui].enabled", issues)
    _check_optional_bool(
        value,
        "expose_legacy_static_panel",
        "[plugin.ui].expose_legacy_static_panel",
        issues,
    )
    for kind in ("panel", "guide", "docs"):
        raw = value.get(kind)
        if raw is None:
            continue
        items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else None
        if items is None:
            issues.append(("error", f"[plugin.ui].{kind} must be a table or array of tables"))
            continue
        for index, surface in enumerate(items):
            _check_ui_surface(plugin_dir, surface, f"[plugin.ui].{kind}[{index}]", issues)


def _check_ui_surface(plugin_dir: Path, value: object, label: str, issues: list[tuple[str, str]]) -> None:
    if not isinstance(value, dict):
        issues.append(("error", f"{label} must be a table"))
        return
    _warn_unknown_keys(value, {"id", "title", "entry", "mode", "url", "ui_path", "open_in", "context", "permissions", "available"}, label, issues)
    _check_optional_string(value, "id", f"{label}.id", issues)
    _check_optional_i18n_string(value, "title", f"{label}.title", issues)
    entry = value.get("entry")
    url = value.get("url")
    if entry and url:
        issues.append(("error", f"{label} cannot declare both entry and url"))
    if entry is not None:
        if not isinstance(entry, str) or not entry.strip():
            issues.append(("error", f"{label}.entry must be a non-empty string"))
        elif not (plugin_dir / entry).exists():
            issues.append(("warning", f"{label}.entry path does not exist: {entry}"))
    _check_optional_string(value, "url", f"{label}.url", issues)
    mode = value.get("mode")
    if mode is not None:
        _check_enum(mode, f"{label}.mode", {"static", "hosted-tsx", "markdown", "auto"}, issues)
    open_in = value.get("open_in")
    if open_in is not None:
        _check_enum(open_in, f"{label}.open_in", {"iframe", "new_tab", "same_tab"}, issues)
    context = value.get("context")
    if context is not None and (not isinstance(context, str) or not context.strip()):
        # context is the plugin-defined @ui.context provider id resolved via
        # host.get_ui_context(), not a placement enum
        issues.append(("error", f"{label}.context must be a non-empty string"))
    permissions = value.get("permissions")
    if permissions is not None:
        _check_string_list(permissions, f"{label}.permissions", issues, required=False)
        if isinstance(permissions, list):
            allowed = {"state:read", "config:read", "config:write", "action:call", "logs:read", "runs:read"}
            for index, permission in enumerate(permissions):
                if isinstance(permission, str) and permission not in allowed:
                    issues.append(("warning", f"{label}.permissions[{index}] is not a recognized permission: {permission}"))
    _check_optional_bool(value, "available", f"{label}.available", issues)


def _check_runtime_table(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin_runtime] must be a table"))
        return
    _warn_unknown_keys(value, {"enabled", "auto_start", "priority", "timeout", "startup_failure"}, "[plugin_runtime]", issues)
    _check_optional_bool(value, "enabled", "[plugin_runtime].enabled", issues)
    _check_optional_bool(value, "auto_start", "[plugin_runtime].auto_start", issues)
    _check_optional_number(value, "priority", "[plugin_runtime].priority", issues, integer=True)
    _check_optional_number(value, "timeout", "[plugin_runtime].timeout", issues)
    timeout = value.get("timeout")
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
        if not math.isfinite(float(timeout)):
            issues.append(("error", "[plugin_runtime].timeout must be finite"))
        elif timeout <= 0:
            issues.append(("error", "[plugin_runtime].timeout must be > 0"))
        elif timeout > _PLUGIN_RUNTIME_TIMEOUT_MAX:
            issues.append(("error", "[plugin_runtime].timeout must be <= 300"))
    startup_failure = value.get("startup_failure")
    if startup_failure is not None:
        _check_enum(startup_failure, "[plugin_runtime].startup_failure", {"warn", "fail", "ignore"}, issues)


def _check_plugin_state_table(value: object, issues: list[tuple[str, str]]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", "[plugin_state] must be a table"))
        return
    _warn_unknown_keys(value, {"backend", "persist_mode"}, "[plugin_state]", issues)
    backend = value.get("backend")
    if backend is not None:
        # Ask the SDK itself: it treats "" as "off" and ignores case.
        try:
            accepted = isinstance(backend, str) and bool(PluginStatePersistence.normalize_backend(backend))
        except InvalidArgumentError:
            accepted = False
        if not accepted:
            _check_enum(backend, "[plugin_state].backend", set(PluginStatePersistence.VALID_BACKENDS), issues)
    persist_mode = value.get("persist_mode")
    if persist_mode is not None and persist_mode not in PluginStatePersistence.VALID_PERSIST_MODES:
        message = (
            "[plugin_state].persist_mode must be one of: "
            f"{', '.join(sorted(PluginStatePersistence.VALID_PERSIST_MODES))}"
        )
        replacement = _PLUGIN_STATE_PERSIST_MODE_MIGRATIONS.get(persist_mode) if isinstance(persist_mode, str) else None
        if replacement is not None:
            hint = {locale: text.format(new=replacement, old=persist_mode) for locale, text in _PERSIST_MODE_MIGRATION_HINT.items()}
            message = _format_multilingual(hint, head=message)
        issues.append(("error", message))


def _check_entry_target(
    plugin_dir: Path,
    plugin_id: str,
    entry: str,
    package_type: str,
    issues: list[tuple[str, str]],
) -> None:
    if ":" not in entry:
        issues.append(("error", "plugin.entry must use 'module:ClassName' format"))
        return

    module_name, class_name = (part.strip() for part in entry.split(":", 1))
    if not module_name or not class_name:
        issues.append(("error", "plugin.entry must include both module and class name"))
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", module_name):
        issues.append(("error", f"plugin.entry module path is invalid: {module_name}"))
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", class_name):
        issues.append(("error", f"plugin.entry class name is invalid: {class_name}"))
        return

    module_path = _resolve_entry_module_path(plugin_dir, plugin_id, module_name)
    if module_path is None:
        issues.append(("warning", f"plugin.entry module '{module_name}' is outside the plugin directory; static entry checks skipped"))
        return
    if not module_path.is_file():
        issues.append(("error", f"plugin.entry module file is missing: {module_path.relative_to(plugin_dir)}"))
        return

    tree = _parse_python_file(module_path, issues, label=str(module_path.relative_to(plugin_dir)))
    if tree is None:
        return

    class_node = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
    if class_node is None:
        issues.append(("error", f"plugin.entry class '{class_name}' was not found in {module_path.relative_to(plugin_dir)}"))
        return

    if not _has_decorator(class_node.decorator_list, "neko_plugin"):
        issues.append(("error", f"plugin.entry class '{class_name}' must be decorated with @neko_plugin"))

    expected_bases = {
        "plugin": {"NekoPluginBase"},
        "adapter": {"NekoAdapterPlugin"},
    }.get(package_type, {"NekoPluginBase"})
    actual_bases = {_name_of(base) for base in class_node.bases}
    if expected_bases and actual_bases.isdisjoint(expected_bases):
        issues.append(("warning", f"plugin.entry class '{class_name}' should inherit one of: {', '.join(sorted(expected_bases))}"))


def _resolve_entry_module_path(plugin_dir: Path, plugin_id: str, module_name: str) -> Path | None:
    prefix = f"plugin.plugins.{plugin_id}"
    if module_name == prefix:
        return plugin_dir / "__init__.py"
    if module_name.startswith(prefix + "."):
        parts = module_name.removeprefix(prefix + ".").split(".")
        base = plugin_dir.joinpath(*parts)
        file_path = base.with_suffix(".py")
        return file_path if file_path.exists() else base / "__init__.py"
    if "." not in module_name:
        base = plugin_dir / module_name
        file_path = base.with_suffix(".py")
        return file_path if file_path.exists() else base / "__init__.py"
    return None


def _plugin_python_files(plugin_dir: Path) -> list[Path]:
    """The plugin's own .py files. Skipped directories are pruned during the
    walk, not filtered afterwards: a retained sync backup or a venv may hold
    a large tree or a mount."""
    return sorted(iter_plugin_python_files(plugin_dir))


def _check_python_decorators(plugin_dir: Path, issues: list[tuple[str, str]]) -> None:
    seen_ids: dict[str, str] = {}
    for path in _plugin_python_files(plugin_dir):
        relative = path.relative_to(plugin_dir)
        tree = _parse_python_file(path, issues, label=str(relative))
        if tree is None:
            continue
        _check_deprecated_push_message_calls(tree, relative, issues)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                name = _decorator_name(decorator)
                if name == "plugin_entry":
                    entry_id = _decorator_keyword_string(decorator, "id") or node.name
                    _check_identifier(entry_id, f"@plugin_entry id in {relative}:{node.lineno}", issues)
                    _check_schema_keyword(decorator, "input_schema", f"{relative}:{node.lineno}", issues)
                    # An entry may return a non-object (a list, a string), so no type='object' hint here.
                    _check_schema_keyword(decorator, "output_schema", f"{relative}:{node.lineno}", issues, expect_object=False)
                    _check_output_schema_literal(decorator, f"{relative}:{node.lineno}", issues)
                    _check_mutually_exclusive(decorator, {"input_schema", "params"}, f"@plugin_entry in {relative}:{node.lineno}", issues)
                    _check_mutually_exclusive(decorator, {"llm_result_fields", "llm_result_model", "fields"}, f"@plugin_entry in {relative}:{node.lineno}", issues)
                    previous = seen_ids.get(entry_id)
                    location = f"{relative}:{node.lineno}"
                    if previous:
                        issues.append(("warning", f"duplicate @plugin_entry id '{entry_id}' in {location}; first seen at {previous}"))
                    else:
                        seen_ids[entry_id] = location
                elif name == "lifecycle":
                    lifecycle_id = _decorator_keyword_string(decorator, "id")
                    allowed = {"startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"}
                    if not lifecycle_id:
                        issues.append(("error", f"@lifecycle in {relative}:{node.lineno} must declare a non-empty id"))
                    elif lifecycle_id not in allowed:
                        issues.append(("error", f"@lifecycle id '{lifecycle_id}' in {relative}:{node.lineno} must be one of: {', '.join(sorted(allowed))}"))
                elif name == "timer_interval":
                    timer_id = _decorator_keyword_string(decorator, "id")
                    if not timer_id:
                        issues.append(("error", f"@timer_interval in {relative}:{node.lineno} must declare a non-empty id"))
                    seconds = _decorator_keyword_literal(decorator, "seconds")
                    if not isinstance(seconds, int) or seconds <= 0:
                        issues.append(("error", f"@timer_interval in {relative}:{node.lineno} must declare seconds > 0"))
                elif name == "message":
                    message_id = _decorator_keyword_string(decorator, "id")
                    if not message_id:
                        issues.append(("error", f"@message in {relative}:{node.lineno} must declare a non-empty id"))
                    _check_schema_keyword(decorator, "input_schema", f"{relative}:{node.lineno}", issues)


def _check_deprecated_push_message_calls(
    tree: ast.Module,
    relative: Path,
    issues: list[tuple[str, str]],
) -> None:
    """Report explicitly-written v1 fields without executing plugin code."""
    for node in ast.walk(tree):
        # The SDK exposes push_message as a method. Requiring an attribute call
        # avoids flagging an unrelated local helper named ``push_message``.
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "push_message"
        ):
            continue
        reported_fields: set[str] = set()

        for index, argument in enumerate(node.args):
            if index >= len(LEGACY_PUSH_MESSAGE_POSITIONAL_FIELDS):
                break
            field = LEGACY_PUSH_MESSAGE_POSITIONAL_FIELDS[index]
            if field is None or isinstance(argument, ast.Starred):
                continue
            if _legacy_push_value_is_inactive(field, argument):
                continue
            _append_push_v1_warning(
                field=field,
                value_node=argument,
                call_node=node,
                relative=relative,
                issues=issues,
            )
            reported_fields.add(field)

        for keyword in node.keywords:
            field = keyword.arg
            if (
                field not in LEGACY_PUSH_MESSAGE_FIELD_MIGRATIONS
                or field in reported_fields
                or _legacy_push_value_is_inactive(field, keyword.value)
            ):
                continue
            _append_push_v1_warning(
                field=field,
                value_node=keyword,
                call_node=node,
                relative=relative,
                issues=issues,
            )


def _legacy_push_value_is_inactive(field: str, value_node: ast.AST) -> bool:
    if not isinstance(value_node, ast.Constant):
        return False
    if value_node.value is None:
        return True
    return (
        field in LEGACY_PUSH_MESSAGE_TRUTHY_ONLY_FIELDS
        and value_node.value is False
    )


def _append_push_v1_warning(
    *,
    field: str,
    value_node: ast.AST,
    call_node: ast.Call,
    relative: Path,
    issues: list[tuple[str, str]],
) -> None:
    location = f"{relative}:{getattr(value_node, 'lineno', call_node.lineno)}"
    issues.append(
        (
            "warning",
            f"{location}: {format_push_message_v1_static_diagnostic(field)}",
        )
    )


def _parse_python_file(path: Path, issues: list[tuple[str, str]], *, label: str) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        issues.append(("error", f"Python syntax error in {label}: {exc.msg} at line {exc.lineno}"))
    except UnicodeDecodeError as exc:
        issues.append(("error", f"Python file is not valid UTF-8: {label}: {exc}"))
    return None


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return _name_of(target)


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return ""


def _has_decorator(decorators: list[ast.expr], name: str) -> bool:
    return any(_decorator_name(item) == name for item in decorators)


def _decorator_keyword_string(decorator: ast.expr, keyword: str) -> str:
    value = _decorator_keyword_literal(decorator, keyword)
    return value.strip() if isinstance(value, str) else ""


def _decorator_keyword_literal(decorator: ast.expr, keyword: str) -> object:
    if not isinstance(decorator, ast.Call):
        return None
    for item in decorator.keywords:
        if item.arg == keyword:
            try:
                return ast.literal_eval(item.value)
            except (ValueError, TypeError):
                return None
    return None


def _decorator_keywords(decorator: ast.expr) -> set[str]:
    if not isinstance(decorator, ast.Call):
        return set()
    return {item.arg for item in decorator.keywords if item.arg}


def _check_identifier(value: str, label: str, issues: list[tuple[str, str]]) -> None:
    if not value:
        issues.append(("error", f"{label} must be non-empty"))
    elif not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        issues.append(("warning", f"{label} should contain only A-Z, a-z, 0-9, _, -"))


def _check_schema_keyword(
    decorator: ast.expr,
    keyword: str,
    label: str,
    issues: list[tuple[str, str]],
    *,
    expect_object: bool = True,
) -> None:
    value = _decorator_keyword_literal(decorator, keyword)
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(("error", f"{keyword} in {label} must be a dict literal when statically declared"))
        return
    if expect_object and value.get("type") != "object":
        issues.append(("warning", f"{keyword} in {label} should declare type='object'"))
    properties = value.get("properties")
    if properties is not None and not isinstance(properties, dict):
        issues.append(("error", f"{keyword}.properties in {label} must be an object"))
    required = value.get("required")
    if required is not None and not isinstance(required, list):
        issues.append(("error", f"{keyword}.required in {label} must be a list"))


def _check_output_schema_literal(decorator: ast.expr, label: str, issues: list[tuple[str, str]]) -> None:
    """An explicit output_schema, like input_schema, is published but not checked at runtime."""
    if not isinstance(_decorator_keyword_literal(decorator, "output_schema"), dict):
        return  # not a dict literal: reported by the shape check, or not static
    issues.append((
        "warning",
        f"output_schema in {label} is published only; the runtime does not validate returns against it "
        f"(annotate the return with a Pydantic model for a runtime check)",
    ))


def _check_mutually_exclusive(decorator: ast.expr, names: set[str], label: str, issues: list[tuple[str, str]]) -> None:
    present = names & _decorator_keywords(decorator)
    if len(present) > 1:
        issues.append(("error", f"{label} cannot combine mutually exclusive options: {', '.join(sorted(present))}"))


def _check_optional_file(path: Path, label: str, issues: list[tuple[str, str]], *, strict: bool) -> None:
    if path.is_file():
        return
    issues.append(("error" if strict else "warning", f"{label} is missing"))


def _check_requirements_file(path: Path, issues: list[tuple[str, str]]) -> None:
    if path.exists():
        issues.append((
            "error",
            "requirements.txt is not supported; use pyproject.toml [project].dependencies "
            "and vendor/ for Python runtime dependencies",
        ))


def _check_pyproject_dependency_layout(
    plugin_dir: Path,
    pyproject_toml: dict[str, object] | None,
    package_type: str,
    issues: list[tuple[str, str]],
) -> None:
    # plugin.core loads the plugin runtime state; keep it off CLI start-up.
    from plugin.core.python_dependencies import (
        collect_project_python_requirements,
        find_missing_python_requirements,
        split_host_provided_requirements,
    )

    python_requirements = collect_project_python_requirements(pyproject_toml)
    external_requirements, _host_requirements = split_host_provided_requirements(python_requirements)
    if not external_requirements:
        return
    vendor_dir = plugin_dir / "vendor"
    if not vendor_dir.is_dir():
        issues.append((
            "error",
            "pyproject.toml [project].dependencies declares Python runtime dependencies "
            f"({', '.join(external_requirements)}), but vendor/ is missing",
        ))
    elif not any(path.is_file() for path in vendor_dir.rglob("*")):
        issues.append((
            "error",
            "pyproject.toml [project].dependencies declares Python runtime dependencies "
            f"({', '.join(external_requirements)}), but vendor/ does not contain any files",
        ))
    else:
        missing_requirements = find_missing_python_requirements(
            external_requirements,
            search_paths=[vendor_dir],
        )
        if missing_requirements:
            issues.append((
                "error",
                "vendor/ does not satisfy Python runtime dependencies: "
                f"{', '.join(missing_requirements)}",
            ))


def _check_json_file(path: Path, label: str, issues: list[tuple[str, str]], *, strict: bool) -> None:
    if not path.is_file():
        issues.append(("error" if strict else "warning", f"{label} is missing"))
        return
    try:
        # utf-8-sig: Windows editors save a BOM, which json.loads rejects.
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        issues.append(("error" if strict else "warning", f"{label} is not valid UTF-8: {exc}"))
        return
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        issues.append(("error", f"{label} is invalid JSON: {exc}"))


def _check_gitignore(path: Path, issues: list[tuple[str, str]], *, strict: bool) -> None:
    if not path.is_file():
        issues.append(("error" if strict else "warning", ".gitignore is missing"))
        return

    try:
        # utf-8-sig: a BOM would otherwise hide the first line from the checks below.
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        issues.append(("error" if strict else "warning", f".gitignore is not valid UTF-8: {exc}"))
        return
    required_patterns = ["__pycache__/", ".pytest_cache/", "store.db"]
    for pattern in required_patterns:
        if not re.search(rf"(^|\n){re.escape(pattern)}($|\n)", text):
            issues.append(("warning", f".gitignore should include {pattern}"))


def _check_symlink_tree(plugin_dir: Path, issues: list[tuple[str, str]]) -> None:
    # Market CI rejects any symlink in the mounted source; report the same
    # rule locally so the upload is not the first place it surfaces. CI scans
    # the checkout before dependency sync, so vendor/ (which pip may populate
    # with link wrappers) and .git/ are out of scope here too.
    links: list[Path] = []
    try:
        for root, directories, files in os.walk(plugin_dir, onerror=reraise_walk_error, followlinks=False):
            base = Path(root)
            if base == plugin_dir:
                directories[:] = [name for name in directories if name not in {"vendor", ".git"}]
                files = [name for name in files if name not in {"vendor", ".git"}]
            # _is_link also catches a Windows junction, which is_symlink()
            # misses on Python 3.11 (and which os.walk would descend into).
            # Junctions are always directories, so files need only is_symlink().
            linked = {name for name in directories if _is_link(base / name)}
            linked.update(name for name in files if (base / name).is_symlink())
            links.extend(base / name for name in (*directories, *files) if name in linked)
            directories[:] = [name for name in directories if name not in linked]
    except OSError as exc:
        # Still report the links found before the failure.
        issues.append(("warning", f"plugin source tree could not be scanned for links: {exc}"))
    if not links:
        return
    shown = ", ".join(str(path.relative_to(plugin_dir)) for path in links[:5])
    suffix = f" (+{len(links) - 5} more)" if len(links) > 5 else ""
    issues.append(("error", f"plugin source must not contain symbolic links: {shown}{suffix}"))


# --- Advisory checks -------------------------------------------------------
#
# ``neko-plugin check`` (without --release) reports these as "advisory"
# issues: they print but never change the exit code, and lint, precheck,
# ``check --release`` and the Market workflows do not run them. Each matcher
# covers only the plain spelling plugin code actually uses; aliases and
# dynamically built calls are deliberately out of scope.
#
# Output layout: one language-neutral line per hit (rule code, file:line,
# AGENTS.md constraint number), then one explanation per rule that has hits,
# written in all eight N.E.K.O locales.

_ADVISORY_REFERENCE = ".agents/skills/neko-plugin/references/execution-boundary.md"
_ADVISORY_LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt")
# rule code -> (AGENTS.md hard constraint number, explanation per locale)
_ADVISORY_RULES: dict[str, tuple[int, dict[str, str]]] = {
    "host-import": (
        2,
        {
            "en": "Host internals bypass consent disclosure and can change without notice. "
            "Import from plugin.sdk.plugin (adapters: plugin.sdk.adapter).",
            "zh-CN": "宿主内部模块绕过了面向用户的授权说明，而且随时可能变动。"
            "请改从 plugin.sdk.plugin 导入（适配器用 plugin.sdk.adapter）。",
            "zh-TW": "宿主內部模組會繞過對使用者的授權說明，而且隨時可能變動。"
            "請改從 plugin.sdk.plugin 匯入（轉接器請用 plugin.sdk.adapter）。",
            "ja": "ホストの内部モジュールは利用者への同意説明を経由せず、予告なく変わることがあります。"
            "plugin.sdk.plugin（アダプターは plugin.sdk.adapter）からインポートしてください。",
            "ko": "호스트 내부 모듈은 사용자 동의 고지를 우회하며 예고 없이 바뀔 수 있습니다. "
            "plugin.sdk.plugin(어댑터는 plugin.sdk.adapter)에서 가져오세요.",
            "ru": "Внутренние модули хоста обходят уведомление о согласии и могут меняться без предупреждения. "
            "Импортируйте из plugin.sdk.plugin (для адаптеров — plugin.sdk.adapter).",
            "es": "Los módulos internos del host eluden el aviso de consentimiento y pueden cambiar sin previo aviso. "
            "Importa desde plugin.sdk.plugin (adaptadores: plugin.sdk.adapter).",
            "pt": "Os módulos internos do host contornam o aviso de consentimento e podem mudar sem aviso prévio. "
            "Importe de plugin.sdk.plugin (adaptadores: plugin.sdk.adapter).",
        },
    ),
    "print": (
        4,
        {
            "en": "print() output never reaches the plugin log. Use self.logger instead.",
            "zh-CN": "print() 的输出不会进入插件日志。请改用 self.logger。",
            "zh-TW": "print() 的輸出不會寫進外掛程式的記錄檔。請改用 self.logger。",
            "ja": "print() の出力はプラグインのログに残りません。代わりに self.logger を使ってください。",
            "ko": "print() 출력은 플러그인 로그에 남지 않습니다. 대신 self.logger를 사용하세요.",
            "ru": "Вывод print() не попадает в журнал плагина. Используйте self.logger.",
            "es": "La salida de print() no llega al registro del plugin. Usa self.logger en su lugar.",
            "pt": "A saída de print() não chega ao log do plugin. Use self.logger.",
        },
    ),
    "async-blocking": (
        7,
        {
            "en": "A blocking call inside async def stalls every entry of the plugin, because they share one "
            "event loop. Use an async library or await asyncio.to_thread(...).",
            "zh-CN": "在 async def 里做阻塞调用会卡住插件的所有入口，因为它们共用一个事件循环。"
            "请改用异步库，或 await asyncio.to_thread(...)。",
            "zh-TW": "在 async def 裡進行阻塞呼叫，會卡住外掛程式的所有進入點，因為它們共用同一個事件迴圈。"
            "請改用非同步函式庫，或 await asyncio.to_thread(...)。",
            "ja": "async def 内のブロッキング呼び出しは、同じイベントループを共有するプラグインの全エントリを止めてしまいます。"
            "非同期ライブラリか await asyncio.to_thread(...) を使ってください。",
            "ko": "async def 안의 블로킹 호출은 이벤트 루프를 함께 쓰는 플러그인의 모든 엔트리를 멈춥니다. "
            "비동기 라이브러리나 await asyncio.to_thread(...)를 사용하세요.",
            "ru": "Блокирующий вызов внутри async def останавливает все точки входа плагина: у них общий цикл событий. "
            "Используйте асинхронную библиотеку или await asyncio.to_thread(...).",
            "es": "Una llamada bloqueante dentro de async def detiene todos los puntos de entrada del plugin, porque comparten "
            "un mismo bucle de eventos. Usa una biblioteca asíncrona o await asyncio.to_thread(...).",
            "pt": "Uma chamada bloqueante dentro de async def trava todos os pontos de entrada do plugin, pois eles compartilham "
            "o mesmo loop de eventos. Use uma biblioteca assíncrona ou await asyncio.to_thread(...).",
        },
    ),
    "eval-exec": (
        10,
        {
            "en": "eval() and exec() run arbitrary code. Parse the input instead (for example json.loads or "
            "ast.literal_eval), or dispatch through an explicit table of functions.",
            "zh-CN": "eval() 和 exec() 会执行任意代码。请改为解析输入（例如 json.loads 或 ast.literal_eval），"
            "或用显式的函数映射表来分派。",
            "zh-TW": "eval() 與 exec() 會執行任意程式碼。請改為解析輸入（例如 json.loads 或 ast.literal_eval），"
            "或透過明確的函式對照表來分派。",
            "ja": "eval() と exec() は任意のコードを実行します。入力は json.loads や ast.literal_eval などで解析するか、"
            "明示的な関数テーブルで振り分けてください。",
            "ko": "eval()과 exec()는 임의의 코드를 실행합니다. 입력을 파싱하거나(json.loads, ast.literal_eval 등) "
            "명시적인 함수 테이블로 분기하세요.",
            "ru": "eval() и exec() выполняют произвольный код. Разбирайте входные данные (например, json.loads или "
            "ast.literal_eval) или выбирайте функцию по явной таблице.",
            "es": "eval() y exec() ejecutan código arbitrario. Analiza la entrada (por ejemplo, con json.loads o "
            "ast.literal_eval) o despacha mediante una tabla explícita de funciones.",
            "pt": "eval() e exec() executam código arbitrário. Interprete a entrada (por exemplo, com json.loads ou "
            "ast.literal_eval) ou despache por meio de uma tabela explícita de funções.",
        },
    ),
    "pickle": (
        10,
        {
            "en": "Unpickling can run code embedded in the data. Store the data as JSON instead.",
            "zh-CN": "pickle 反序列化可能执行数据里夹带的代码。请改用 JSON 保存数据。",
            "zh-TW": "pickle 反序列化可能執行資料中夾帶的程式碼。請改用 JSON 儲存資料。",
            "ja": "pickle のデシリアライズは、データに埋め込まれたコードを実行することがあります。"
            "データは JSON で保存してください。",
            "ko": "pickle 역직렬화는 데이터에 숨은 코드를 실행할 수 있습니다. "
            "데이터는 JSON으로 저장하세요.",
            "ru": "При десериализации pickle может выполниться код, встроенный в данные. "
            "Храните данные в JSON.",
            "es": "Deserializar con pickle puede ejecutar código incrustado en los datos. "
            "Guarda los datos en JSON.",
            "pt": "Desserializar com pickle pode executar código embutido nos dados. "
            "Guarde os dados em JSON.",
        },
    ),
    "shell-true": (
        10,
        {
            "en": "shell=True hands the whole command line to a shell, so outside text in it can inject commands. "
            "Pass the arguments as a list and drop shell=True.",
            "zh-CN": "shell=True 会把整条命令行交给 shell 执行，混在其中的外部文本可能注入命令。"
            "请把参数写成列表传入，并去掉 shell=True。",
            "zh-TW": "shell=True 會把整行指令交給 shell 執行，混在其中的外部文字可能被用來注入指令。"
            "請將參數以串列傳入，並拿掉 shell=True。",
            "ja": "shell=True ではコマンドライン全体がシェルに渡されるため、紛れ込んだ外部の文字列でコマンドを注入されるおそれがあります。"
            "引数はリストで渡し、shell=True は外してください。",
            "ko": "shell=True는 명령줄 전체를 셸에 넘기므로, 섞여 들어간 외부 문자열로 명령이 주입될 수 있습니다. "
            "인수를 리스트로 넘기고 shell=True는 빼세요.",
            "ru": "При shell=True вся командная строка уходит в оболочку, и попавший в неё внешний текст может "
            "внедрить команды. Передавайте аргументы списком и уберите shell=True.",
            "es": "Con shell=True toda la línea de comandos pasa por un shell, así que cualquier texto externo que "
            "contenga puede inyectar comandos. Pasa los argumentos como lista y quita shell=True.",
            "pt": "Com shell=True a linha de comando inteira passa por um shell, e qualquer texto externo nela pode "
            "injetar comandos. Passe os argumentos como lista e remova shell=True.",
        },
    ),
    "verify-false": (
        10,
        {
            "en": "verify=False turns off TLS certificate checks, so the connection can be intercepted. "
            'Remove it; for a private CA, pass verify="path/to/ca.pem".',
            "zh-CN": "verify=False 会关闭 TLS 证书校验，连接可能被中间人截获。"
            '请去掉它；需要信任私有 CA 时，传入该 CA 证书的路径，例如 verify="path/to/ca.pem"。',
            "zh-TW": "verify=False 會關閉 TLS 憑證驗證，連線可能遭到中間人攔截。"
            '請移除它；需要信任私有 CA 時，請傳入該 CA 憑證的路徑，例如 verify="path/to/ca.pem"。',
            "ja": "verify=False は TLS 証明書の検証を無効にするため、通信を傍受されるおそれがあります。"
            '削除してください。独自の CA を使う場合は、その CA 証明書のパスを verify="path/to/ca.pem" のように渡してください。',
            "ko": "verify=False는 TLS 인증서 검증을 꺼 버려서 제3자가 연결을 가로챌 수 있습니다. "
            '제거하세요. 사설 CA를 신뢰해야 한다면 verify="path/to/ca.pem"처럼 CA 인증서 경로를 넘기세요.',
            "ru": "verify=False отключает проверку TLS-сертификатов, и соединение могут перехватить. "
            'Уберите его; для собственного центра сертификации передайте verify="path/to/ca.pem".',
            "es": "verify=False desactiva la verificación de certificados TLS, así que la conexión puede ser "
            'interceptada. Quítalo; para una CA privada, pasa verify="path/to/ca.pem".',
            "pt": "verify=False desativa a verificação de certificados TLS, e a conexão pode ser interceptada. "
            'Remova-o; para uma CA privada, passe verify="path/to/ca.pem".',
        },
    ),
}

_ADVISORY_SKIPPED_TOP_DIRS = frozenset({"tests"})
# Top-level host packages a plugin process can import because the N.E.K.O
# root is on sys.path. ``plugin.*`` is handled separately.
_HOST_INTERNAL_ROOTS = frozenset(
    {
        "app",
        "config",
        "knowledge",
        "launcher_core",
        "main_logic",
        "main_routers",
        "memory",
        "nekoclaw",
        "services",
        "steamworks",
        "utils",
    }
)
# Public SDK facades (see plugin/sdk/__init__.py); ``plugin.plugins.*`` is
# plugin code, not the host.
_PUBLIC_PLUGIN_MODULE_PREFIXES = ("plugin.sdk.plugin", "plugin.sdk.adapter", "plugin.plugins")
_PICKLE_LOAD_FUNCS = frozenset({"load", "loads", "Unpickler"})
_REQUESTS_BLOCKING_FUNCS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request"})
_SUBPROCESS_BLOCKING_FUNCS = frozenset({"run", "call", "check_call", "check_output"})
_THREAD_OFFLOAD_FUNCS = frozenset({"to_thread", "run_in_executor"})
_NESTED_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def collect_advisory_issues(plugin_dir: Path) -> list[tuple[str, str]]:
    """Non-blocking hints for AGENTS.md hard constraints 2, 4, 7 and 10.

    Files that do not parse are skipped: validate_plugin_dir already reports
    them as errors.
    """
    hits: dict[str, list[str]] = {code: [] for code in _ADVISORY_RULES}
    for path in _plugin_python_files(plugin_dir):
        relative = path.relative_to(plugin_dir)
        if relative.parts[0] in _ADVISORY_SKIPPED_TOP_DIRS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for code, lineno, detail in sorted(_advisory_findings(tree), key=lambda item: item[1]):
            hits[code].append(f"{relative}:{lineno}" + (f" {detail}" if detail else ""))

    issues: list[tuple[str, str]] = []
    for code, locations in hits.items():
        if not locations:
            continue
        constraint = _ADVISORY_RULES[code][0]
        issues.extend(("advisory", f"{code}: {location} -> AGENTS.md #{constraint}") for location in locations)
        issues.append(("advisory", _advisory_explanation(code)))
    return issues


def _advisory_explanation(code: str) -> str:
    """One block per rule: reference first, then one line per locale."""
    constraint, texts = _ADVISORY_RULES[code]
    lines = [f"{code}: AGENTS.md #{constraint}, {_ADVISORY_REFERENCE}"]
    lines.extend(f"      {locale}: {texts[locale]}" for locale in _ADVISORY_LOCALES)
    return "\n".join(lines)


def _advisory_findings(tree: ast.Module) -> list[tuple[str, int, str]]:
    """(rule code, line, language-neutral detail) for each hit."""
    findings: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for code, detail in _advisory_call_rules(node):
                findings.append((code, node.lineno, detail))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for module in _host_internal_imports(node):
                findings.append(("host-import", node.lineno, module))
        elif isinstance(node, ast.AsyncFunctionDef):
            for call in _calls_in_own_scope(node):
                blocking = _blocking_call_name(call)
                if blocking:
                    findings.append(("async-blocking", call.lineno, f"{node.name}(): {blocking}()"))
    return findings


def _advisory_call_rules(node: ast.Call) -> list[tuple[str, str]]:
    """Every (rule code, detail) one call matches."""
    rules: list[tuple[str, str]] = []
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "print":
            rules.append(("print", ""))
        elif func.id in {"eval", "exec"}:
            rules.append(("eval-exec", f"{func.id}()"))
    elif (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "pickle"
        and func.attr in _PICKLE_LOAD_FUNCS
    ):
        rules.append(("pickle", f"pickle.{func.attr}"))
    for keyword in node.keywords:
        if not isinstance(keyword.value, ast.Constant):
            continue
        if keyword.arg == "shell" and keyword.value.value is True:
            rules.append(("shell-true", ""))
        elif keyword.arg == "verify" and keyword.value.value is False:
            rules.append(("verify-false", ""))
    return rules


def _host_internal_imports(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Every host-internal module one import statement names."""
    if isinstance(node, ast.ImportFrom):
        if node.level or not node.module:
            return []
        # ``from plugin import core`` / ``from plugin.sdk import shared`` name
        # the internal module in the imported names. Everything ``plugin``
        # exports besides ``sdk`` and ``plugins`` is host runtime; under
        # ``plugin.sdk`` only ``shared`` is internal (SDK_VERSION etc. are public).
        if node.module == "plugin":
            return [
                # ``*`` is not a module path; show the statement itself.
                "from plugin import *" if alias.name == "*" else f"plugin.{alias.name}"
                for alias in node.names
                if alias.name not in {"sdk", "plugins"}
            ]
        if node.module == "plugin.sdk":
            return [f"plugin.sdk.{alias.name}" for alias in node.names if alias.name == "shared"]
        modules = [node.module]
    else:
        modules = [alias.name for alias in node.names]
    return [module for module in modules if _is_host_internal_module(module)]


def _is_host_internal_module(module: str) -> bool:
    root = module.split(".", 1)[0]
    if root in _HOST_INTERNAL_ROOTS:
        return True
    if root != "plugin" or module in {"plugin", "plugin.sdk"}:
        return False
    return not any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in _PUBLIC_PLUGIN_MODULE_PREFIXES
    )


def _calls_in_own_scope(function: ast.AsyncFunctionDef) -> list[ast.Call]:
    """Calls that run on the event loop: nested defs, lambdas and classes are
    skipped (``asyncio.to_thread(lambda: ...)`` is the recommended fix)."""
    calls: list[ast.Call] = []
    pending: list[ast.AST] = [*function.body]
    while pending:
        node = pending.pop()
        if isinstance(node, _NESTED_SCOPE_NODES):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
            if _is_thread_offload(node):
                # A generator handed to a worker thread is consumed there:
                # only its first iterable runs here, on creation.
                for child in ast.iter_child_nodes(node):
                    pending.append(child.generators[0].iter if isinstance(child, ast.GeneratorExp) else child)
                continue
        pending.extend(ast.iter_child_nodes(node))
    return calls


def _is_thread_offload(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr in _THREAD_OFFLOAD_FUNCS


def _blocking_call_name(call: ast.Call) -> str:
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return ""
    owner, name = func.value.id, func.attr
    if (
        (owner == "time" and name == "sleep")
        or (owner == "requests" and name in _REQUESTS_BLOCKING_FUNCS)
        or (owner == "subprocess" and name in _SUBPROCESS_BLOCKING_FUNCS)
    ):
        return f"{owner}.{name}"
    return ""
