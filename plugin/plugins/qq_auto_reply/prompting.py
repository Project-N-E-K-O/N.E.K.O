from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from pathlib import Path
import re
import time
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from PIL import Image

from utils.llm_client import create_chat_llm_async, strip_thinking_segments
from utils.screenshot_utils import compress_screenshot
from utils.token_tracker import set_call_type
from .pipeline_models import QQInstructionBundle
from .prompt_fragment_templates import LOGIN_IDENTITY_PROMPT


_THINK_TAG_VARIANT_PAIRED_RE = re.compile(
    r"<(?P<tag>think(?:ing)?(?:[_-][a-z0-9_:-]+)+)\s*>.*?</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_TAG_VARIANT_CLOSE_RE = re.compile(
    r"</think(?:ing)?(?:[_-][a-z0-9_:-]+)+\s*>",
    re.IGNORECASE,
)


class QQAutoReplyPromptingMixin:
    def _build_user_title(
        self,
        *,
        permission_level: str,
        sender_id: str,
        master_name: str,
        custom_nickname: str | None,
        user_nickname: str | None,
        is_group: bool,
    ) -> str:
        return self.prompt_builder.build_user_title(
            permission_level=permission_level,
            sender_id=sender_id,
            master_name=master_name,
            custom_nickname=custom_nickname,
            user_nickname=user_nickname,
            is_group=is_group,
        )

    def _build_character_card_fields(self, current_character: dict[str, Any]) -> dict[str, Any]:
        return self.prompt_builder.build_character_card_fields(current_character)

    def _should_use_memory_context(self, *, is_group: bool, permission_level: str, requested: Optional[bool]) -> bool:
        return self.prompt_builder.should_use_memory_context(
            is_group=is_group,
            permission_level=permission_level,
            requested=requested,
        )

    def _should_persist_memory(self, *, should_use_memory_context: bool, requested: Optional[bool]) -> bool:
        return self.prompt_builder.should_persist_memory(
            should_use_memory_context=should_use_memory_context,
            requested=requested,
        )

    def _build_prompt_message(
        self,
        *,
        is_group: bool,
        group_facing: bool,
        group_scene_mode: str,
        user_title: str,
        sender_id: str,
        group_id: str | None,
        message: str,
        current_message_id: str = "",
    ) -> str:
        return self.prompt_builder.build_prompt_message(
            is_group=is_group,
            group_facing=group_facing,
            group_scene_mode=group_scene_mode,
            user_title=user_title,
            sender_id=sender_id,
            group_id=group_id,
            message=message,
            current_message_id=current_message_id,
        )

    @staticmethod
    def _sanitize_generated_reply(reply: str) -> str:
        cleaned = strip_thinking_segments(str(reply or "")).strip()
        if not cleaned:
            return ""
        cleaned = _THINK_TAG_VARIANT_PAIRED_RE.sub("", cleaned)
        while True:
            match = _THINK_TAG_VARIANT_CLOSE_RE.search(cleaned)
            if not match:
                break
            suffix = cleaned[match.end():]
            if _THINK_TAG_VARIANT_CLOSE_RE.sub("", suffix).strip():
                cleaned = suffix
                continue
            cleaned = _THINK_TAG_VARIANT_CLOSE_RE.sub("", cleaned)
            break
        return cleaned.strip()

    @staticmethod
    def _normalize_login_identity(login_payload: dict[str, Any] | None) -> tuple[str, str | None, str | None]:
        payload = dict(login_payload or {})
        status = str(payload.get("status") or "offline").strip() or "offline"
        self_id = str(payload.get("self_id") or "").strip() or None
        nickname = str(payload.get("nickname") or "").strip() or None
        return status, self_id, nickname

    @staticmethod
    def _build_login_identity_instruction(*, her_name: str, login_status: str, login_self_id: str | None, login_nickname: str | None) -> str:
        if login_self_id:
            account_line = (
                f'- 当前登录的 QQ 账号对应名字是：{login_nickname}；账号号码仅供你内部识别，不要在普通自我介绍里主动报出'
                if login_nickname else
                '- 当前登录的 QQ 账号号码已知，但没有可用昵称；除非对方明确追问号码，否则不要主动报出'
            )
        else:
            account_line = "- 当前暂时无法确认登录的 QQ 账号，请不要编造账号身份信息"
        status_line = "- 当前 QQ 账号状态：已登录" if login_status == "online" and login_self_id else "- 当前 QQ 账号状态：暂时无法确认或未登录"
        return LOGIN_IDENTITY_PROMPT.format(
            account_line=account_line,
            status_line=status_line,
            her_name=her_name,
        )

    @staticmethod
    def _collect_image_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for attachment in list(attachments or []):
            if not isinstance(attachment, dict):
                continue
            attachment_type = str(attachment.get("type") or "").strip()
            if attachment_type not in {"image", "image_url"}:
                continue
            locator = str(attachment.get("url") or attachment.get("path") or attachment.get("file") or "").strip()
            if not locator:
                continue
            normalized.append(dict(attachment))
        return normalized

    @staticmethod
    def _should_skip_text_fallback_for_images(*, prompt_message: str, attachments: list[dict[str, Any]] | None) -> bool:
        has_images = bool(QQAutoReplyPromptingMixin._collect_image_attachments(attachments))
        return has_images and not str(prompt_message or "").strip()

    @staticmethod
    def _should_skip_direct_llm_fallback_for_images(*, message: str, attachments: list[dict[str, Any]] | None) -> bool:
        has_images = bool(QQAutoReplyPromptingMixin._collect_image_attachments(attachments))
        return has_images and not str(message or "").strip()

    @staticmethod
    def _resolve_local_attachment_path(locator: str) -> Path:
        text = str(locator or "").strip()
        if text.startswith("file://"):
            parsed = urlparse(text)
            candidate = unquote(parsed.path or "")
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                candidate = f"//{parsed.netloc}{candidate}"
            elif re.match(r"^/[A-Za-z]:/", candidate):
                candidate = candidate[1:]
            return Path(candidate)
        return Path(text)

    async def _prepare_attachment_image_b64(self, attachment: dict[str, Any]) -> str | None:
        locator = str(attachment.get("url") or attachment.get("path") or attachment.get("file") or "").strip()
        if not locator:
            return None
        try:
            image_bytes: bytes
            if locator.startswith(("http://", "https://")):
                import httpx

                timeout = max(3.0, min(float(self._ai_turn_timeout_seconds or 60.0) / 2.0, 15.0))
                async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
                    response = await client.get(locator)
                    response.raise_for_status()
                    image_bytes = response.content
            else:
                image_path = self._resolve_local_attachment_path(locator)
                image_bytes = await asyncio.to_thread(image_path.read_bytes)
            return await asyncio.to_thread(self._encode_image_bytes_to_jpeg_b64, image_bytes)
        except Exception as exc:
            self.logger.warning(f"QQ 图片附件预处理失败: {exc}")
            return None

    @staticmethod
    def _encode_image_bytes_to_jpeg_b64(image_bytes: bytes) -> str | None:
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                if image.mode in ("RGBA", "LA", "P"):
                    image = image.convert("RGB")
                jpeg_bytes = compress_screenshot(image)
            return base64.b64encode(jpeg_bytes).decode("utf-8")
        except Exception:
            return None

    async def _queue_attachment_images(self, user_session: Any, attachments: list[dict[str, Any]] | None) -> int:
        queued = 0
        for attachment in self._collect_image_attachments(attachments):
            image_b64 = await self._prepare_attachment_image_b64(attachment)
            if not image_b64:
                continue
            await user_session.stream_image(image_b64)
            queued += 1
        return queued

    @staticmethod
    def _build_group_turn_message(*, group_scene_mode: str, user_title: str, sender_id: str, group_id: str | None, message: str, current_message_id: str = "") -> str:
        normalized_mode = str(group_scene_mode or "shared_context").strip() or "shared_context"
        msg_id_line = f"当前消息ID: {current_message_id}\n" if current_message_id else ""
        if normalized_mode == "group_collective":
            return (
                f"[QQ 群公开发言]\n"
                f"当前群号: {str(group_id or '').strip()}\n"
                f"当前讨论内容:\n{message}\n"
                f"请把这次回复视为面向整个群体的公开发言，而不是只对某一个人说话。"
            )
        if normalized_mode == "directed_user":
            return (
                f"[QQ 群定向回应]\n"
                f"{msg_id_line}"
                f"当前发言人: {user_title}\n"
                f"当前发言人QQ: {sender_id}\n"
                f"当前群号: {str(group_id or '').strip()}\n"
                f"消息内容:\n{message}\n"
                f"请把这次回复视为对当前发言人的自然回应。"
            )
        return (
            f"[QQ 群共享上下文]\n"
            f"{msg_id_line}"
            f"当前发言人: {user_title}\n"
            f"当前发言人QQ: {sender_id}\n"
            f"当前群号: {str(group_id or '').strip()}\n"
            f"消息内容:\n{message}\n"
            f"请结合群里的共享话题自然接话，但不要把回复写成明显点名当前发言人的一对一回应。"
        )

    async def _build_qq_session_instructions(
        self,
        her_name: str,
        master_name: str,
        character_prompt: str,
        character_card_fields: dict,
        permission_level: str,
        sender_id: str,
        user_title: str,
        is_group: bool = False,
        group_id: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        address_user_by_name: bool = True,
        group_facing: bool = False,
        shared_group_session: bool = False,
        group_scene_mode: str = "",
        login_status: str = "offline",
        login_self_id: str | None = None,
        login_nickname: str | None = None,
    ) -> QQInstructionBundle:
        return await self.session_instruction_service.build_session_instructions(
            her_name=her_name,
            master_name=master_name,
            character_prompt=character_prompt,
            character_card_fields=character_card_fields,
            permission_level=permission_level,
            sender_id=sender_id,
            user_title=user_title,
            is_group=is_group,
            group_id=group_id,
            use_memory_context=use_memory_context,
            address_user_by_name=address_user_by_name,
            group_facing=group_facing,
            shared_group_session=shared_group_session,
            group_scene_mode=group_scene_mode,
            login_status=login_status,
            login_self_id=login_self_id,
            login_nickname=login_nickname,
        )

    async def _generate_reply_fallback_direct_llm(
        self,
        *,
        message: str,
        attachments: list[dict[str, Any]] | None,
        her_name: str,
        master_name: str,
        character_prompt: str,
        character_card_fields: dict,
        permission_level: str,
        sender_id: str,
        user_title: str,
        is_group: bool = False,
        group_id: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        group_facing: bool = False,
        group_scene_mode: str = "",
        login_status: str = "offline",
        login_self_id: str | None = None,
        login_nickname: str | None = None,
    ) -> Optional[str]:
        return await self.reply_generation_service.generate_reply_fallback_direct_llm(
            message=message,
            attachments=attachments,
            her_name=her_name,
            master_name=master_name,
            character_prompt=character_prompt,
            character_card_fields=character_card_fields,
            permission_level=permission_level,
            sender_id=sender_id,
            user_title=user_title,
            is_group=is_group,
            group_id=group_id,
            use_memory_context=use_memory_context,
            group_facing=group_facing,
            group_scene_mode=group_scene_mode,
            login_status=login_status,
            login_self_id=login_self_id,
            login_nickname=login_nickname,
        )

    async def _ensure_session_for_user(self, user_data: dict[str, object]) -> Optional[dict[str, object]]:
        return await self.session_bootstrap_service.ensure_session_for_user(user_data)

    async def _generate_reply(
        self,
        message: str,
        permission_level: str,
        sender_id: str,
        attachments: list[dict[str, Any]] | None = None,
        is_group: bool = False,
        group_id: str = None,
        user_nickname: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        persist_memory: Optional[bool] = None,
        ephemeral_session: bool = False,
        group_facing: bool = False,
        group_scene_mode: str = "",
    ) -> Optional[str]:
        return await self.reply_generation_service.generate_reply(
            message=message,
            permission_level=permission_level,
            sender_id=sender_id,
            attachments=attachments,
            is_group=is_group,
            group_id=group_id,
            user_nickname=user_nickname,
            use_memory_context=use_memory_context,
            persist_memory=persist_memory,
            ephemeral_session=ephemeral_session,
            group_facing=group_facing,
            group_scene_mode=group_scene_mode,
        )
