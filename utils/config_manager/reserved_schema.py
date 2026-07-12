# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pure helpers for the character `_reserved` field schema.

Read/write/delete of nested reserved fields, schema validation, the
legacy-to-`_reserved` migration for a single character and the reverse
flattening for legacy callers/frontends. Stateless module-level functions.
"""
from config import RESERVED_FIELD_SCHEMA
from utils.voice_config import read_legacy_voice_id


def get_reserved(data: dict, *path, default=None, legacy_keys: tuple[str, ...] | None = None):
    """Unified read of nested fields under `_reserved`, with fallback to legacy flat fields.

    If the nested path exists in _reserved (even when the value is None), return it directly;
    only fall back to the legacy flat field when the path is absent or _reserved itself is missing.
    """
    if not isinstance(data, dict):
        return default

    reserved = data.get("_reserved")
    if isinstance(reserved, dict):
        current = reserved
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found:
            return current

    # COMPAT(v1->v2): 旧平铺字段回退读取，避免历史配置在迁移前读不到值。
    if legacy_keys:
        for legacy_key in legacy_keys:
            if legacy_key in data and data[legacy_key] is not None:
                return data[legacy_key]
    return default


def set_reserved(data: dict, *path_and_value) -> bool:
    """Unified write of nested fields under `_reserved`, auto-creating intermediate levels.

    Returns ``True`` if the stored value was actually changed, ``False``
    otherwise (including invalid input).
    """
    if not isinstance(data, dict) or len(path_and_value) < 2:
        return False
    *path, value = path_and_value
    if not path:
        return False

    reserved = data.get("_reserved")
    if not isinstance(reserved, dict):
        reserved = {}
        data["_reserved"] = reserved

    current = reserved
    for key in path[:-1]:
        next_node = current.get(key)
        if not isinstance(next_node, dict):
            next_node = {}
            current[key] = next_node
        current = next_node

    last_key = path[-1]
    if last_key in current and current[last_key] == value:
        return False
    current[last_key] = value
    return True


def delete_reserved(data: dict, *path) -> bool:
    """Delete a nested field under `_reserved`, cleaning up empty intermediate levels where possible."""
    if not isinstance(data, dict) or not path:
        return False

    reserved = data.get("_reserved")
    if not isinstance(reserved, dict):
        return False

    current = reserved
    parents: list[tuple[dict, str]] = []
    for key in path[:-1]:
        if not isinstance(current, dict) or key not in current:
            return False
        parents.append((current, key))
        current = current.get(key)

    last_key = path[-1]
    if not isinstance(current, dict) or last_key not in current:
        return False

    current.pop(last_key, None)

    while parents:
        parent, key = parents.pop()
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)
            continue
        break

    if isinstance(data.get("_reserved"), dict) and not data["_reserved"]:
        data.pop("_reserved", None)

    return True


def _legacy_live2d_to_model_path(legacy_live2d: str) -> str:
    """Convert a legacy live2d directory name to a model3 file path."""
    if not legacy_live2d:
        return ""
    raw = str(legacy_live2d).strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.endswith(".model3.json"):
        return raw
    # COMPAT(v1->v2): 历史配置只有目录名（如 mao_pro），迁移时自动补全默认 model3 文件名。
    return f"{raw}/{raw}.model3.json"


def _legacy_live2d_name_from_model_path(model_path: str) -> str:
    """Map a new model_path back to the legacy live2d model name (for legacy frontend fields)."""
    if not model_path:
        return ""
    raw = str(model_path).strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.endswith(".model3.json"):
        parent = raw.rsplit("/", 1)[0] if "/" in raw else ""
        if parent:
            return parent.rsplit("/", 1)[-1]
        filename = raw.rsplit("/", 1)[-1]
        name = filename[:-len(".model3.json")]
        return name
    return raw.rsplit("/", 1)[-1]


def validate_reserved_schema(reserved: dict) -> list[str]:
    """Validate the `_reserved` structure; returns a list of errors (empty list means pass)."""
    errors: list[str] = []

    def _walk(value, schema, path: str):
        if isinstance(schema, dict):
            if not isinstance(value, dict):
                errors.append(f"{path} 需要 dict，实际 {type(value).__name__}")
                return
            for key, sub_schema in schema.items():
                if key in value and value[key] is not None:
                    _walk(value[key], sub_schema, f"{path}.{key}")
            return
        if isinstance(schema, tuple):
            if not isinstance(value, schema):
                expected = ",".join(t.__name__ for t in schema)
                errors.append(f"{path} 需要类型({expected})，实际 {type(value).__name__}")
            return
        if not isinstance(value, schema):
            errors.append(f"{path} 需要 {schema.__name__}，实际 {type(value).__name__}")

    if reserved is None:
        return errors
    _walk(reserved, RESERVED_FIELD_SCHEMA, "_reserved")
    # voice_id 是 str | 结构对象的联合类型，schema 的 tuple 分支只做 isinstance，挡不住
    # {"foo": 1} 这种坏 dict。结构对象形态额外校验 source/provider/ref 都为 str，避免
    # 放宽 schema 后契约松掉（CodeRabbit）。
    vid = reserved.get("voice_id")
    if isinstance(vid, dict):
        for field in ("source", "provider", "ref"):
            if not isinstance(vid.get(field), str):
                errors.append(
                    f"_reserved.voice_id.{field} 需要 str，实际 {type(vid.get(field)).__name__}"
                )
    return errors


def migrate_catgirl_reserved(catgirl_data: dict) -> bool:
    """Migrate a single character config to the `_reserved` structure; returns whether changes occurred."""
    if not isinstance(catgirl_data, dict):
        return False

    changed = False

    if not isinstance(catgirl_data.get("_reserved"), dict):
        catgirl_data["_reserved"] = {}
        changed = True

    voice_id = get_reserved(catgirl_data, "voice_id", default="", legacy_keys=("voice_id",))
    # 把 voice_id 收口进 _reserved。结构对象（惰性迁移形态，可能来自顶层 legacy 字段）
    # 必须原样 set_reserved 搬进来：既不能被 str(dict) 损坏，也不能跳过——否则随后的旧
    # 顶层字段清理会 pop 掉它，导致绑定在加载时悄悄丢失（Codex/CodeRabbit P2）。
    # 普通 legacy 值仍规整成字符串。
    if isinstance(voice_id, dict):
        changed |= set_reserved(catgirl_data, "voice_id", voice_id)
    elif voice_id is not None:
        changed |= set_reserved(catgirl_data, "voice_id", str(voice_id))

    system_prompt = get_reserved(catgirl_data, "system_prompt", default=None, legacy_keys=("system_prompt",))
    if system_prompt is not None:
        changed |= set_reserved(catgirl_data, "system_prompt", str(system_prompt))

    model_type = str(
        get_reserved(catgirl_data, "avatar", "model_type", default="", legacy_keys=("model_type",))
    ).strip().lower()
    if model_type not in {"live2d", "vrm", "live3d", "pngtuber"}:
        has_vrm = catgirl_data.get("vrm") or get_reserved(catgirl_data, "avatar", "vrm", "model_path")
        has_mmd = catgirl_data.get("mmd") or get_reserved(catgirl_data, "avatar", "mmd", "model_path")
        has_pngtuber = catgirl_data.get("pngtuber") or get_reserved(catgirl_data, "avatar", "pngtuber", default={})
        if has_pngtuber:
            model_type = "pngtuber"
        else:
            model_type = "live3d" if (has_vrm or has_mmd) else "live2d"
    # 归一化：旧配置中的 'vrm' 统一为 'live3d'
    if model_type == "vrm":
        model_type = "live3d"
    changed |= set_reserved(catgirl_data, "avatar", "model_type", model_type)

    asset_source_id = get_reserved(
        catgirl_data,
        "avatar",
        "asset_source_id",
        default="",
        legacy_keys=("live2d_item_id", "item_id"),
    )
    asset_source_id = str(asset_source_id).strip() if asset_source_id is not None else ""
    changed |= set_reserved(catgirl_data, "avatar", "asset_source_id", asset_source_id)

    asset_source = get_reserved(catgirl_data, "avatar", "asset_source", default="")
    if not asset_source:
        asset_source = "steam_workshop" if asset_source_id else "local"
    changed |= set_reserved(catgirl_data, "avatar", "asset_source", str(asset_source))

    live2d_model_path = get_reserved(
        catgirl_data,
        "avatar",
        "live2d",
        "model_path",
        default="",
        legacy_keys=("live2d",),
    )
    if live2d_model_path:
        changed |= set_reserved(
            catgirl_data,
            "avatar",
            "live2d",
            "model_path",
            _legacy_live2d_to_model_path(str(live2d_model_path)),
        )

    live2d_idle_animation = get_reserved(
        catgirl_data,
        "avatar",
        "live2d",
        "idle_animation",
        default=None,
        legacy_keys=("live2d_idle_animation",),
    )
    if live2d_idle_animation is not None:
        if isinstance(live2d_idle_animation, str):
            changed |= set_reserved(catgirl_data, "avatar", "live2d", "idle_animation", live2d_idle_animation if live2d_idle_animation else None)
        elif isinstance(live2d_idle_animation, list):
            changed |= set_reserved(catgirl_data, "avatar", "live2d", "idle_animation", live2d_idle_animation[0] if live2d_idle_animation else None)

    vrm_model_path = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "model_path",
        default="",
        legacy_keys=("vrm",),
    )
    if vrm_model_path:
        changed |= set_reserved(catgirl_data, "avatar", "vrm", "model_path", str(vrm_model_path).strip())

    vrm_animation = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "animation",
        default=None,
        legacy_keys=("vrm_animation",),
    )
    if vrm_animation is not None:
        changed |= set_reserved(catgirl_data, "avatar", "vrm", "animation", vrm_animation)

    idle_animation = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "idle_animation",
        default=None,
        legacy_keys=("idleAnimation", "idleAnimations"),
    )
    if idle_animation is not None:
        # 向前兼容: 旧版存的是 string, 迁移为 list; 空值保留 []
        if isinstance(idle_animation, str):
            changed |= set_reserved(catgirl_data, "avatar", "vrm", "idle_animation", [idle_animation] if idle_animation else [])
        elif isinstance(idle_animation, list):
            changed |= set_reserved(catgirl_data, "avatar", "vrm", "idle_animation", idle_animation)

    lighting = get_reserved(
        catgirl_data,
        "avatar",
        "vrm",
        "lighting",
        default=None,
        legacy_keys=("lighting",),
    )
    if isinstance(lighting, dict):
        changed |= set_reserved(catgirl_data, "avatar", "vrm", "lighting", lighting)

    # MMD 模型路径迁移
    mmd_model_path = get_reserved(
        catgirl_data,
        "avatar",
        "mmd",
        "model_path",
        default="",
        legacy_keys=("mmd",),
    )
    if mmd_model_path:
        changed |= set_reserved(catgirl_data, "avatar", "mmd", "model_path", str(mmd_model_path).strip())

    mmd_animation = get_reserved(
        catgirl_data,
        "avatar",
        "mmd",
        "animation",
        default=None,
        legacy_keys=("mmd_animation",),
    )
    if mmd_animation is not None:
        changed |= set_reserved(catgirl_data, "avatar", "mmd", "animation", mmd_animation)

    pngtuber_config = get_reserved(catgirl_data, "avatar", "pngtuber", default=None, legacy_keys=("pngtuber",))
    if pngtuber_config is None:
        pngtuber_config = {}
    if not isinstance(pngtuber_config, dict):
        pngtuber_config = {"idle_image": str(pngtuber_config)} if str(pngtuber_config or "").strip() else {}
    pngtuber_config = dict(pngtuber_config)
    legacy_pngtuber_fields = {
        "idle_image": "pngtuber_idle_image",
        "talking_image": "pngtuber_talking_image",
        "happy_image": "pngtuber_happy_image",
        "sad_image": "pngtuber_sad_image",
        "angry_image": "pngtuber_angry_image",
        "surprised_image": "pngtuber_surprised_image",
    }
    for reserved_key, legacy_key in legacy_pngtuber_fields.items():
        if not pngtuber_config.get(reserved_key) and catgirl_data.get(legacy_key):
            pngtuber_config[reserved_key] = str(catgirl_data.get(legacy_key) or "")
    if pngtuber_config:
        changed |= set_reserved(catgirl_data, "avatar", "pngtuber", pngtuber_config)

    mmd_idle_animation = get_reserved(
        catgirl_data,
        "avatar",
        "mmd",
        "idle_animation",
        default=None,
        legacy_keys=("mmd_idle_animation", "mmd_idle_animations"),
    )
    if mmd_idle_animation is not None:
        # 向前兼容: 旧版存的是 string, 迁移为 list; 空值保留 []
        if isinstance(mmd_idle_animation, str):
            changed |= set_reserved(catgirl_data, "avatar", "mmd", "idle_animation", [mmd_idle_animation] if mmd_idle_animation else [])
        elif isinstance(mmd_idle_animation, list):
            changed |= set_reserved(catgirl_data, "avatar", "mmd", "idle_animation", mmd_idle_animation)

    live3d_sub_type = str(
        get_reserved(
            catgirl_data,
            "avatar",
            "live3d_sub_type",
            default="",
            legacy_keys=("live3d_sub_type",),
        )
        or ""
    ).strip().lower()
    if live3d_sub_type not in {"vrm", "mmd"}:
        has_mmd_model = bool(get_reserved(catgirl_data, "avatar", "mmd", "model_path", default=""))
        has_vrm_model = bool(get_reserved(catgirl_data, "avatar", "vrm", "model_path", default=""))
        if model_type == "live3d":
            if has_mmd_model:
                live3d_sub_type = "mmd"
            elif has_vrm_model:
                live3d_sub_type = "vrm"
            else:
                live3d_sub_type = ""
        elif has_mmd_model and not has_vrm_model:
            live3d_sub_type = "mmd"
        elif has_vrm_model and not has_mmd_model:
            live3d_sub_type = "vrm"
        else:
            live3d_sub_type = ""
    if live3d_sub_type:
        changed |= set_reserved(catgirl_data, "avatar", "live3d_sub_type", live3d_sub_type)
    else:
        # 非 3D 角色或没有明确活动 3D 子类型时，不要强行写回空字符串，
        # 否则会让导出/导入后的角色配置出现无意义的额外字段。
        changed |= delete_reserved(catgirl_data, "avatar", "live3d_sub_type")

    # COMPAT(v1->v2): 保留字段统一迁入 _reserved 后，移除旧平铺字段，避免再次泄露到可编辑字段。
    for legacy_key in (
        "voice_id",
        "system_prompt",
        "model_type",
        "live3d_sub_type",
        "live2d_item_id",
        "item_id",
        "live2d",
        "live2d_idle_animation",
        "vrm",
        "vrm_animation",
        "idleAnimation",
        "idleAnimations",
        "lighting",
        "vrm_rotation",
        "mmd",
        "mmd_animation",
        "mmd_idle_animation",
        "mmd_idle_animations",
        "pngtuber",
        "pngtuber_idle_image",
        "pngtuber_talking_image",
        "pngtuber_happy_image",
        "pngtuber_sad_image",
        "pngtuber_angry_image",
        "pngtuber_surprised_image",
    ):
        if legacy_key in catgirl_data:
            catgirl_data.pop(legacy_key, None)
            changed = True

    return changed


def flatten_reserved(catgirl_data: dict) -> dict:
    """Expand `_reserved` into legacy flat fields (only for compatibility with legacy callers/frontends)."""
    if not isinstance(catgirl_data, dict):
        return catgirl_data
    result = dict(catgirl_data)

    # 展平给 legacy 前端/调用方：始终吐 legacy 字符串形态（容忍结构对象）。
    voice_id = read_legacy_voice_id(get_reserved(result, "voice_id", default=""))
    if voice_id:
        result["voice_id"] = voice_id
    system_prompt = get_reserved(result, "system_prompt", default=None)
    if system_prompt is not None:
        result["system_prompt"] = system_prompt

    model_type = get_reserved(result, "avatar", "model_type", default="live2d")
    if model_type:
        result["model_type"] = model_type

    live3d_sub_type = get_reserved(result, "avatar", "live3d_sub_type", default="")
    if live3d_sub_type:
        result["live3d_sub_type"] = live3d_sub_type

    live2d_model_path = get_reserved(result, "avatar", "live2d", "model_path", default="")
    if live2d_model_path:
        result["live2d"] = _legacy_live2d_name_from_model_path(str(live2d_model_path))

    live2d_idle_animation = get_reserved(result, "avatar", "live2d", "idle_animation", default=None)
    if live2d_idle_animation is not None:
        result["live2d_idle_animation"] = live2d_idle_animation

    vrm_model_path = get_reserved(result, "avatar", "vrm", "model_path", default="")
    if vrm_model_path:
        result["vrm"] = vrm_model_path

    asset_source_id = get_reserved(result, "avatar", "asset_source_id", default="")
    if asset_source_id:
        result["live2d_item_id"] = asset_source_id

    vrm_animation = get_reserved(result, "avatar", "vrm", "animation", default=None)
    if vrm_animation is not None:
        result["vrm_animation"] = vrm_animation

    idle_animation = get_reserved(result, "avatar", "vrm", "idle_animation", default=None)
    if idle_animation is not None:
        # idleAnimation (string): 供 vrm-init / vrm-manager 等运行时消费
        # idleAnimations (list): 供 model_manager 多选 UI 消费
        if isinstance(idle_animation, str):
            result["idleAnimation"] = idle_animation
            result["idleAnimations"] = [idle_animation] if idle_animation else []
        elif isinstance(idle_animation, list):
            result["idleAnimation"] = idle_animation[0] if idle_animation else ""
            result["idleAnimations"] = idle_animation
        else:
            result["idleAnimation"] = ""
            result["idleAnimations"] = []

    lighting = get_reserved(result, "avatar", "vrm", "lighting", default=None)
    if isinstance(lighting, dict):
        result["lighting"] = lighting

    mmd_model_path = get_reserved(result, "avatar", "mmd", "model_path", default="")
    if mmd_model_path:
        result["mmd"] = mmd_model_path

    mmd_animation = get_reserved(result, "avatar", "mmd", "animation", default=None)
    if mmd_animation is not None:
        result["mmd_animation"] = mmd_animation

    mmd_idle_animation = get_reserved(result, "avatar", "mmd", "idle_animation", default=None)
    if mmd_idle_animation is not None:
        # mmd_idle_animation (string): 供 mmd-init / app-interpage 等运行时消费
        # mmd_idle_animations (list): 供 model_manager 多选 UI 消费
        if isinstance(mmd_idle_animation, str):
            result["mmd_idle_animation"] = mmd_idle_animation
            result["mmd_idle_animations"] = [mmd_idle_animation] if mmd_idle_animation else []
        elif isinstance(mmd_idle_animation, list):
            result["mmd_idle_animation"] = mmd_idle_animation[0] if mmd_idle_animation else ""
            result["mmd_idle_animations"] = mmd_idle_animation
        else:
            result["mmd_idle_animation"] = ""
            result["mmd_idle_animations"] = []

    pngtuber_config = get_reserved(result, "avatar", "pngtuber", default=None)
    if isinstance(pngtuber_config, dict) and pngtuber_config:
        result["pngtuber"] = dict(pngtuber_config)
        for key in (
            "idle_image",
            "talking_image",
            "happy_image",
            "sad_image",
            "angry_image",
            "surprised_image",
        ):
            if pngtuber_config.get(key):
                result[f"pngtuber_{key}"] = pngtuber_config.get(key)

    touch_set = get_reserved(result, 'touch_set', default=None)
    if touch_set:
        result['touch_set'] = touch_set
    return result
