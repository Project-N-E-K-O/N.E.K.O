"""
本地规则摘要引擎

功能：
- 基于模板的摘要生成
- 关键词提取和话题聚类
- 高价值事件筛选
- 回应建议生成
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter, defaultdict
import jieba
import jieba.analyse

from ..aggregator import AggregatedEvent


@dataclass
class SummaryResult:
    """摘要结果"""
    summary_text: str                    # 自然语言摘要
    highlights: List[Dict[str, Any]]     # 高亮事件
    topics: List[str]                    # 话题聚类
    suggestions: List[str]               # 回应建议
    priority: int                        # 推送优先级 (1-10)
    metadata: Dict[str, Any]             # 元数据


class LocalEngine:
    """本地规则摘要引擎"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化本地引擎
        
        Args:
            config: 配置参数
        """
        self.config = config or {}
        
        # 关键词提取配置
        self.keyword_config = {
            "top_n": 5,                      # 提取前N个关键词
            "with_weight": False,            # 是否带权重
            "allow_pos": ("n", "v", "a"),    # 允许的词性
            "stopwords_path": None,          # 停用词文件路径
        }
        
        # 模板配置
        self.templates = {
            "normal": """
过去{period}秒的直播间动态：

✨ 高亮事件：
{highlights}

🎁 礼物汇总：
{gift_summary}

💬 热门话题：
{topics}

💡 建议回应：
{suggestions}
""",
            "high_activity": """
📈 直播间活跃度很高！过去{period}秒：

🔥 核心互动：
{highlights}

💰 消费情况：
{gift_summary}

🎯 观众关注：
{topics}

🌟 推荐回应：
{suggestions}
""",
            "low_activity": """
📊 过去{period}秒的直播间情况：

👥 观众互动：
{highlights}

🎁 收到的支持：
{gift_summary}

💭 聊天内容：
{topics}

🤔 可以这样回应：
{suggestions}
"""
        }
        
        # 初始化jieba
        self._init_jieba()
        
        # 加载停用词
        self.stopwords = self._load_stopwords()
    
    def _init_jieba(self) -> None:
        """初始化jieba分词"""
        # 添加直播领域自定义词典
        live_words = [
            "直播间", "弹幕", "礼物", "SC", "SuperChat", "总督", "提督", "舰长",
            "粉丝团", "老爷", "互动", "回复", "感谢", "可爱", "唱歌", "跳舞"
        ]
        
        for word in live_words:
            jieba.add_word(word, freq=1000)
    
    def _load_stopwords(self) -> set:
        """加载停用词"""
        stopwords = set()
        
        # 内置停用词
        builtin_stopwords = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
            "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
            "自己", "这", "那", "啊", "呢", "吧", "嗯", "哦", "哈", "呵", "嘿"
        }
        stopwords.update(builtin_stopwords)
        
        # 从文件加载停用词
        stopwords_path = self.keyword_config.get("stopwords_path")
        if stopwords_path:
            try:
                with open(stopwords_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        word = line.strip()
                        if word:
                            stopwords.add(word)
            except Exception:
                pass
        
        return stopwords
    
    async def summarize(
        self, 
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int = 30
    ) -> SummaryResult:
        """
        生成摘要
        
        Args:
            events: 聚合事件列表
            room_id: 直播间ID
            period_sec: 聚合周期（秒）
        
        Returns:
            SummaryResult: 摘要结果
        """
        if not events:
            return self._generate_empty_summary(room_id, period_sec)
        
        # 1. 统计分析
        stats = self._calculate_statistics(events)
        
        # 2. 提取高亮事件
        highlights = self._extract_highlights(events, stats)
        
        # 3. 生成礼物汇总
        gift_summary = self._generate_gift_summary(events)
        
        # 4. 提取话题
        topics = self._extract_topics(events)
        
        # 5. 生成回应建议
        suggestions = self._generate_suggestions(events, highlights, topics)
        
        # 6. 选择模板并生成摘要
        template_type = self._select_template(stats)
        summary_text = self._fill_template(
            template_type, period_sec, highlights, gift_summary, topics, suggestions
        )
        
        # 7. 计算优先级
        priority = self._calculate_priority(stats, highlights)
        
        return SummaryResult(
            summary_text=summary_text,
            highlights=highlights,
            topics=topics,
            suggestions=suggestions,
            priority=priority,
            metadata={
                "room_id": room_id,
                "period_sec": period_sec,
                "event_count": len(events),
                "engine": "local",
                "generated_at": time.time()
            }
        )
    
    def _calculate_statistics(self, events: List[AggregatedEvent]) -> Dict[str, Any]:
        """计算统计信息"""
        stats = {
            "total_events": len(events),
            "danmaku_count": 0,
            "gift_count": 0,
            "sc_count": 0,
            "guard_count": 0,
            "total_battery": 0,
            "total_rmb": 0.0,
            "unique_users": set(),
            "high_value_users": set(),
            "event_types": Counter(),
            "user_levels": Counter(),
        }
        
        for event in events:
            stats["event_types"][event.type] += 1
            
            if event.type == "danmaku":
                stats["danmaku_count"] += 1
            elif event.type == "gift":
                stats["gift_count"] += 1
                stats["total_battery"] += event.battery
                stats["total_rmb"] += event.rmb_value
            elif event.type == "superchat":
                stats["sc_count"] += 1
                stats["total_battery"] += event.battery
                stats["total_rmb"] += event.rmb_value
            elif event.type == "guard":
                stats["guard_count"] += 1
                stats["total_battery"] += event.battery
                stats["total_rmb"] += event.rmb_value
            
            stats["unique_users"].add(event.uid)
            stats["user_levels"][event.user_level] += 1
            
            if event.is_high_value or event.guard_level > 0:
                stats["high_value_users"].add(event.uid)
        
        stats["unique_users_count"] = len(stats["unique_users"])
        stats["high_value_users_count"] = len(stats["high_value_users"])
        
        return stats
    
    def _extract_highlights(
        self, 
        events: List[AggregatedEvent], 
        stats: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """提取高亮事件"""
        highlights = []
        
        # 按优先级排序
        sorted_events = sorted(events, key=lambda x: x.priority, reverse=True)
        
        # 提取SC事件（全部保留）
        sc_events = [e for e in sorted_events if e.type == "superchat"]
        for event in sc_events[:3]:  # 最多3条SC
            highlights.append({
                "user": event.name,
                "action": f"发送了{event.price}元SC",
                "content": event.content or "",
                "suggest": "点名感谢并回应SC内容",
                "priority": event.priority
            })
        
        # 提取高价值礼物事件
        high_value_gifts = [
            e for e in sorted_events 
            if e.type == "gift" and e.is_high_value
        ]
        for event in high_value_gifts[:3]:  # 最多3个高价值礼物
            highlights.append({
                "user": event.name,
                "action": f"送了{event.gift_num}个{event.gift_name}（约{event.rmb_value:.1f}元）",
                "content": "",
                "suggest": "感谢送礼，可以提及礼物名称",
                "priority": event.priority
            })
        
        # 提取大航海事件
        guard_events = [e for e in sorted_events if e.type == "guard"]
        for event in guard_events[:2]:  # 最多2个大航海
            guard_names = {1: "总督", 2: "提督", 3: "舰长"}
            guard_name = guard_names.get(event.guard_level, "舰长")
            highlights.append({
                "user": event.name,
                "action": f"开通了{guard_name}（{event.rmb_value:.0f}元）",
                "content": "",
                "suggest": "热烈欢迎并感谢开通大航海",
                "priority": event.priority
            })
        
        # 提取高价值弹幕（提问或有意义的内容）
        high_value_danmaku = [
            e for e in sorted_events 
            if e.type == "danmaku" and e.priority >= 50
        ]
        for event in high_value_danmaku[:5]:  # 最多5条高价值弹幕
            is_question = self._is_question(event.content)
            highlight_type = "提问" if is_question else "发言"
            
            highlights.append({
                "user": event.name,
                "action": highlight_type,
                "content": event.content or "",
                "suggest": "回应问题" if is_question else "接话互动",
                "priority": event.priority
            })
        
        # 按优先级排序并限制数量
        highlights.sort(key=lambda x: x["priority"], reverse=True)
        return highlights[:8]  # 最多8个高亮事件
    
    def _generate_gift_summary(self, events: List[AggregatedEvent]) -> str:
        """生成礼物汇总"""
        gift_events = [e for e in events if e.type == "gift"]
        sc_events = [e for e in events if e.type == "superchat"]
        guard_events = [e for e in events if e.type == "guard"]
        
        if not any([gift_events, sc_events, guard_events]):
            return "暂无礼物"
        
        lines = []
        
        # SC汇总
        if sc_events:
            sc_count = len(sc_events)
            sc_total = sum(e.price for e in sc_events)
            lines.append(f"SC: {sc_count}条，共{sc_total}元")
        
        # 大航海汇总
        if guard_events:
            guard_counts = Counter(e.guard_level for e in guard_events)
            guard_names = {1: "总督", 2: "提督", 3: "舰长"}
            
            guard_lines = []
            for level, count in guard_counts.items():
                name = guard_names.get(level, "舰长")
                guard_lines.append(f"{name}×{count}")
            
            if guard_lines:
                lines.append(f"大航海: {', '.join(guard_lines)}")
        
        # 礼物汇总
        if gift_events:
            gift_total = sum(e.rmb_value for e in gift_events)
            gift_count = sum(e.gift_num for e in gift_events)
            lines.append(f"礼物: {gift_count}个，约{gift_total:.1f}元")
        
        return "，".join(lines)
    
    def _extract_topics(self, events: List[AggregatedEvent]) -> List[str]:
        """提取话题"""
        # 收集所有文本内容
        texts = []
        for event in events:
            if event.content and event.type == "danmaku":
                texts.append(event.content)
        
        if not texts:
            return ["暂无话题"]
        
        # 合并文本
        all_text = " ".join(texts)
        
        # 使用jieba提取关键词
        try:
            keywords = jieba.analyse.extract_tags(
                all_text,
                topK=self.keyword_config["top_n"],
                withWeight=self.keyword_config["with_weight"],
                allowPOS=self.keyword_config["allow_pos"]
            )
            
            # 过滤停用词
            filtered_keywords = [
                kw for kw in keywords 
                if kw not in self.stopwords and len(kw) > 1
            ]
            
            return filtered_keywords[:5]  # 最多5个话题
        
        except Exception:
            # 降级：使用简单词频统计
            words = []
            for text in texts:
                words.extend(jieba.lcut(text))
            
            # 过滤停用词和单字
            filtered_words = [
                w for w in words 
                if w not in self.stopwords and len(w) > 1
            ]
            
            word_counts = Counter(filtered_words)
            top_words = [word for word, _ in word_counts.most_common(5)]
            
            return top_words if top_words else ["聊天互动"]
    
    def _generate_suggestions(
        self, 
        events: List[AggregatedEvent],
        highlights: List[Dict[str, Any]],
        topics: List[str]
    ) -> List[str]:
        """生成回应建议"""
        suggestions = []
        
        # 基于高亮事件的建议
        for highlight in highlights[:3]:
            if "SC" in highlight["action"]:
                suggestions.append(f"感谢{highlight['user']}的SC，回应: {highlight['content'][:20]}...")
            elif "开通" in highlight["action"]:
                suggestions.append(f"欢迎{highlight['user']}{highlight['action'].split('了')[1]}！")
            elif highlight.get("suggest"):
                suggestions.append(highlight["suggest"])
        
        # 基于话题的建议
        if topics and topics[0] != "暂无话题":
            suggestions.append(f"可以聊聊关于{topics[0]}的话题")
        
        # 通用建议
        if len(events) > 20:
            suggestions.append("直播间很活跃，多和观众互动")
        elif len(events) < 5:
            suggestions.append("观众互动较少，可以主动发起话题")
        
        # 去重并限制数量
        unique_suggestions = []
        seen = set()
        for s in suggestions:
            if s not in seen:
                unique_suggestions.append(s)
                seen.add(s)
        
        return unique_suggestions[:3]  # 最多3条建议
    
    def _select_template(self, stats: Dict[str, Any]) -> str:
        """选择摘要模板"""
        total_events = stats["total_events"]
        high_value_count = stats["high_value_users_count"]
        
        if total_events > 30 or high_value_count > 3:
            return "high_activity"
        elif total_events < 10:
            return "low_activity"
        else:
            return "normal"
    
    def _fill_template(
        self,
        template_type: str,
        period_sec: int,
        highlights: List[Dict[str, Any]],
        gift_summary: str,
        topics: List[str],
        suggestions: List[str]
    ) -> str:
        """填充模板"""
        template = self.templates.get(template_type, self.templates["normal"])
        
        # 格式化高亮事件
        highlights_text = ""
        for i, h in enumerate(highlights[:5], 1):
            content_preview = h["content"][:30] + "..." if len(h["content"]) > 30 else h["content"]
            highlights_text += f"{i}. {h['user']} {h['action']}"
            if content_preview:
                highlights_text += f": {content_preview}"
            highlights_text += "\n"
        
        if not highlights_text:
            highlights_text = "暂无特别高亮事件\n"
        
        # 格式化话题
        topics_text = "，".join(topics[:5])
        
        # 格式化建议
        suggestions_text = ""
        for i, s in enumerate(suggestions, 1):
            suggestions_text += f"{i}. {s}\n"
        
        if not suggestions_text:
            suggestions_text = "1. 继续保持自然互动\n"
        
        return template.format(
            period=period_sec,
            highlights=highlights_text.strip(),
            gift_summary=gift_summary,
            topics=topics_text,
            suggestions=suggestions_text.strip()
        )
    
    def _calculate_priority(self, stats: Dict[str, Any], highlights: List[Dict[str, Any]]) -> int:
        """计算推送优先级"""
        priority = 5  # 默认优先级
        
        # 基于事件数量
        if stats["total_events"] > 30:
            priority += 2
        elif stats["total_events"] < 5:
            priority -= 1
        
        # 基于高价值用户
        if stats["high_value_users_count"] > 0:
            priority += 1
        
        # 基于消费金额
        if stats["total_rmb"] > 50:
            priority += 1
        elif stats["total_rmb"] > 200:
            priority += 2
        
        # 基于高亮事件数量
        if len(highlights) > 3:
            priority += 1
        
        return max(1, min(10, priority))  # 限制在1-10范围内
    
    def _is_question(self, text: Optional[str]) -> bool:
        """判断是否为提问"""
        if not text:
            return False
        
        question_indicators = [
            "吗？", "?", "？", "什么", "怎么", "如何", "为什么", "为啥",
            "请教", "请问", "问一下", "知不知道", "懂不懂", "会不会"
        ]
        
        text_lower = text.lower()
        for indicator in question_indicators:
            if indicator in text_lower:
                return True
        
        return False
    
    def _generate_empty_summary(self, room_id: int, period_sec: int) -> SummaryResult:
        """生成空摘要"""
        empty_text = f"过去{period_sec}秒直播间暂无新互动，可以主动和观众聊聊天~"
        
        return SummaryResult(
            summary_text=empty_text,
            highlights=[],
            topics=["暂无话题"],
            suggestions=["主动发起话题", "聊聊直播内容", "和观众打招呼"],
            priority=3,
            metadata={
                "room_id": room_id,
                "period_sec": period_sec,
                "event_count": 0,
                "engine": "local",
                "generated_at": time.time(),
                "is_empty": True
            }
        )


# 全局本地引擎实例
_global_local_engine: Optional[LocalEngine] = None

def get_global_local_engine(config: Optional[Dict[str, Any]] = None) -> LocalEngine:
    """获取全局本地引擎实例"""
    global _global_local_engine
    
    if _global_local_engine is None:
        _global_local_engine = LocalEngine(config)
    
    return _global_local_engine