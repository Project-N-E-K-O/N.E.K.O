from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

CredentialProvider = Callable[[], Awaitable[Optional[object]]]


class BiliContentService:
    def __init__(self, *, logger, credential_provider: CredentialProvider) -> None:
        self.logger = logger
        self._credential_provider = credential_provider

    def _require_sdk(self):
        try:
            from bilibili_api import comment as bili_comment
            from bilibili_api import dynamic, favorite_list, hot, rank, search, session, user
            from bilibili_api import video as bili_video
            from bilibili_api.utils.picture import Picture
        except ImportError as exc:
            raise RuntimeError("缺少 bilibili_api 依赖，无法使用 B站 内容工具。") from exc
        from types import SimpleNamespace
        return SimpleNamespace(
            comment=bili_comment,
            dynamic=dynamic,
            favorite_list=favorite_list,
            hot=hot,
            rank=rank,
            search=search,
            session=session,
            user=user,
            video=bili_video,
            Picture=Picture,
        )

    async def get_valid_credential(self):
        credential = await self._credential_provider()
        if not credential:
            raise RuntimeError("未登录B站！请先调用 bili_login 获取登录二维码。")
        return credential

    async def _resolve_video_by_keyword(self, keyword: str) -> Dict[str, Any]:
        if not keyword or not keyword.strip():
            raise RuntimeError("keyword 不能为空")
        search_result = await self.search_videos(keyword=keyword.strip(), num=1)
        videos = search_result.get("videos") or []
        if not videos:
            raise RuntimeError(f"未找到关键词对应的视频: {keyword}")
        first = videos[0] if isinstance(videos[0], dict) else {}
        bvid = first.get("bvid") if isinstance(first.get("bvid"), str) else ""
        if not bvid:
            raise RuntimeError(f"搜索结果缺少有效 bvid: {keyword}")
        return {
            "keyword": keyword.strip(),
            "matched_bvid": bvid,
            "matched_title": first.get("title") if isinstance(first.get("title"), str) else "",
        }

    def _attach_keyword_match(self, payload: Dict[str, Any], matched: Dict[str, Any]) -> Dict[str, Any]:
        payload["keyword"] = matched["keyword"]
        payload["matched_bvid"] = matched["matched_bvid"]
        if matched.get("matched_title"):
            payload["matched_title"] = matched["matched_title"]
        return payload

    async def _get_video_and_info(self, *, bvid: str):
        sdk = self._require_sdk()
        cred = await self.get_valid_credential()
        v = sdk.video.Video(bvid=bvid, credential=cred)
        info = await v.get_info()
        return v, info, cred

    async def _get_video_cid(self, *, bvid: str):
        v, info, _ = await self._get_video_and_info(bvid=bvid)
        cid = info.get("cid", 0)
        if not cid and info.get("pages"):
            cid = info["pages"][0].get("cid", 0)
        if not cid:
            raise RuntimeError("无法获取cid")
        return v, info, cid

    async def search_videos(self, keyword: str, num: int = 10, order: str = "totalrank") -> Dict[str, Any]:
        sdk = self._require_sdk()
        order_video = getattr(sdk.search, "OrderVideo", None)
        order_map = {
            "totalrank": getattr(order_video, "TOTALRANK", None),
            "click": getattr(order_video, "CLICK", None),
            "pubdate": getattr(order_video, "PUBDATE", None),
            "dm": getattr(order_video, "DM", None),
        }
        order_enum = order_map.get(order) or order_map.get("totalrank")
        search_kwargs = {
            "keyword": keyword,
            "search_type": sdk.search.SearchObjectType.VIDEO,
            "page": 1,
            "page_size": max(1, min(30, int(num or 10))),
        }
        if order_enum is not None:
            search_kwargs["order_type"] = order_enum
        result = await sdk.search.search_by_type(**search_kwargs)
        videos: List[Dict[str, Any]] = []
        for item in result.get("result", [])[: max(1, min(30, int(num or 10)))]:
            title = item.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
            videos.append({
                "bvid": item.get("bvid", ""),
                "aid": item.get("aid", 0),
                "title": title,
                "author": item.get("author", ""),
                "play": item.get("play", 0),
                "review": item.get("review", 0),
                "danmaku": item.get("video_review", 0),
                "duration": item.get("duration", ""),
                "description": item.get("description", "")[:200],
            })
        return {"keyword": keyword, "count": len(videos), "videos": videos}

    async def hot_videos(self, pn: int = 1, ps: int = 20) -> Dict[str, Any]:
        sdk = self._require_sdk()
        result = await sdk.hot.get_hot_videos(pn=pn, ps=min(max(1, ps), 50))
        videos: List[Dict[str, Any]] = []
        for item in result.get("list", []):
            stat = item.get("stat", {})
            videos.append({
                "bvid": item.get("bvid", ""),
                "title": item.get("title", ""),
                "author": item.get("owner", {}).get("name", ""),
                "play": stat.get("view", 0),
                "like": stat.get("like", 0),
                "danmaku": stat.get("danmaku", 0),
                "reply": stat.get("reply", 0),
                "desc": (item.get("desc", "") or "")[:100],
                "duration": item.get("duration", 0),
                "tname": item.get("tname", ""),
            })
        return {"page": pn, "count": len(videos), "videos": videos}

    async def hot_buzzwords(self, page_num: int = 1, page_size: int = 20) -> Dict[str, Any]:
        sdk = self._require_sdk()
        result = await sdk.hot.get_hot_buzzwords(page_num=page_num, page_size=page_size)
        buzzwords: List[Dict[str, Any]] = []
        for item in result.get("buzzwords", [])[: min(max(1, page_size), 50)]:
            buzzwords.append({
                "id": item.get("id", 0),
                "name": item.get("name", ""),
                "picture": item.get("picture", ""),
            })
        return {
            "page_num": page_num,
            "page_size": page_size,
            "count": len(buzzwords),
            "buzzwords": buzzwords,
        }

    async def weekly_hot(self, week: int = 0) -> Dict[str, Any]:
        sdk = self._require_sdk()
        if week <= 0:
            result = await sdk.hot.get_weekly_hot_videos_list()
            return {
                "week": 0,
                "count": len(result.get("list", [])),
                "list": result.get("list", []),
            }

        result = await sdk.hot.get_weekly_hot_videos(week=week)
        videos: List[Dict[str, Any]] = []
        for item in result.get("list", []):
            stat = item.get("stat", {})
            videos.append({
                "bvid": item.get("bvid", ""),
                "title": item.get("title", ""),
                "author": item.get("owner", {}).get("name", ""),
                "play": stat.get("view", 0),
                "like": stat.get("like", 0),
                "reply": stat.get("reply", 0),
                "danmaku": stat.get("danmaku", 0),
                "desc": (item.get("desc", "") or "")[:100],
                "tname": item.get("tname", ""),
            })
        return {"week": week, "count": len(videos), "videos": videos}

    async def rank_videos(self, category: str = "all", day: int = 3) -> Dict[str, Any]:
        sdk = self._require_sdk()
        type_map = {
            "all": sdk.rank.RankType.All,
            "original": sdk.rank.RankType.Original,
            "rookie": sdk.rank.RankType.Rookie,
            "douga": sdk.rank.RankType.Douga,
            "music": sdk.rank.RankType.Music,
            "dance": sdk.rank.RankType.Dance,
            "game": sdk.rank.RankType.Game,
            "knowledge": sdk.rank.RankType.Knowledge,
            "technology": sdk.rank.RankType.Technology,
            "sports": sdk.rank.RankType.Sports,
            "car": sdk.rank.RankType.Car,
            "life": sdk.rank.RankType.Life,
            "food": sdk.rank.RankType.Food,
            "animal": sdk.rank.RankType.Animal,
            "fashion": sdk.rank.RankType.Fashion,
            "ent": sdk.rank.RankType.Ent,
            "cinephile": sdk.rank.RankType.Cinephile,
        }
        day_map = {3: sdk.rank.RankDayType.THREE_DAY, 7: sdk.rank.RankDayType.WEEK}
        rank_type = type_map.get((category or "all").lower(), sdk.rank.RankType.All)
        rank_day = day_map.get(day, sdk.rank.RankDayType.THREE_DAY)
        result = await sdk.rank.get_rank(type_=rank_type, day=rank_day)
        videos: List[Dict[str, Any]] = []
        for item in result.get("list", []):
            stat = item.get("stat", {})
            videos.append({
                "bvid": item.get("bvid", ""),
                "title": item.get("title", ""),
                "author": item.get("owner", {}).get("name", ""),
                "play": stat.get("view", 0),
                "like": stat.get("like", 0),
                "coin": stat.get("coin", 0),
                "score": item.get("score", 0),
                "tname": item.get("tname", ""),
            })
        return {"category": category, "day": day, "count": len(videos), "videos": videos}

    async def video_info(self, *, bvid: Optional[str] = None, aid: Optional[int] = None) -> Dict[str, Any]:
        sdk = self._require_sdk()
        cred = await self.get_valid_credential()
        if bvid:
            v = sdk.video.Video(bvid=bvid, credential=cred)
        elif aid:
            v = sdk.video.Video(aid=aid, credential=cred)
        else:
            raise RuntimeError("bvid or aid is required")
        info = await v.get_info()
        stat = info.get("stat", {})
        return {
            "bvid": info.get("bvid"),
            "aid": info.get("aid"),
            "title": info.get("title"),
            "description": info.get("desc"),
            "author": info.get("owner", {}).get("name"),
            "duration": info.get("duration"),
            "pages": len(info.get("pages", [])),
            "tags": [t.get("tag_name") for t in info.get("tag", []) if t.get("tag_name")],
            "stat": {
                "view": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "reply": stat.get("reply", 0),
                "favorite": stat.get("favorite", 0),
                "coin": stat.get("coin", 0),
                "like": stat.get("like", 0),
                "share": stat.get("share", 0),
            },
        }

    async def comments(self, *, bvid: str, num: int = 30) -> Dict[str, Any]:
        sdk = self._require_sdk()
        cred = await self.get_valid_credential()
        v = sdk.video.Video(bvid=bvid, credential=cred)
        info = await v.get_info()
        aid = info["aid"]
        max_comments = max(1, min(int(num or 30), 100))
        comments: List[Dict[str, Any]] = []
        page = 1
        max_pages = max(1, min((max_comments + 19) // 20 + 1, 5))
        while len(comments) < max_comments and page <= max_pages:
            try:
                resp = await sdk.comment.get_comments(
                    oid=aid,
                    type_=sdk.comment.CommentResourceType.VIDEO,
                    page_index=page,
                    order=sdk.comment.OrderType.LIKE,
                    credential=cred,
                )
                replies = resp.get("replies") or []
                if not replies:
                    break
                for r in replies:
                    member = r.get("member", {})
                    content = r.get("content", {})
                    item: Dict[str, Any] = {
                        "rpid": r.get("rpid", 0),
                        "user": member.get("uname", ""),
                        "content": content.get("message", ""),
                        "like": r.get("like", 0),
                        "reply_count": r.get("rcount", 0),
                        "time": r.get("ctime", 0),
                    }
                    sub_replies = []
                    for sub in (r.get("replies") or [])[:2]:
                        sub_replies.append({
                            "user": sub.get("member", {}).get("uname", ""),
                            "content": sub.get("content", {}).get("message", ""),
                            "like": sub.get("like", 0),
                        })
                    if sub_replies:
                        item["top_replies"] = sub_replies
                    comments.append(item)
                    if len(comments) >= max_comments:
                        break
                page += 1
            except Exception as e:
                self.logger.debug("获取评论分页失败 page=%d: %s", page, e)
                break
        return {"bvid": bvid, "count": len(comments), "comments": comments}

    async def comments_by_keyword(self, keyword: str, num: int = 30) -> Dict[str, Any]:
        matched = await self._resolve_video_by_keyword(keyword)
        payload = await self.comments(bvid=matched["matched_bvid"], num=num)
        return self._attach_keyword_match(payload, matched)

    async def subtitle(self, *, bvid: str) -> Dict[str, Any]:
        import aiohttp

        v, info, cid = await self._get_video_cid(bvid=bvid)
        subtitle_list = await v.get_subtitle(cid=cid)
        subtitles = subtitle_list.get("subtitles", [])
        if not subtitles:
            return {
                "bvid": bvid,
                "title": info.get("title", ""),
                "message": "该视频没有字幕",
                "segments": 0,
                "text": "",
            }
        # 字幕语言优先级：人工中文 > 简体中文 > AI 中文
        target = None
        for prio in ["zh-CN", "zh", "ai-zh"]:
            for subtitle in subtitles:
                if subtitle.get("lan") == prio:
                    target = subtitle
                    break
            if target:
                break
        if not target:
            target = subtitles[0]
        sub_url = target.get("subtitle_url", "")
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(sub_url) as resp:
                resp.raise_for_status()
                sub_data = await resp.json(content_type=None)
        texts = [item.get("content", "") for item in sub_data.get("body", [])]
        return {
            "bvid": bvid,
            "title": info.get("title", ""),
            "language": target.get("lan_doc", ""),
            "segments": len(texts),
            "text": "\n".join(texts),
        }

    async def subtitle_by_keyword(self, keyword: str) -> Dict[str, Any]:
        matched = await self._resolve_video_by_keyword(keyword)
        payload = await self.subtitle(bvid=matched["matched_bvid"])
        return self._attach_keyword_match(payload, matched)

    async def danmaku(self, *, bvid: str, num: int = 100) -> Dict[str, Any]:
        v, _, _ = await self._get_video_and_info(bvid=bvid)
        danmakus = await v.get_danmakus(page_index=0)
        count = max(1, min(int(num or 100), 1000))
        result: List[Dict[str, Any]] = []
        for item in danmakus[:count]:
            result.append({"text": item.text, "time": item.dm_time})
        return {"bvid": bvid, "count": len(result), "danmakus": result}

    async def danmaku_by_keyword(self, keyword: str, num: int = 100) -> Dict[str, Any]:
        matched = await self._resolve_video_by_keyword(keyword)
        payload = await self.danmaku(bvid=matched["matched_bvid"], num=num)
        return self._attach_keyword_match(payload, matched)

    async def user_info(self, uid: int) -> Dict[str, Any]:
        sdk = self._require_sdk()
        cred = await self.get_valid_credential()
        target = sdk.user.User(uid=uid, credential=cred)
        info = await target.get_user_info()
        up_stat: Dict[str, Any] = {}
        relation: Dict[str, Any] = {}
        try:
            up_stat = await target.get_up_stat()
        except Exception as e:
            self.logger.debug("获取 up_stat 失败 uid=%d: %s", uid, e)
        try:
            relation = await target.get_relation_info()
        except Exception as e:
            self.logger.debug("获取 relation_info 失败 uid=%d: %s", uid, e)
        return {
            "uid": uid,
            "name": info.get("name", ""),
            "sign": info.get("sign", ""),
            "level": info.get("level", 0),
            "face": info.get("face", ""),
            "fans": relation.get("follower", info.get("follower", 0)),
            "following": relation.get("following", info.get("following", 0)),
            "likes": up_stat.get("likes", 0),
            "archive_view": up_stat.get("archive", {}).get("view", 0),
            "article_view": up_stat.get("article", {}).get("view", 0),
            "is_senior_member": info.get("is_senior_member", 0),
            "top_photo": info.get("top_photo", ""),
        }

    async def user_videos(self, uid: int, pn: int = 1, ps: int = 30, order: str = "pubdate", keyword: str = "") -> Dict[str, Any]:
        sdk = self._require_sdk()
        cred = await self.get_valid_credential()
        target = sdk.user.User(uid=uid, credential=cred)
        order_map = {
            "pubdate": sdk.user.VideoOrder.PUBDATE,
            "click": sdk.user.VideoOrder.VIEW,
            "stow": sdk.user.VideoOrder.FAVORITE,
        }
        order_enum = order_map.get(order, sdk.user.VideoOrder.PUBDATE)
        result = await target.get_videos(pn=pn, ps=ps, order=order_enum, keyword=keyword)
        videos: List[Dict[str, Any]] = []
        for item in result.get("list", {}).get("vlist", []):
            videos.append({
                "bvid": item.get("bvid", ""),
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "length": item.get("length", ""),
                "play": item.get("play", 0),
                "comment": item.get("comment", 0),
                "created": item.get("created", 0),
            })
        return {"uid": uid, "page": pn, "count": len(videos), "videos": videos}

    async def favorite_lists(self, uid: int = 0) -> Dict[str, Any]:
        sdk = self._require_sdk()
        cred = await self.get_valid_credential()
        target_uid = uid
        if target_uid == 0:
            try:
                target_uid = int(cred.dedeuserid)
            except Exception as exc:
                raise RuntimeError("无法从当前凭证解析 UID") from exc
        result = await sdk.favorite_list.get_video_favorite_list(uid=target_uid, credential=cred)
        fav_lists: List[Dict[str, Any]] = []
        for item in result.get("list", []) or []:
            fav_lists.append({
                "id": item.get("id", 0),
                "title": item.get("title", ""),
                "media_count": item.get("media_count", 0),
                "fav_state": item.get("fav_state", 0),
            })
        return {"uid": target_uid, "count": len(fav_lists), "lists": fav_lists}

    async def favorite_content(self, media_id: int, page: int = 1, keyword: str = "") -> Dict[str, Any]:
        sdk = self._require_sdk()
        cred = await self.get_valid_credential()
        result = await sdk.favorite_list.get_video_favorite_list_content(
            media_id=media_id,
            page=page,
            keyword=keyword if keyword else None,
            credential=cred,
        )
        medias: List[Dict[str, Any]] = []
        for item in result.get("medias", []) or []:
            medias.append({
                "bvid": item.get("bvid", ""),
                "title": item.get("title", ""),
                "play": item.get("cnt_info", {}).get("play", 0),
                "collect": item.get("cnt_info", {}).get("collect", 0),
                "author": item.get("upper", {}).get("name", ""),
                "duration": item.get("duration", 0),
                "fav_time": item.get("fav_time", 0),
            })
        return {
            "media_id": media_id,
            "page": page,
            "has_more": result.get("has_more", False),
            "count": len(medias),
            "medias": medias,
        }

    async def reply(self, *, bvid: str, text: str, rpid: int = 0, root: int = 0) -> Dict[str, Any]:
        sdk = self._require_sdk()
        if not text or not text.strip():
            raise RuntimeError("text 不能为空")
        _, info, cred = await self._get_video_and_info(bvid=bvid)
        aid = info["aid"]
        if rpid == 0:
            result = await sdk.comment.send_comment(
                text=text.strip(),
                oid=aid,
                type_=sdk.comment.CommentResourceType.VIDEO,
                credential=cred,
            )
        else:
            actual_root = root if root != 0 else rpid
            result = await sdk.comment.send_comment(
                text=text.strip(),
                oid=aid,
                type_=sdk.comment.CommentResourceType.VIDEO,
                root=actual_root,
                parent=rpid,
                credential=cred,
            )
        return {"success": True, "message": "评论发送成功", "data": result if isinstance(result, dict) else str(result)}

    async def send_dynamic(self, text: str, images: Optional[List[str]] = None, topic_id: int = 0, schedule_time: int = 0) -> Dict[str, Any]:
        sdk = self._require_sdk()
        if not text or not text.strip():
            raise RuntimeError("text 不能为空")
        cred = await self.get_valid_credential()
        dyn = sdk.dynamic.BuildDynamic.empty()
        dyn.add_plain_text(text.strip())
        if images:
            import os

            for img_path in images[:9]:
                if not img_path or not img_path.strip():
                    continue
                img_path = img_path.strip()
                if img_path.startswith(("http://", "https://")):
                    pic = await sdk.Picture.load_url(img_path)
                else:
                    if not os.path.isfile(img_path):
                        raise RuntimeError(f"图片文件不存在: {img_path}")
                    pic = sdk.Picture.from_file(img_path)
                dyn.add_image(pic)
        if topic_id:
            dyn.set_topic(topic_id)
        if schedule_time > 0:
            dyn.set_send_time(schedule_time)
        result = await sdk.dynamic.send_dynamic(info=dyn, credential=cred)
        return {"success": True, "message": "动态发布成功", "data": result if isinstance(result, dict) else str(result)}

    async def send_message(self, receiver_uid: int, text: str) -> Dict[str, Any]:
        sdk = self._require_sdk()
        if not text or not text.strip():
            raise RuntimeError("text 不能为空")
        cred = await self.get_valid_credential()
        result = await sdk.session.send_msg(
            credential=cred,
            receiver_id=receiver_uid,
            msg_type=sdk.session.EventType.TEXT,
            content=text.strip(),
        )
        return {
            "success": True,
            "message": f"私信已发送给UID:{receiver_uid}",
            "data": result if isinstance(result, dict) else str(result),
        }
