"""
chat_fetcher.py
独立实现的消息抓取模块，用于 Maizone 插件读取今日聊天记录作为说说素材。
不依赖日记插件，保持独立。
"""

import datetime
from typing import Any, Dict, List, Optional, Tuple

from maibot_sdk import API

# ===== Logger =====
class NoLogger:
    def info(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        pass
    def debug(self, msg):
        pass

logger = NoLogger()

def set_chat_fetcher_logger(custom_logger):
    """设置日志记录器"""
    global logger
    logger = custom_logger


# ===== 工具函数 =====
def peel_envelope(result: Any) -> Any:
    """
    解包 SDK 返回的 envelope 结构。
    如果 result 是 dict 且包含 'data' 或 'result' 字段，返回其值。
    否则返回原值。
    """
    if isinstance(result, dict):
        if "data" in result:
            return result["data"]
        if "result" in result:
            return result["result"]
    return result


def _parse_target_config(configs: List[str]) -> Tuple[List[str], List[str]]:
    """
    解析 target_chats 配置，分离私聊和群聊。

    Args:
        configs: 配置列表，格式如 ["group:123456", "private:789012", "987654"]

    Returns:
        (private_qqs, group_qqs): 私聊QQ列表和群聊QQ列表
    """
    private_qqs: List[str] = []
    group_qqs: List[str] = []
    for cfg in configs:
        cfg = cfg.strip()
        if not cfg:
            continue
        if cfg.startswith("private:"):
            private_qqs.append(cfg[len("private:"):].strip())
        elif cfg.startswith("group:"):
            group_qqs.append(cfg[len("group:"):].strip())
        else:
            # 无前缀默认视为群
            group_qqs.append(cfg)
    return private_qqs, group_qqs


async def _resolve_session_id(ctx, *, group_id: Optional[str] = None, user_id: Optional[str] = None) -> Optional[str]:
    """
    通过 ctx.chat 解析 session_id/stream_id。

    Args:
        ctx: 插件上下文
        group_id: 群QQ号
        user_id: 用户QQ号

    Returns:
        session_id 或 None
    """
    try:
        if group_id:
            result = await ctx.chat.get_stream_by_group_id(group_id)
        elif user_id:
            result = await ctx.chat.get_stream_by_user_id(user_id)
        else:
            return None
    except Exception as exc:
        logger.warning("ctx.chat 调用异常: %s", exc)
        return None

    result = peel_envelope(result)
    if not isinstance(result, dict):
        return None
    stream_data = result.get("stream", result)
    if isinstance(stream_data, dict):
        return stream_data.get("session_id") or stream_data.get("stream_id")
    return None


async def _list_messages(
    ctx,
    *,
    start_time: float,
    end_time: float,
    chat_id: str = "",
) -> List[Dict[str, Any]]:
    """
    统一调用 ctx.message 获取消息列表，处理 envelope。

    Args:
        ctx: 插件上下文
        start_time: 开始时间戳
        end_time: 结束时间戳
        chat_id: 可选，指定会话ID

    Returns:
        消息列表
    """
    kwargs: Dict[str, Any] = {
        "start_time": str(start_time),
        "end_time": str(end_time),
        "limit": 0,
        "limit_mode": "earliest",
        "filter_mai": False,
        "filter_command": False,
    }
    try:
        if chat_id:
            result = await ctx.message.get_by_time_in_chat(chat_id, **kwargs)
        else:
            # get_by_time 不接受 filter_command
            kwargs.pop("filter_command", None)
            result = await ctx.message.get_by_time(**kwargs)
    except Exception as exc:
        logger.error("ctx.message 查询失败 (chat_id=%s): %s", chat_id, exc, exc_info=True)
        return []

    result = peel_envelope(result)
    if isinstance(result, list):
        return [m for m in result if isinstance(m, dict)]
    if isinstance(result, dict):
        if not result.get("success", True):
            logger.warning("ctx.message 返回 success=False: %s", result.get("error"))
            return []
        messages = result.get("messages") or []
        return [m for m in messages if isinstance(m, dict)]
    logger.warning("ctx.message 返回非 list/dict: %s", type(result).__name__)
    return []


# ===== MessageFetcher 类 =====
class MessageFetcher:
    """
    消息抓取器，支持按过滤模式、会话、时间范围抓取消息。
    独立实现，不依赖日记插件。
    """

    def __init__(self, ctx) -> None:
        """
        初始化消息抓取器。

        Args:
            ctx: 插件上下文
        """
        self._ctx = ctx

    async def fetch_all(self, start_time: float, end_time: float) -> List[Dict[str, Any]]:
        """
        抓取所有会话的消息。

        Args:
            start_time: 开始时间戳
            end_time: 结束时间戳

        Returns:
            消息列表，按时间升序排列
        """
        msgs = await _list_messages(self._ctx, start_time=start_time, end_time=end_time, chat_id="")
        msgs.sort(key=lambda m: float(m.get("timestamp", 0) or 0))
        return msgs

    async def fetch_for_chats(
        self,
        chat_ids: List[str],
        start_time: float,
        end_time: float,
    ) -> List[Dict[str, Any]]:
        """
        抓取指定会话列表的消息。

        Args:
            chat_ids: 会话ID列表
            start_time: 开始时间戳
            end_time: 结束时间戳

        Returns:
            消息列表，按时间升序排列
        """
        all_msgs: List[Dict[str, Any]] = []
        for chat_id in chat_ids:
            if not chat_id:
                continue
            msgs = await _list_messages(self._ctx, start_time=start_time, end_time=end_time, chat_id=chat_id)
            all_msgs.extend(msgs)
        all_msgs.sort(key=lambda m: float(m.get("timestamp", 0) or 0))
        return all_msgs

    async def fetch_with_filter(
        self,
        filter_mode: str,
        target_chats: List[str],
        start_time: float,
        end_time: float,
    ) -> List[Dict[str, Any]]:
        """
        根据过滤模式抓取消息。

        Args:
            filter_mode: "all" | "whitelist" | "blacklist"
            target_chats: 目标会话配置列表
            start_time: 开始时间戳
            end_time: 结束时间戳

        Returns:
            消息列表，按时间升序排列
        """
        if filter_mode == "all":
            return await self.fetch_all(start_time, end_time)

        if filter_mode == "whitelist":
            if not target_chats:
                logger.info("白名单为空，返回空")
                return []
            session_ids = await self._resolve_session_ids(target_chats)
            if not session_ids:
                logger.warning("白名单解析后为空")
                return []
            return await self.fetch_for_chats(session_ids, start_time, end_time)

        if filter_mode == "blacklist":
            all_msgs = await self.fetch_all(start_time, end_time)
            if not target_chats:
                return all_msgs
            return self._filter_blacklist(all_msgs, target_chats)

        logger.warning("未知 filter_mode=%s，使用 all", filter_mode)
        return await self.fetch_all(start_time, end_time)

    async def _resolve_session_ids(self, configs: List[str]) -> List[str]:
        """
        解析配置列表为会话ID列表。

        Args:
            configs: 配置列表

        Returns:
            会话ID列表
        """
        private_qqs, group_qqs = _parse_target_config(configs)
        session_ids: List[str] = []
        for qq in group_qqs:
            sid = await _resolve_session_id(self._ctx, group_id=qq)
            if sid:
                session_ids.append(sid)
        for qq in private_qqs:
            sid = await _resolve_session_id(self._ctx, user_id=qq)
            if sid:
                session_ids.append(sid)
        return session_ids

    @staticmethod
    def _filter_blacklist(messages: List[Dict[str, Any]], excluded: List[str]) -> List[Dict[str, Any]]:
        """
        从消息列表中排除黑名单会话的消息。

        Args:
            messages: 消息列表
            excluded: 排除的会话配置列表

        Returns:
            过滤后的消息列表
        """
        ex_private, ex_group = _parse_target_config(excluded)
        ex_p = set(ex_private)
        ex_g = set(ex_group)

        filtered: List[Dict[str, Any]] = []
        for msg in messages:
            info = msg.get("message_info") or {}
            user_info = info.get("user_info") or {}
            group_info = info.get("group_info") or {}
            user_id = str(user_info.get("user_id", "") or "")
            group_id = str(group_info.get("group_id", "") or "")
            if group_id and group_id in ex_g:
                continue
            if not group_id and user_id and user_id in ex_p:
                continue
            filtered.append(msg)
        return filtered

    @staticmethod
    def format_chat_messages(
        messages: List[Dict[str, Any]],
        per_message_max_chars: int = 200,
        max_messages: int = 50,
        reverse: bool = True,
    ) -> str:
        """
        将消息列表格式化为可读文本，用于 prompt。

        Args:
            messages: 消息列表（已按时间升序排列）
            per_message_max_chars: 单条消息最大字符数，0表示不截断
            max_messages: 最大消息条数
            reverse: 是否反转顺序（True=最近的在前）

        Returns:
            格式化后的文本
        """
        if not messages:
            return ""

        # 截断消息数量
        if max_messages > 0 and len(messages) > max_messages:
            messages = messages[-max_messages:]

        # 决定顺序
        if reverse:
            messages = list(reversed(messages))

        parts: List[str] = []
        for msg in messages:
            # 提取消息信息
            info = msg.get("message_info") or {}
            user_info = info.get("user_info") or {}
            nickname = user_info.get("user_nickname") or user_info.get("user_cardname") or "某人"
            user_id = str(user_info.get("user_id", "") or "")

            # 提取文本内容
            text = str(msg.get("processed_plain_text") or msg.get("raw_message") or "")

            # 截断单条消息
            if per_message_max_chars > 0 and text and len(text) > per_message_max_chars:
                text = text[:per_message_max_chars] + "..."

            # 检查是否为图片
            is_pic = msg.get("is_picture") or msg.get("is_picid")
            if is_pic:
                text = text or "[图片]"

            # 检查是否包含图片段
            if not is_pic and "[picid:" in text.lower():
                text = "[图片]"

            # 构建时间显示
            timestamp = msg.get("timestamp", 0)
            try:
                if timestamp:
                    dt = datetime.datetime.fromtimestamp(float(timestamp))
                    time_str = dt.strftime("%H:%M")
                else:
                    time_str = "未知时间"
            except (OSError, OverflowError, ValueError):
                time_str = "未知时间"

            parts.append(f"[{time_str}] {nickname}: {text}")

        return "\n".join(parts)

    @staticmethod
    def count_messages(messages: List[Dict[str, Any]]) -> int:
        """获取消息总数"""
        return len(messages)