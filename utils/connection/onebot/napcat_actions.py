"""NapCat / go-cqhttp extension actions for :class:`OneBotClient`.

The OneBot v11 public API is a fixed list of actions (``send_msg``, ``get_msg``,
``set_group_ban``, ``get_record`` ...). Everything here is an extension that
NapCat, go-cqhttp or LLOneBot added on top; another v11 implementation (for
example a bridge to a different chat platform) will usually answer these with
``status: failed``. They live in a mixin so the v11 client itself stays readable
and so the boundary between "standard" and "extension" is visible.

Every method only builds params and calls ``self.call_action``; the mixin relies
on the class it is mixed into to provide that.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class NapCatActionsMixin:
    """Extension action wrappers (not part of the OneBot v11 public API)."""

    # ── Message operations ────────────────────────────────────────────────

    async def set_msg_emoji_like(self, message_id: str, emoji_id: str) -> Dict[str, Any]:
        """Set an emoji reaction on a message."""
        return await self.call_action("set_msg_emoji_like", {"message_id": int(message_id), "emoji_id": str(emoji_id)}, timeout=5.0)

    # ── Account settings ────────────────────────────────────────────────

    async def set_qq_profile(self, nickname: str = "", company: str = "", email: str = "", college: str = "", personal_note: str = "") -> Dict[str, Any]:
        """Set the QQ profile."""
        return await self.call_action("set_qq_profile", {"nickname": str(nickname or ""), "company": str(company or ""), "email": str(email or ""), "college": str(college or ""), "personal_note": str(personal_note or "")}, timeout=5.0)

    async def set_qq_avatar(self, file: str) -> Dict[str, Any]:
        """Set the QQ avatar."""
        return await self.call_action("set_qq_avatar", {"file": str(file)}, timeout=10.0)

    async def set_self_longnick(self, longnick: str) -> Dict[str, Any]:
        """Set one's own signature."""
        return await self.call_action("set_self_longnick", {"longnick": str(longnick)}, timeout=5.0)

    async def set_online_status(self, status: int) -> Dict[str, Any]:
        """Set the online status."""
        return await self.call_action("set_online_status", {"status": int(status)}, timeout=5.0)

    async def get_online_clients(self) -> Dict[str, Any]:
        """Fetch the list of currently online clients."""
        return await self.call_action("get_online_clients", timeout=5.0)

    async def get_robot_uin_range(self) -> Dict[str, Any]:
        """Fetch the robot UIN range."""
        return await self.call_action("get_robot_uin_range", timeout=5.0)

    # ── Friend management ────────────────────────────────────────────────

    async def delete_friend(self, user_id: str) -> Dict[str, Any]:
        """Delete a friend."""
        return await self.call_action("delete_friend", {"user_id": int(user_id)}, timeout=5.0)

    async def get_friends_with_category(self) -> Dict[str, Any]:
        """Fetch the friends list grouped by category."""
        return await self.call_action("get_friends_with_category", timeout=10.0)

    async def friend_poke(self, user_id: str) -> Dict[str, Any]:
        """Poke a friend."""
        return await self.call_action("friend_poke", {"user_id": int(user_id)}, timeout=5.0)

    async def get_profile_like(self) -> Dict[str, Any]:
        """Fetch one's own like list."""
        return await self.call_action("get_profile_like", timeout=5.0)

    # ── Message read ────────────────────────────────────────────────

    async def mark_msg_as_read(self, message_id: str) -> Dict[str, Any]:
        """Mark a message as read."""
        return await self.call_action("mark_msg_as_read", {"message_id": int(message_id)}, timeout=5.0)

    async def mark_private_msg_as_read(self, user_id: str) -> Dict[str, Any]:
        """Mark a private message as read."""
        return await self.call_action("mark_private_msg_as_read", {"user_id": int(user_id)}, timeout=5.0)

    async def mark_group_msg_as_read(self, group_id: str) -> Dict[str, Any]:
        """Mark a group message as read."""
        return await self.call_action("mark_group_msg_as_read", {"group_id": int(group_id)}, timeout=5.0)

    async def _mark_all_as_read(self) -> Dict[str, Any]:
        """Mark all messages as read."""
        return await self.call_action("_mark_all_as_read", timeout=5.0)

    # ── Merged forward ────────────────────────────────────────────────

    async def send_group_forward_msg(self, group_id: str, messages: list[Dict[str, Any]]) -> Dict[str, Any]:
        """Send a merged-forward message to a group."""
        return await self.call_action("send_group_forward_msg", {"group_id": int(group_id), "messages": messages}, timeout=10.0)

    async def send_private_forward_msg(self, user_id: str, messages: list[Dict[str, Any]]) -> Dict[str, Any]:
        """Send a merged-forward message to a friend."""
        return await self.call_action("send_private_forward_msg", {"user_id": int(user_id), "messages": messages}, timeout=10.0)

    async def send_forward_msg(self, message_type: str, user_id: str = "", group_id: str = "", messages: Optional[list[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Send a merged-forward message (generic)."""
        params: Dict[str, Any] = {"message_type": str(message_type), "messages": messages or []}
        if user_id:
            params["user_id"] = int(user_id)
        if group_id:
            params["group_id"] = int(group_id)
        return await self.call_action("send_forward_msg", params, timeout=10.0)

    async def forward_friend_single_msg(self, user_id: str, message_id: str) -> Dict[str, Any]:
        """Forward a single friend message."""
        return await self.call_action("forward_friend_single_msg", {"user_id": int(user_id), "message_id": int(message_id)}, timeout=5.0)

    async def forward_group_single_msg(self, group_id: str, message_id: str) -> Dict[str, Any]:
        """Forward a single group message."""
        return await self.call_action("forward_group_single_msg", {"group_id": int(group_id), "message_id": int(message_id)}, timeout=5.0)

    # ── Message history / input status / recent contacts ───────────────────────

    async def get_friend_msg_history(self, user_id: str, message_seq: int = 0, count: int = 20) -> Dict[str, Any]:
        """Fetch a friend's message history."""
        return await self.call_action("get_friend_msg_history", {"user_id": int(user_id), "message_seq": int(message_seq), "count": int(count)}, timeout=10.0)

    async def get_group_msg_history(self, group_id: str, message_seq: int = 0, count: int = 20) -> Dict[str, Any]:
        """Fetch a group's message history."""
        return await self.call_action("get_group_msg_history", {"group_id": int(group_id), "message_seq": int(message_seq), "count": int(count)}, timeout=10.0)

    async def set_input_status(self, user_id: str = "", group_id: str = "", event_type: int = 1) -> Dict[str, Any]:
        """Set the input status (1: typing, 2: stop typing)."""
        params: Dict[str, Any] = {"event_type": int(event_type)}
        if user_id:
            params["user_id"] = int(user_id)
        if group_id:
            params["group_id"] = int(group_id)
        return await self.call_action("set_input_status", params, timeout=5.0)

    async def get_recent_contact(self) -> Dict[str, Any]:
        """Fetch the recent contact list."""
        return await self.call_action("get_recent_contact", timeout=10.0)

    # ── Group system messages / announcements ──────────────────────────────────────

    async def get_group_system_msg(self) -> Dict[str, Any]:
        """Fetch group system messages."""
        return await self.call_action("get_group_system_msg", timeout=5.0)

    async def _send_group_notice(self, group_id: str, content: str, image: str = "") -> Dict[str, Any]:
        """Send a group announcement."""
        return await self.call_action("_send_group_notice", {"group_id": int(group_id), "content": str(content), "image": str(image)}, timeout=5.0)

    async def _get_group_notice(self, group_id: str) -> Dict[str, Any]:
        """Fetch a group announcement."""
        return await self.call_action("_get_group_notice", {"group_id": int(group_id)}, timeout=5.0)

    async def _del_group_notice(self, group_id: str, notice_id: str) -> Dict[str, Any]:
        """Delete a group announcement."""
        return await self.call_action("_del_group_notice", {"group_id": int(group_id), "notice_id": str(notice_id)}, timeout=5.0)

    async def get_group_at_all_remain(self, group_id: str) -> Dict[str, Any]:
        """Fetch the remaining @-all count for a group."""
        return await self.call_action("get_group_at_all_remain", {"group_id": int(group_id)}, timeout=5.0)

    async def get_group_ignore_add_request(self, group_id: str) -> Dict[str, Any]:
        """Fetch the list of ignored group-add requests."""
        return await self.call_action("get_group_ignore_add_request", {"group_id": int(group_id)}, timeout=5.0)

    async def get_group_shut_list(self, group_id: str) -> list[Dict[str, Any]]:
        """Fetch the group's mute list."""
        data = await self.call_action("get_group_shut_list", {"group_id": int(group_id)}, timeout=5.0)
        return data if isinstance(data, list) else []

    # ── Group sign-in / avatar ───────────────────────────────────────────

    async def set_group_sign(self, group_id: str, sign: str = "") -> Dict[str, Any]:
        """Set the group sign-in."""
        return await self.call_action("set_group_sign", {"group_id": int(group_id), "sign": str(sign)}, timeout=5.0)

    async def send_group_sign(self, group_id: str) -> Dict[str, Any]:
        """Perform group sign-in."""
        return await self.call_action("send_group_sign", {"group_id": int(group_id)}, timeout=5.0)

    async def set_group_portrait(self, group_id: str, file: str, is_set: bool = True) -> Dict[str, Any]:
        """Set the group avatar."""
        return await self.call_action("set_group_portrait", {"group_id": int(group_id), "file": str(file), "is_set": bool(is_set)}, timeout=10.0)

    async def get_group_info_ex(self, group_id: str) -> Dict[str, Any]:
        """Fetch extended group info."""
        return await self.call_action("get_group_info_ex", {"group_id": int(group_id)}, timeout=5.0)

    # ── Essence messages ────────────────────────────────────────────────

    async def get_essence_msg_list(self, group_id: str) -> Dict[str, Any]:
        """Fetch the essence message list."""
        return await self.call_action("get_essence_msg_list", {"group_id": int(group_id)}, timeout=5.0)

    async def set_essence_msg(self, message_id: str) -> Dict[str, Any]:
        """Set an essence message."""
        return await self.call_action("set_essence_msg", {"message_id": int(message_id)}, timeout=5.0)

    async def delete_essence_msg(self, message_id: str) -> Dict[str, Any]:
        """Remove an essence message."""
        return await self.call_action("delete_essence_msg", {"message_id": int(message_id)}, timeout=5.0)

    # ── Group files ──────────────────────────────────────────────────

    async def upload_group_file(self, group_id: str, file: str, name: str = "", folder: str = "") -> Dict[str, Any]:
        """Upload a group file."""
        return await self.call_action("upload_group_file", {"group_id": int(group_id), "file": str(file), "name": str(name), "folder": str(folder)}, timeout=30.0)

    async def delete_group_file(self, group_id: str, file_id: str, busid: int = 0) -> Dict[str, Any]:
        """Delete a group file."""
        return await self.call_action("delete_group_file", {"group_id": int(group_id), "file_id": str(file_id), "busid": int(busid)}, timeout=5.0)

    async def create_group_file_folder(self, group_id: str, name: str) -> Dict[str, Any]:
        """Create a group file folder."""
        return await self.call_action("create_group_file_folder", {"group_id": int(group_id), "name": str(name)}, timeout=5.0)

    async def delete_group_folder(self, group_id: str, folder_id: str) -> Dict[str, Any]:
        """Delete a group file folder."""
        return await self.call_action("delete_group_folder", {"group_id": int(group_id), "folder_id": str(folder_id)}, timeout=5.0)

    async def get_group_file_system_info(self, group_id: str) -> Dict[str, Any]:
        """Fetch the group file system info."""
        return await self.call_action("get_group_file_system_info", {"group_id": int(group_id)}, timeout=5.0)

    async def get_group_root_files(self, group_id: str) -> Dict[str, Any]:
        """Fetch the group root-file list."""
        return await self.call_action("get_group_root_files", {"group_id": int(group_id)}, timeout=10.0)

    async def get_group_files_by_folder(self, group_id: str, folder_id: str) -> Dict[str, Any]:
        """Fetch the group folder file list."""
        return await self.call_action("get_group_files_by_folder", {"group_id": int(group_id), "folder_id": str(folder_id)}, timeout=10.0)

    async def get_group_file_url(self, group_id: str, file_id: str, busid: int = 0) -> Dict[str, Any]:
        """Fetch a group file download URL."""
        return await self.call_action("get_group_file_url", {"group_id": int(group_id), "file_id": str(file_id), "busid": int(busid)}, timeout=5.0)

    async def get_private_file_url(self, user_id: str, file_id: str) -> Dict[str, Any]:
        """Fetch a private file download URL (NapCat requires both user_id and file_id)."""
        return await self.call_action(
            "get_private_file_url",
            {"user_id": int(user_id), "file_id": str(file_id)},
            timeout=5.0,
        )

    async def upload_private_file(self, user_id: str, file: str, name: str = "") -> Dict[str, Any]:
        """Upload a private file."""
        return await self.call_action("upload_private_file", {"user_id": int(user_id), "file": str(file), "name": str(name)}, timeout=30.0)

    async def download_file(self, url: str, thread_count: int = 3, headers: Optional[list[str]] = None) -> Dict[str, Any]:
        """Download a file to local."""
        return await self.call_action("download_file", {"url": str(url), "thread_count": int(thread_count), "headers": headers or []}, timeout=60.0)

    async def get_file(self, url: str, thread_count: int = 3, headers: Optional[list[str]] = None) -> Dict[str, Any]:
        """Fetch file data."""
        return await self.call_action("get_file", {"url": str(url), "thread_count": int(thread_count), "headers": headers or []}, timeout=60.0)

    async def get_file_by_id(self, file_id: str) -> Dict[str, Any]:
        """Fetch file info by file_id via ``get_file`` (a NapCat / go-cqhttp
        extension; the v11 public API has no ``get_file``).

        Used when a group-file message has only ``file_id`` and no ``busid``, as a
        substitute for ``get_group_file_url`` (NapCat's needs a real busid; passing 0
        is rejected).
        """
        return await self.call_action("get_file", {"file_id": str(file_id)}, timeout=5.0)

    # ── AI / OCR / translation ─────────────────────────────────────────

    async def ocr_image(self, image: str) -> Dict[str, Any]:
        """OCR an image."""
        return await self.call_action("ocr_image", {"image": str(image)}, timeout=10.0)

    async def check_url_safely(self, url: str) -> Dict[str, Any]:
        """Check link safety."""
        return await self.call_action("check_url_safely", {"url": str(url)}, timeout=5.0)

    async def translate_en2zh(self, words: str) -> Dict[str, Any]:
        """Translate English to Chinese."""
        return await self.call_action("translate_en2zh", {"words": str(words)}, timeout=5.0)

    async def fetch_custom_face(self, count: int = 10) -> Dict[str, Any]:
        """Fetch the favorite-emoji list."""
        return await self.call_action("fetch_custom_face", {"count": int(count)}, timeout=5.0)

    async def fetch_emoji_like(self, message_id: str, emoji_id: str, emoji_type: str = "", set: bool = True) -> Dict[str, Any]:
        """Fetch the emoji-like list on a message."""
        return await self.call_action("fetch_emoji_like", {"message_id": int(message_id), "emoji_id": str(emoji_id), "emoji_type": str(emoji_type), "set": bool(set)}, timeout=5.0)

    async def create_collection(self, rawdata: str, brief: str = "") -> Dict[str, Any]:
        """Create a collection."""
        return await self.call_action("create_collection", {"rawdata": str(rawdata), "brief": str(brief)}, timeout=5.0)

    async def get_collection_list(self, category: int = 0) -> Dict[str, Any]:
        """Fetch the collection list."""
        return await self.call_action("get_collection_list", {"category": int(category)}, timeout=5.0)

    # ── Model display ────────────────────────────────────────────────

    async def _get_model_show(self, model: str) -> Dict[str, Any]:
        """Fetch the model-display config."""
        return await self.call_action("_get_model_show", {"model": str(model)}, timeout=5.0)

    async def _set_model_show(self, model: str, model_show: str) -> Dict[str, Any]:
        """Set the model display."""
        return await self.call_action("_set_model_show", {"model": str(model), "model_show": str(model_show)}, timeout=5.0)

    # ── NapCat extensions ─────────────────────────────────────────────

    async def ArkSharePeer(self, user_id: str, ark_json: str) -> Dict[str, Any]:
        """Share an ARK message to a friend."""
        return await self.call_action("ArkSharePeer", {"user_id": int(user_id), "ark_json": str(ark_json)}, timeout=10.0)

    async def ArkShareGroup(self, group_id: str, ark_json: str) -> Dict[str, Any]:
        """Share an ARK message to a group."""
        return await self.call_action("ArkShareGroup", {"group_id": int(group_id), "ark_json": str(ark_json)}, timeout=10.0)

    async def get_mini_app_ark(self, appid: str = "", app_type: str = "", app_path: str = "", title: str = "", desc: str = "", pic_url: str = "", jump_url: str = "", scene: int = 0) -> Dict[str, Any]:
        """Fetch a mini-app ARK message."""
        return await self.call_action("get_mini_app_ark", {"appid": str(appid), "app_type": str(app_type), "app_path": str(app_path), "title": str(title), "desc": str(desc), "pic_url": str(pic_url), "jump_url": str(jump_url), "scene": int(scene)}, timeout=10.0)

    async def nc_get_packet_status(self) -> Dict[str, Any]:
        """Fetch the packet status."""
        return await self.call_action("nc_get_packet_status", timeout=5.0)

    async def nc_get_user_status(self, user_id: str) -> Dict[str, Any]:
        """Fetch a user's status."""
        return await self.call_action("nc_get_user_status", {"user_id": int(user_id)}, timeout=5.0)

    async def nc_get_rkey(self) -> Dict[str, Any]:
        """Fetch the rkey."""
        return await self.call_action("nc_get_rkey", timeout=5.0)

    # ── AI voice ─────────────────────────────────────────────────

    async def get_ai_record(self, group_id: str, character_id: str, text: str) -> Dict[str, Any]:
        """Fetch AI voice."""
        return await self.call_action("get_ai_record", {"group_id": int(group_id), "character_id": str(character_id), "text": str(text)}, timeout=30.0)

    async def get_ai_characters(self, group_id: str, chat_type: int = 1) -> Dict[str, Any]:
        """Fetch the AI role list."""
        return await self.call_action("get_ai_characters", {"group_id": int(group_id), "chat_type": int(chat_type)}, timeout=10.0)

    async def send_group_ai_record(self, group_id: str, character_id: str, text: str) -> Dict[str, Any]:
        """Send a group-message AI voice note."""
        return await self.call_action("send_group_ai_record", {"group_id": int(group_id), "character_id": str(character_id), "text": str(text)}, timeout=30.0)

    async def group_poke(self, group_id: str, user_id: str) -> Dict[str, Any]:
        """Poke in a group (via API)."""
        return await self.call_action("group_poke", {"group_id": int(group_id), "user_id": int(user_id)}, timeout=5.0)
