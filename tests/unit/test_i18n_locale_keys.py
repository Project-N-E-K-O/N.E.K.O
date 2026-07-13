import json
import os
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = REPO_ROOT / "static" / "locales"
REQUIRED_KEYS = (
    "autostartPrompt.title",
    "autostartPrompt.message",
    "autostartPrompt.note",
    "autostartPrompt.startNow",
    "autostartPrompt.later",
    "autostartPrompt.never",
    "autostartPrompt.requiresApproval",
    "tutorialPrompt.title",
    "tutorialPrompt.message",
    "tutorialPrompt.note",
    "tutorialPrompt.startNow",
    "tutorialPrompt.later",
    "tutorialPrompt.never",
    "tutorialPrompt.startFailed",
)

CHARACTER_MANAGER_VOICE_KEYS = (
    "voice.providerUnknown",
    "voice.providerLocal",
    "voice.providerFree",
    "voice.providerFreeApi",
    "voice.sourcePreset",
    "voice.sourceClone",
    "voice.sourceDesign",
    "voice.nativeVoice.qingchunshaonv",
    "voice.nativeVoice.wenrounansheng",
)

CHARACTER_MANAGER_JS_DIR = REPO_ROOT / "static" / "js" / "character_card_manager"

PNG_TUBER_PREVIEW_LABELS = {
    "zh-CN.json": ("测试说话", "状态预览"),
    "zh-TW.json": ("測試說話", "狀態預覽"),
    "en.json": ("Test Talking", "State Preview"),
    "ja.json": ("発話をテスト", "状態プレビュー"),
    "ko.json": ("말하기 테스트", "상태 미리보기"),
    "ru.json": ("Проверить речь", "Предпросмотр состояния"),
    "es.json": ("Probar habla", "Vista previa de estado"),
    "pt.json": ("Testar fala", "Prévia de estado"),
}

PNG_TUBER_UPLOAD_LABELS = {
    "zh-CN.json": ("导入工程文件", "导入文件夹"),
    "zh-TW.json": ("導入工程檔案", "導入資料夾"),
    "en.json": ("Import Project File", "Import Folder"),
    "ja.json": ("プロジェクトファイルをインポート", "フォルダーをインポート"),
    "ko.json": ("프로젝트 파일 가져오기", "폴더 가져오기"),
    "ru.json": ("Импорт файла проекта", "Импорт папки"),
    "es.json": ("Importar archivo de proyecto", "Importar carpeta"),
    "pt.json": ("Importar arquivo de projeto", "Importar pasta"),
}


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: locale coverage checks are file-only."""
    yield




def _flatten_leaf_strings(payload, prefix=""):
    if isinstance(payload, dict):
        for key, value in payload.items():
            dotted = f"{prefix}.{key}" if prefix else key
            yield from _flatten_leaf_strings(value, dotted)
    elif isinstance(payload, str):
        yield prefix, payload


def _iter_tracked_source_files():
    ignored_dirs = {"node_modules", "dist", "build"}
    source_roots = (REPO_ROOT / "static" / "js", REPO_ROOT / "templates")
    source_suffixes = {".html", ".js", ".jsx", ".py", ".ts", ".tsx"}
    for source_root in source_roots:
        if not source_root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(source_root):
            dirnames[:] = [dirname for dirname in dirnames if dirname not in ignored_dirs]
            root = Path(dirpath)
            if root == LOCALES_DIR or LOCALES_DIR in root.parents:
                continue
            for filename in filenames:
                path = root / filename
                if path.suffix in source_suffixes:
                    yield path


def _extract_call(text: str, start: int) -> str | None:
    paren = text.find("(", start)
    if paren < 0:
        return None

    depth = 0
    quote = None
    escaped = False
    for index in range(paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None

def _has_nested_key(data: dict, dotted_key: str) -> bool:
    current = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


@pytest.mark.unit
def test_tutorial_prompt_locale_keys_exist_in_all_locales():
    missing_by_locale: dict[str, list[str]] = {}

    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = [key for key in REQUIRED_KEYS if not _has_nested_key(data, key)]
        if missing:
            missing_by_locale[locale_path.name] = missing

    assert missing_by_locale == {}


@pytest.mark.unit
def test_character_manager_voice_source_labels_exist_in_all_locales():
    missing_by_locale: dict[str, list[str]] = {}

    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = [key for key in CHARACTER_MANAGER_VOICE_KEYS if not _has_nested_key(data, key)]
        if missing:
            missing_by_locale[locale_path.name] = missing

    assert missing_by_locale == {}


@pytest.mark.unit
def test_character_manager_voice_source_labels_do_not_use_cjk_fallbacks():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(CHARACTER_MANAGER_JS_DIR.glob("*.js"))
    )
    relevant_start = source.index("function _panelVoiceProviderShortName(provider)")
    relevant_end = source.index("function _panelCreateVoiceSelectUi(selectEl)", relevant_start)
    relevant_source = source[relevant_start:relevant_end]
    relevant_source = re.sub(r"//.*", "", relevant_source)
    relevant_source = re.sub(r"/\*.*?\*/", "", relevant_source, flags=re.DOTALL)

    for hardcoded_label in ("其他", "本地 CosyVoice", "免费", "预制", "克隆", "描述生成"):
        assert hardcoded_label not in relevant_source


@pytest.mark.unit
def test_pngtuber_preview_labels_are_localized():
    mismatches: dict[str, tuple[str | None, str | None]] = {}

    for locale_name, expected in PNG_TUBER_PREVIEW_LABELS.items():
        data = json.loads((LOCALES_DIR / locale_name).read_text(encoding="utf-8"))
        live2d = data.get("live2d") if isinstance(data, dict) else None
        actual = (
            live2d.get("pngtuberTalkPreview") if isinstance(live2d, dict) else None,
            live2d.get("pngtuberStatePreview") if isinstance(live2d, dict) else None,
        )
        if actual != expected:
            mismatches[locale_name] = actual

    assert mismatches == {}


@pytest.mark.unit
def test_pngtuber_upload_choice_labels_are_localized():
    mismatches: dict[str, tuple[str | None, str | None]] = {}

    for locale_name, expected in PNG_TUBER_UPLOAD_LABELS.items():
        data = json.loads((LOCALES_DIR / locale_name).read_text(encoding="utf-8"))
        live2d = data.get("live2d") if isinstance(data, dict) else None
        actual = (
            live2d.get("pngtuberImportProjectFile") if isinstance(live2d, dict) else None,
            live2d.get("pngtuberImportFolder") if isinstance(live2d, dict) else None,
        )
        if actual != expected:
            mismatches[locale_name] = actual

    assert mismatches == {}


@pytest.mark.unit
def test_error_placeholder_i18n_calls_pass_error_params():
    locale_error_keys: dict[str, set[str]] = {}
    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        locale_error_keys[locale_path.name] = {
            key for key, value in _flatten_leaf_strings(payload) if "{{error}}" in value
        }

    assert "en.json" in locale_error_keys, "en.json locale file not found - ensure it exists in the locales directory"
    baseline = locale_error_keys["en.json"]
    assert all(keys == baseline for keys in locale_error_keys.values())

    source_texts = [
        (path, path.read_text(encoding="utf-8", errors="ignore"))
        for path in _iter_tracked_source_files()
    ]
    missing_error_params: list[str] = []

    for key in sorted(baseline):
        pattern = re.compile(r"(?:window\.)?t\s*\(\s*['\"`]" + re.escape(key) + r"['\"`]")
        for path, source in source_texts:
            for match in pattern.finditer(source):
                call = _extract_call(source, match.start())
                line = source.count("\n", 0, match.start()) + 1
                if call is None or not re.search(r"\berror\s*:", call):
                    missing_error_params.append(f"{path.relative_to(REPO_ROOT)}:{line}: {key}")

    assert missing_error_params == []
