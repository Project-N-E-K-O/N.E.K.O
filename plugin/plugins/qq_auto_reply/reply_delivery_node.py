from __future__ import annotations

import asyncio
import random
from typing import Any

from .pipeline_models import QQDeliveryPlan, QQDeliveryResult, QQMessageBlock


class QQReplyDeliveryNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def deliver(self, plan: QQDeliveryPlan | None) -> QQDeliveryResult | None:
        if not plan or not plan.blocks:
            return None

        blocks = plan.blocks
        first_text = ""
        for i, block in enumerate(blocks):
            if i > 0:
                # 块间延迟：模拟真人打字间隔
                await asyncio.sleep(random.uniform(2.0, 5.0))

            if block.rps: await self._send_rps(plan); continue
            if block.dice: await self._send_dice(plan); continue
            if block.contact_type and block.contact_id: await self._send_contact(plan, block); continue
            if block.music_type: await self._send_music(plan, block); continue
            if block.mface_id: await self._send_mface(plan, block); continue
            if block.file_path: await self._send_file(plan, block); continue
            if block.json_data: await self._send_json(plan, block); continue
            if block.ark: await self._send_ark(plan, block); continue

            # keyboard 无 text: 选项当文本发。有 text: 落到 _send_text 一起发
            if block.keyboard and not block.text:
                await self._send_keyboard_text(plan, block, f"[选项] {block.keyboard}")
                continue

            if block.poke:
                await self._send_poke(plan, block)
                continue

            if block.record:
                await self._send_record(plan, block)
                continue

            # 文本：先发文字（如果有），再发附属媒体（sticker 跟在文字后面）
            if i == 0:
                first_text = block.text or ""
            if block.text:
                await self._send_text(plan, block, block.text)
            if block.sticker:
                await self._send_sticker(plan, block)

        return QQDeliveryResult(
            delivered=True,
            target_type=plan.target_type,
            target_id=plan.target_id,
            reply_text=first_text,
        )

    @staticmethod
    def _build_segments(block: QQMessageBlock) -> list[dict[str, Any]]:
        """构建 OneBot 消息段列表（reply/at 用独立 segment，不用 CQ 码）。"""
        segs: list[dict[str, Any]] = []
        if block.reply_to:
            segs.append({"type": "reply", "data": {"id": block.reply_to}})
        if block.at_user:
            segs.append({"type": "at", "data": {"qq": block.at_user}})
        if block.text:
            segs.append({"type": "text", "data": {"text": block.text}})
        return segs

    async def _send_text(self, plan: QQDeliveryPlan, block: QQMessageBlock, text: str) -> None:
        segs = self._build_segments(block)
        if not segs:
            return
        # keyboard 和 text 一起发：私聊追加到文本，群聊走原生 keyboard 参数
        if block.keyboard and plan.target_type != "group":
            segs.append({"type": "text", "data": {"text": f"\n[选项] {block.keyboard}"}})
        self.plugin._emit_log("DEBUG", f"[Delivery] 发送文本: target={plan.target_type}:{plan.target_id} text={text[:40]} segs_count={len(segs)}")
        mode = self.plugin._get_reply_mode()
        if mode == "voice" and block.text:
            voice_text = block.text
            if block.keyboard:
                voice_text += f"\n[选项] {block.keyboard}"
            if plan.target_type == "group":
                await self.plugin._deliver_group_reply(
                    plan.target_id, voice_text,
                    reply_message_id=block.reply_to or "",
                    at_user_id=block.at_user or "",
                    fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure,
                )
            else:
                await self.plugin._deliver_private_reply(plan.target_id, voice_text, fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure)
        elif plan.target_type == "group":
            await self.plugin.qq_client.send_group_message_segments(plan.target_id, segs, keyboard=block.keyboard)
        else:
            await self.plugin.qq_client.send_private_message_segments(plan.target_id, segs)

    async def _send_sticker(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> None:
        sticker_path = self.plugin._resolve_sticker_path(block.sticker)
        if not sticker_path:
            return
        if plan.target_type == "group":
            await self.plugin.qq_client.send_group_image(plan.target_id, sticker_path)
        else:
            await self.plugin.qq_client.send_private_image(plan.target_id, sticker_path)

    async def _send_poke(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> None:
        if plan.target_type != "group" or not block.poke:
            return
        # 冷却：同一群每 30 秒最多戳一次，避免刷屏
        now = __import__("time").time()
        key = f"poke_out:{plan.target_id}"
        last = getattr(self, "_last_poke_out", {}).get(key, 0)
        if now - last < 30:
            self.plugin._emit_log("INFO", f"戳一戳冷却中，跳过 (群{plan.target_id})")
            return
        if not hasattr(self, "_last_poke_out"):
            self._last_poke_out = {}
        self._last_poke_out[key] = now
        await self.plugin.qq_client.send_group_poke(plan.target_id, block.poke)

    async def _send_record(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> None:
        if not block.record:
            return
        self.plugin._emit_log("DEBUG", f"[Delivery] 发送语音: target={plan.target_type}:{plan.target_id} record_text={block.record[:40]}")
        try:
            file_uri, _ = await self.plugin.voice_reply_service.synthesize_reply_voice_file(block.record)
            self.plugin._emit_log("DEBUG", f"[Delivery] 语音合成完成: uri={file_uri[:60] if file_uri else 'empty'}")
            if plan.target_type == "group":
                await self.plugin.qq_client.send_group_record(
                    plan.target_id, file_uri,
                    reply_message_id=block.reply_to or "",
                    at_user_id=block.at_user or "",
                )
            else:
                await self.plugin.qq_client.send_private_record(plan.target_id, file_uri)
            self.plugin._emit_log("DEBUG", f"[Delivery] 语音已发送")
        except Exception:
            self.plugin.logger.warning("语音发送失败", exc_info=True)
            if plan.fallback_to_text_on_voice_failure and block.record:
                self.plugin._emit_log("INFO", f"[Delivery] 语音失败，fallback 文本: {block.record[:40]}")
                segs = self._build_segments(block)
                segs.append({"type": "text", "data": {"text": block.record}})
                if plan.target_type == "group":
                    await self.plugin.qq_client.send_group_message_segments(plan.target_id, segs)
                else:
                    await self.plugin.qq_client.send_private_message_segments(plan.target_id, segs)

    async def _send_rps(self, plan): seg = {"type":"rps","data":{}}; (await self.plugin.qq_client.send_group_message_segments(plan.target_id,[seg]) if plan.target_type=="group" else await self.plugin.qq_client.send_private_message_segments(plan.target_id,[seg]))
    async def _send_dice(self, plan): seg = {"type":"dice","data":{}}; (await self.plugin.qq_client.send_group_message_segments(plan.target_id,[seg]) if plan.target_type=="group" else await self.plugin.qq_client.send_private_message_segments(plan.target_id,[seg]))
    async def _send_contact(self, plan, block):
        seg = {"type":"contact","data":{"type":block.contact_type,"id":block.contact_id}}
        if plan.target_type=="group": await self.plugin.qq_client.send_group_message_segments(plan.target_id,[seg])
        else: await self.plugin.qq_client.send_private_message_segments(plan.target_id,[seg])
    async def _send_music(self, plan, block):
        data = {"type":block.music_type}
        if block.music_type=="custom":
            for k in ["url","audio","title","singer","image"]:
                v = getattr(block, f"music_{k}", "") or ""
                if v: data[k] = v
        else: data["id"] = block.music_id
        seg = {"type":"music","data":data}
        if plan.target_type=="group": await self.plugin.qq_client.send_group_message_segments(plan.target_id,[seg])
        else: await self.plugin.qq_client.send_private_message_segments(plan.target_id,[seg])
    async def _send_mface(self, plan, block):
        data = {"emoji_id":block.mface_id}
        if block.mface_pkg: data["emoji_package_id"]=block.mface_pkg
        if block.mface_key: data["key"]=block.mface_key
        seg = {"type":"mface","data":data}
        if plan.target_type=="group": await self.plugin.qq_client.send_group_message_segments(plan.target_id,[seg])
        else: await self.plugin.qq_client.send_private_message_segments(plan.target_id,[seg])
    async def _send_file(self, plan, block):
        data = {"file":block.file_path or "empty"}
        if block.file_name: data["name"]=block.file_name
        seg = {"type":"file","data":data}
        if plan.target_type=="group": await self.plugin.qq_client.send_group_message_segments(plan.target_id,[seg])
        else: await self.plugin.qq_client.send_private_message_segments(plan.target_id,[seg])
    async def _send_json(self, plan, block):
        seg = {"type":"json","data":{"data":block.json_data}}
        if plan.target_type=="group": await self.plugin.qq_client.send_group_message_segments(plan.target_id,[seg])
        else: await self.plugin.qq_client.send_private_message_segments(plan.target_id,[seg])
    async def _send_keyboard_text(self, plan, block, text):
        """纯 keyboard 无 text 时的降级：把选项当成文本发送。"""
        if plan.target_type=="group":
            await self.plugin.qq_client.send_group_message(plan.target_id, text)
        else:
            await self.plugin.qq_client.send_message(plan.target_id, text)
    async def _send_ark(self, plan, block):
        """发送 Ark 卡片。NapCat 不支持，降级为文本。"""
        from .qq_open_plat import QQOpenPlatformConnection
        title = block.ark.get("title", "")
        desc = block.ark.get("desc", "")
        btn = block.ark.get("btn", "")
        url = block.ark.get("url", "")
        pic = block.ark.get("pic", "")
        body = block.ark.get("_body", title or desc or "卡片")
        if not isinstance(self.plugin.qq_client, QQOpenPlatformConnection):
            fallback = body or title or desc or ""
            if fallback:
                if plan.target_type=="group":
                    await self.plugin.qq_client.send_group_message(plan.target_id, fallback)
                else:
                    await self.plugin.qq_client.send_message(plan.target_id, fallback)
            return
        if plan.target_type != "group":
            # 私聊不支持 Ark，降级为文本
            fallback = body or title or desc or ""
            if fallback:
                await self.plugin.qq_client.send_message(plan.target_id, fallback)
            return
        try:
            await self.plugin.qq_client._ensure_token()
            ark_obj: dict[str, Any] = {"msg_type": 10}
            if title:
                ark_obj["ark"] = {"template_id": 37, "kv": [
                    {"key": "#PROMPT#", "value": body},
                    {"key": "#TITLE#", "value": title},
                    {"key": "#DESC#", "value": desc or body},
                ]}
                if pic: ark_obj["ark"]["kv"].append({"key": "#IMGPATH#", "value": pic})
            else:
                ark_obj["ark"] = {"template_id": 23, "kv": [
                    {"key": "#TITLE#", "value": body},
                    {"key": "#DESC#", "value": desc},
                ]}
                if pic: ark_obj["ark"]["kv"].append({"key": "#IMG#", "value": pic})
            if btn: ark_obj["ark"]["kv"].append({"key": "#SUBTITLE#", "value": btn})
            if url: ark_obj["ark"]["kv"].append({"key": "#URL#", "value": url})
            r = await self.plugin.qq_client._http.post(
                f"{self.plugin.qq_client._API_BASE}/v2/groups/{plan.target_id}/messages",
                json=ark_obj,
                headers=self.plugin.qq_client._auth_headers(),
            )
            data = r.json()
            if not data.get("id"): self.plugin.logger.warning(f"[Ark] 发送失败: {data}")
        except Exception as e:
            self.plugin.logger.warning(f"[Ark] 发送失败: {e}")
