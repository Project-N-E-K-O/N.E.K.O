"""Legacy URL aliases for built-in media that moved inside ``static/``.

The built-in cat media used to live flat under ``static/assets/neko-idle/``.
They now live in the ``static/assets/cat-resources/`` registry tree, and the
old copies were removed so packaged builds ship one physical copy. Earlier
releases documented the old absolute URLs (for example in the PNGTuber sample
model), so saved model and character configs may still reference them. The
``/static`` mounts resolve those old paths onto the new files in place instead
of answering 404.
"""
from __future__ import annotations

_CAT_APPEARANCE = "assets/cat-resources/appearance/dev_neko"
_CAT_VOICE = "assets/cat-resources/voice/dev_neko"

# 旧路径 -> 新路径，均相对 static/ 根、POSIX 分隔符。只登记确实被删掉的文件。
LEGACY_STATIC_ASSET_ALIASES: dict[str, str] = {
    "assets/neko-idle/cat-idle-cat1.gif": f"{_CAT_APPEARANCE}/idle/cat-idle-cat1.gif",
    "assets/neko-idle/cat-idle-cat2.gif": f"{_CAT_APPEARANCE}/idle/cat-idle-cat2.gif",
    "assets/neko-idle/cat-idle-cat3.gif": f"{_CAT_APPEARANCE}/idle/cat-idle-cat3.gif",
    "assets/neko-idle/cat-idle-cat1-click.gif": f"{_CAT_APPEARANCE}/click/cat-idle-cat1-click.gif",
    "assets/neko-idle/cat-idle-cat2-click.gif": f"{_CAT_APPEARANCE}/click/cat-idle-cat2-click.gif",
    "assets/neko-idle/cat-idle-cat3-click.gif": f"{_CAT_APPEARANCE}/click/cat-idle-cat3-click.gif",
    "assets/neko-idle/cat-idle-cat-move-1.gif": f"{_CAT_APPEARANCE}/drag/cat-idle-cat-move-1.gif",
    "assets/neko-idle/cat-idle-cat-move-2.gif": f"{_CAT_APPEARANCE}/drag/cat-idle-cat-move-2.gif",
    "assets/neko-idle/cat-idle-cat-move-3.gif": f"{_CAT_APPEARANCE}/drag/cat-idle-cat-move-3.gif",
    "assets/neko-idle/cat-idle-cat-move-4.gif": f"{_CAT_APPEARANCE}/drag/cat-idle-cat-move-4.gif",
    "assets/neko-idle/cat-idle-cat-move-5.gif": f"{_CAT_APPEARANCE}/drag/cat-idle-cat-move-5.gif",
    "assets/neko-idle/cat-idle-cat4-1.gif": f"{_CAT_APPEARANCE}/movement/cat-idle-cat4-1.gif",
    "assets/neko-idle/cat-idle-cat4-2.gif": f"{_CAT_APPEARANCE}/movement/cat-idle-cat4-2.gif",
    "assets/neko-idle/cat-idle-cat4-3.gif": f"{_CAT_APPEARANCE}/movement/cat-idle-cat4-3.gif",
    "assets/neko-idle/cat-idle-cat1-eat.gif": f"{_CAT_APPEARANCE}/action/cat-idle-cat1-eat.gif",
    "assets/neko-idle/cat-idle-cat-play-1.gif": f"{_CAT_APPEARANCE}/action/cat-idle-cat-play-1.gif",
    "assets/neko-idle/cat1-voice1.mp3": f"{_CAT_VOICE}/ambient/cat1-voice1.mp3",
    "assets/neko-idle/cat1-voice2.mp3": f"{_CAT_VOICE}/ambient/cat1-voice2.mp3",
    "assets/neko-idle/cat1-voice3.mp3": f"{_CAT_VOICE}/ambient/cat1-voice3.mp3",
    "assets/neko-idle/cat1-voice-click.mp3": f"{_CAT_VOICE}/interaction/cat1-voice-click.mp3",
    "assets/neko-idle/cat1-voice-funny.mp3": f"{_CAT_VOICE}/interaction/cat1-voice-funny.mp3",
    "assets/neko-idle/cat1-voice-chat-angry.mp3": f"{_CAT_VOICE}/interaction/cat1-voice-chat-angry.mp3",
    "assets/neko-idle/cat1-voice-eat.mp3": f"{_CAT_VOICE}/action/cat1-voice-eat.mp3",
    "assets/neko-idle/cat2-sleep1.mp3": f"{_CAT_VOICE}/sleep/cat2-sleep1.mp3",
    "assets/neko-idle/cat2-sleep2.mp3": f"{_CAT_VOICE}/sleep/cat2-sleep2.mp3",
    "assets/neko-idle/cat3-sleep1.mp3": f"{_CAT_VOICE}/sleep/cat3-sleep1.mp3",
    "assets/neko-idle/cat3-sleep2.mp3": f"{_CAT_VOICE}/sleep/cat3-sleep2.mp3",
}


def resolve_legacy_static_asset_path(path: str) -> str:
    """Return the current path for a retired ``static/`` asset path.

    ``path`` is the mount-relative path Starlette hands to ``get_response``
    (OS separators on Windows). Unknown paths are returned unchanged.
    """
    return LEGACY_STATIC_ASSET_ALIASES.get(str(path).replace("\\", "/"), path)


class LegacyStaticAssetAliasMixin:
    """StaticFiles mixin that serves retired asset URLs from their new files.

    Only mix this into the mount that serves the repository ``static/`` root.
    """

    async def get_response(self, path, scope):
        return await super().get_response(resolve_legacy_static_asset_path(path), scope)
