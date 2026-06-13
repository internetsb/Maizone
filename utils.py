"""
utils.py
LLM生成内容并调用底层API与QQ空间交互
"""
from typing import List, Dict, Tuple, Any
import datetime
import asyncio
import json
import os
import random
from pathlib import Path

from .qzone_api import create_qzone_api
from .cookie import renew_cookies
from .image import generate_images

# 全局插件上下文
plugin_context = None
def set_utils_plugin_context(ctx):
    global plugin_context
    plugin_context = ctx

# 数据存储
_processed_list_lock = asyncio.Lock()
_processed_list_cache: Dict[str, List] | None = None
_MAX_PROCESSED_FEEDS = 500  # 最多记录500条说说
_MAX_PROCESSED_COMMENTS = 100  # 每条说说最多记录100条已处理评论


def _processed_list_path() -> str:
    return str(Path(__file__).parent.resolve() / "processed_list.json")


async def _get_processed_list() -> Dict[str, List]:
    """
    获取已处理说说及评论字典，格式为 { "说说tid": [已处理评论tid1, 已处理评论tid2, ...], ... }
    进程内所有调用方共享同一个dict，避免各自加载副本、最后整体覆盖保存造成丢失更新。
    """
    global _processed_list_cache
    if _processed_list_cache is not None:
        return _processed_list_cache
    logger = plugin_context.ctx.logger  # type: ignore
    async with _processed_list_lock:
        if _processed_list_cache is None:
            file_path = _processed_list_path()
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        _processed_list_cache = json.load(f)
                except Exception as e:
                    logger.error(f"加载已处理说说失败: {str(e)}")
                    _processed_list_cache = {}
            else:
                logger.warning("未找到已处理说说列表，将创建新列表")
                _processed_list_cache = {}
    return _processed_list_cache


async def _mark_processed(fid: str, comment_tid=None) -> bool:
    """
    标记一条说说（及可选的其中一条评论）为已处理，并立即原子落盘。
    每次标记都会把该说说移到字典末尾（LRU），仍然出现在最近列表中的说说
    不会被容量裁剪淘汰，从而避免重复评论/重复回复。
    Args:
        fid: 说说tid
        comment_tid: 已处理的评论tid，None表示仅标记说说本身
    Returns:
        bool: 落盘是否成功（内存中的标记总是生效）。
    """
    logger = plugin_context.ctx.logger  # type: ignore
    processed_list = await _get_processed_list()
    async with _processed_list_lock:
        comments = processed_list.pop(fid, [])
        if comment_tid is not None and comment_tid not in comments:
            comments.append(comment_tid)
            if len(comments) > _MAX_PROCESSED_COMMENTS:
                comments = comments[-_MAX_PROCESSED_COMMENTS:]
        processed_list[fid] = comments
        while len(processed_list) > _MAX_PROCESSED_FEEDS:
            processed_list.pop(next(iter(processed_list)))
        try:
            file_path = _processed_list_path()
            tmp_path = file_path + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(processed_list, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, file_path)
            return True
        except Exception as e:
            logger.error(f"保存已处理说说失败: {str(e)}")
            return False
    
async def send_feed(topic: str) -> Tuple[bool, str]:
    """
    根据主题和配置生成文本和图片，发送至QQ空间，返回是否发送成功和发送结果。

    Args:
        topic (str): 要发送的说说主题。

    Returns:
        Tuple[bool, str]:
        bool: 如果发送成功返回True，否则返回False。
        str: 发送结果，可能为"已发送说说：【文本内容】" 或 "发送说说失败"。
    """
    logger = plugin_context.ctx.logger  # type: ignore
    config = plugin_context.config  # type: ignore
    # ===== 根据主题和历史说说生成内容 =====
    prompt_pattern = plugin_context.config.send.prompt # type: ignore
    prompt = prompt_pattern.format(
        bot_personality=plugin_context.personality, # type: ignore
        current_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        topic=topic,
        bot_expression=plugin_context.reply_style # type: ignore
    )
    await renew_cookies(config.plugin.http_host, config.plugin.http_port, config.plugin.napcat_token) # type: ignore
    qzone = create_qzone_api()
    if not qzone:
        logger.error("创建QzoneAPI实例失败，无法发送说说")
        return False, "发送说说失败"
    history = await qzone.get_send_history(config.send.history_number) # type: ignore
    prompt += "\n以下是你近期发布过的说说，请勿在短时间内发布重复内容：\n"
    prompt += history

    llm_response = await plugin_context.ctx.llm.generate(prompt, model=config.plugin.text_model) # type: ignore
    message = llm_response.get("response", "")
    logger.info(f"已生成说说：{message}")
    # ===== 根据内容生成图片 =====
    images_list: list[bytes] = []
    if config.send.enable_image: # type: ignore
        images_list = await generate_images(message, config.send.image_mode, config.send.image_number, config.send.ai_probability) # type: ignore
    # ===== 发布说说 =====
    result = await qzone.publish_emotion(message, images_list)
    if result is not None:
        logger.info(f"发布说说ID：{result}")
        return True, f"已发送说说：【{message}】"
    else:
        logger.error("发送说说失败")
        return False, "说说发布失败"

async def read_feed(target_qq: str) -> Tuple[bool, list[dict[str, Any]]]:
    """
    阅读指定QQ号最近的动态，根据配置进行点赞回复，并返回结果
    Args:
        target_qq: 需要阅读的QQ号

    Returns:
        Tuple[bool, list[dict[str, Any]]]: 返回一个元组，第一个元素表示是否成功，第二个元素为目标空间内容的列表或错误信息。
    """
    logger = plugin_context.ctx.logger  # type: ignore
    config = plugin_context.config  # type: ignore
    await renew_cookies(config.plugin.http_host, config.plugin.http_port, config.plugin.napcat_token)  # type: ignore
    qzone = create_qzone_api()
    if not qzone:
        logger.error("创建QzoneAPI实例失败，无法读取说说")
        return False, [{"error": "无法创建QzoneAPI实例"}]
    # ===== 获取说说列表 =====
    feeds_list = await qzone.get_list(target_qq, config.read.read_number)  # type: ignore
    if not feeds_list:
        logger.error("获取说说列表失败：返回为空")
        return False, [{"error": "获取说说列表为空"}]
    first_feed = feeds_list[0]
    # 检查是否获取失败
    if isinstance(first_feed, dict) and first_feed.get("error"):
        logger.error(f"获取说说列表失败，错误信息：{first_feed['error']}")
        return False, feeds_list
    logger.info(f"获取到的说说列表：{format_feed_list(feeds_list)}")
    # ===== 逐条点赞、回复 =====
    like_probability = config.read.like_probability  # type: ignore
    comment_probability = config.read.comment_probability  # type: ignore
    try:
        target_user_info = await plugin_context.ctx.db.get(model_name="PersonInfo", filters={"user_id": target_qq})  # type: ignore
    except Exception:
        target_user_info = [{"person_name": "未知用户", "memory_points": "无印象"}]
    target_name = target_user_info[0].get("person_name") if target_user_info else "未知用户"
    impression = str(target_user_info[0].get("memory_points", "")) if target_user_info else "无印象"
    bot_personality = plugin_context.personality  # type: ignore
    bot_expression = plugin_context.reply_style  # type: ignore
    processed_list = await _get_processed_list()
    for feed in feeds_list:
        fid = feed["tid"]
        if fid in processed_list:
            # 已处理过：touch一次保持LRU活跃，防止仍在最近列表中的说说被裁剪淘汰后重复评论
            await _mark_processed(fid)
            continue
        await asyncio.sleep(3 + random.random())
        content = feed["content"]
        if feed["images"]:
            for image in feed["images"]:
                content = content + image
        rt_con = feed.get("rt_con", "")
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            # 进行评论
            if random.random() <= comment_probability:
                data = {
                        "current_time": current_time,
                        "created_time": feed['created_time'],
                        "bot_personality": bot_personality,
                        "bot_expression": bot_expression,
                        "target_name": target_name,
                        "content": content,
                        "impression": impression
                    }
                if not rt_con:
                    prompt_pre = config.read.prompt
                else:
                    prompt_pre = config.read.rt_prompt
                    data["rt_con"] = rt_con
                prompt = prompt_pre.format(**data)
                logger.info(f"LLM生成prompt：{prompt}")
                llm_response = await plugin_context.ctx.llm.generate(prompt, model=config.plugin.text_model)  # type: ignore
                comment_message = llm_response.get("response", "")
                result = await qzone.comment(fid, target_qq, comment_message)
                if result:
                    logger.info(f"评论成功：{comment_message}")
                else:
                    logger.error("评论失败")
            # 进行点赞
            if random.random() <= like_probability:
                result = await qzone.like(fid, target_qq)
                if result:
                    logger.info("点赞成功")
                else:
                    logger.error("点赞失败")
        except Exception as e:
            logger.error(f"处理说说{fid}时出错: {str(e)}")
        # 无论成功与否都立即落盘标记，避免下一轮对同一说说重复评论
        await _mark_processed(fid)
    return True, feeds_list

async def monitor_read_feed() -> Tuple[bool, list[dict[str, Any]]]:
    """
    读取空间下最新说说并根据配置进行点赞、评论等操作
    Returns:
    Tuple[bool, list[dict[str, Any]]]: 返回一个元组，第一个元素表示是否成功，第二个元素为操作结果的列表或错误信息。
    """
    logger = plugin_context.ctx.logger  # type: ignore
    config = plugin_context.config  # type: ignore
    black_list = config.authority.auto_read_blacklist
    processed_list = await _get_processed_list()
    bot_personality = plugin_context.personality  # type: ignore
    bot_expression = plugin_context.reply_style  # type: ignore
    await renew_cookies(config.plugin.http_host, config.plugin.http_port, config.plugin.napcat_token)  # type: ignore
    qzone = create_qzone_api()
    if not qzone:
        logger.error("创建QzoneAPI实例失败，无法监控说说")
        return False, [{"error": "无法创建QzoneAPI实例"}]
    # 获取说说列表
    logger.info("正在阅读空间...")
    feeds_list = await qzone.get_qzone_list()
    # 检查是否获取失败
    if isinstance(feeds_list, list) and len(feeds_list) > 0 and isinstance(feeds_list[0], dict) and feeds_list[0].get("error"):
        logger.error(f"获取说说列表失败，错误信息：{feeds_list[0]['error']}")
        return False, feeds_list
    # 点赞、评论等操作
    like_possibility = config.read.like_probability  # type: ignore
    comment_possibility = config.read.comment_probability  # type: ignore
    for feed in feeds_list:
        # 跳过黑名单QQ
        if feed["target_qq"] in black_list:
            logger.info(f"跳过黑名单QQ {feed['target_qq']} 的说说")
            continue
        fid = feed["tid"]
        if fid in processed_list:
            # 已处理过：touch一次保持LRU活跃，防止仍在动态页中的说说被裁剪淘汰后重复评论
            await _mark_processed(fid)
            continue
        # 提取说说信息
        await asyncio.sleep(3 + random.random())
        content = feed["content"]
        if feed["images"]:
            for image in feed["images"]:
                content = content + image
        target_qq = feed["target_qq"]
        rt_con = feed.get("rt_con", "")
        try:
            # 进行评论
            if random.random() <= comment_possibility:
                # 根据配置生成评论内容
                try:
                    target_user_info = await plugin_context.ctx.db.get(model_name="PersonInfo", filters={"user_id": target_qq})  # type: ignore
                except Exception as e:
                    logger.error(f"获取目标用户信息失败：{e}")
                    target_user_info = [{"person_name": "未知用户", "memory_points": "无印象"}]
                target_name = target_user_info[0].get("person_name") if target_user_info else "未知用户"
                impression = str(target_user_info[0].get("memory_points", "")) if target_user_info else "无印象"
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 获取当前时间
                created_time = feed.get("created_time", "未知时间")
                data = {
                    "current_time": current_time,
                    "created_time": created_time,
                    "bot_personality": bot_personality,
                    "bot_expression": bot_expression,
                    "target_name": target_name,
                    "content": content,
                    "impression": impression
                }
                if not rt_con:
                    prompt_pre = config.read.prompt
                else:
                    prompt_pre = config.read.rt_prompt
                    data["rt_con"] = rt_con
                prompt = prompt_pre.format(**data)
                logger.info(f"正在评论'{target_qq}'的说说：{content[:30]}...")
                response = await plugin_context.ctx.llm.generate(prompt, model=config.plugin.text_model)  # type: ignore
                comment = response.get("response", "")
                result = await qzone.comment(fid, target_qq, comment)
                if result:
                    logger.info(f"成功对说说'{content[:30]}...'发表评论：{comment}")
                else:
                    logger.error(f"对说说'{content[:30]}...'发表评论失败")
            # 进行点赞
            if random.random() <= like_possibility:
                result = await qzone.like(fid, target_qq)
                if result:
                    logger.info(f"成功点赞说说'{content[:30]}...'")
                else:
                    logger.error(f"点赞说说'{content[:30]}...'失败")
        except Exception as e:
            logger.error(f"处理说说{fid}时出错: {str(e)}")
        # 无论成功与否都立即落盘标记，避免下一轮对同一说说重复评论
        await _mark_processed(fid)

    return True, feeds_list

async def reply_feed() -> Tuple[bool, str]:
    """
    根据配置自动回复说说
    Returns:
        Tuple[bool, str]: 返回一个元组，第一个元素表示是否成功，第二个元素为操作结果或错误信息。
    """
    logger = plugin_context.ctx.logger  # type: ignore
    config = plugin_context.config  # type: ignore
    reply_number = config.auto_reply.reply_number
    await renew_cookies(config.plugin.http_host, config.plugin.http_port, config.plugin.napcat_token)
    qzone = create_qzone_api()
    if not qzone:
        logger.error("创建QzoneAPI实例失败，无法回复说说")
        return False, "回复说说失败"
    # 获取自己的说说列表
    processed_list = await _get_processed_list()
    feeds_list = await qzone.get_list(qzone.uin, reply_number, False)
    if not feeds_list:
        logger.error("获取自己的说说列表失败：返回为空")
        return False, "获取自己的说说列表为空"
    if isinstance(feeds_list[0], dict) and feeds_list[0].get("error"):
        logger.error(f"获取自己的说说列表失败：{feeds_list[0]['error']}")
        return False, str(feeds_list[0]['error'])
    reply_count = 0
    for feed in feeds_list:
        fid = feed["tid"]
        # touch：自己的说说仍在回复窗口内时保持LRU活跃，防止评论记录被裁剪淘汰后重复回复
        await _mark_processed(fid)
        content = feed["content"]
        if feed["images"]:
            for image in feed["images"]:
                content = content + image
        target_qq = feed["target_qq"]
        comments_list = feed["comments"]
        # 检查需要回复的评论
        list_to_reply = []
        for comment in (comments_list or []):
            comment_qq = str(comment.get('qq_account', '') or '').strip()
            comment_tid = comment.get('comment_tid')
            if not comment_qq.isdigit() or not comment_tid:
                # 缺少评论者QQ或评论ID时无法定位评论，跳过
                continue
            if comment_qq == str(qzone.uin):
                # 只考虑不是自己的评论
                continue
            if comment_tid in processed_list.get(fid, []):
                # 只考虑未处理过的评论
                continue
            list_to_reply.append(comment)
        # 无新评论需要回复则跳过
        if len(list_to_reply) == 0:
            continue
        # 逐条回复
        for comment in list_to_reply:
            await asyncio.sleep(3 + random.random())
            comment_qq = str(comment.get('qq_account', ''))
            try:
                try:
                    comment_user_info = await plugin_context.ctx.db.get(model_name="PersonInfo", filters={"user_id": comment_qq})  # type: ignore
                except Exception as e:
                    logger.error(f"获取评论用户信息失败：{e}")
                    comment_user_info = [{"person_name": "未知用户", "memory_points": "无印象"}]
                impression = str(comment_user_info[0].get("memory_points", "无印象")) if comment_user_info else "无印象"
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 获取当前时间
                prompt_pre = config.auto_reply.prompt
                data = {
                    "current_time": current_time,
                    "created_time": comment['created_time'],
                    "bot_personality": plugin_context.personality, # type: ignore
                    "bot_expression": plugin_context.reply_style, # type: ignore
                    "nickname": comment['nickname'],
                    "content": content,
                    "comment_content": comment['content'],
                    "impression": impression,
                }
                prompt = prompt_pre.format(**data)
                logger.info(f"正在回复{comment['nickname']}的评论'{comment['content'][:30]}...'")
                response = await plugin_context.ctx.llm.generate(prompt, model=config.plugin.text_model)  # type: ignore
                reply_message = response.get("response", "")
                await renew_cookies(config.plugin.http_host, config.plugin.http_port, config.plugin.napcat_token)
                result = await qzone.reply(
                    fid,
                    target_qq,
                    comment['nickname'],
                    comment_qq,
                    reply_message,
                    comment['comment_tid'],
                )
                if result:
                    logger.info(f"成功回复{comment['nickname']}的评论'{comment['content'][:30]}...'：{reply_message}")
                    reply_count += 1
                else:
                    logger.error(f"回复{comment['nickname']}的评论'{comment['content'][:30]}...'失败")
            except Exception as e:
                logger.error(f"回复评论{comment.get('comment_tid')}时出错: {str(e)}")
            # 无论成功与否都立即落盘标记，避免下一轮重复回复同一条评论
            await _mark_processed(fid, comment['comment_tid'])
    return True, f"回复了{reply_count}条新评论"

def format_feed_list(feed_list: List[Dict]) -> str:
    """
    格式化说说列表为分层清晰的字符串以便显示
    Args:
        feed_list: 说说列表

    Returns:
        str: 格式化后的字符串
    """
    if not feed_list:
        return "feed_list 为空"

    # 检查是否是错误情况
    if len(feed_list) == 1 and "error" in feed_list[0]:
        error_msg = feed_list[0].get("error", "未知错误")
        return f"{error_msg}"

    result = []
    result.append("=" * 80)
    result.append("FEED LIST")
    result.append("=" * 80)

    for i, feed in enumerate(feed_list, 1):
        result.append(f"\nFeed #{i}")
        result.append("-" * 40)

        # 基本信息
        result.append(f"target_qq: {feed.get('target_qq', 'N/A')}")
        result.append(f"tid: {feed.get('tid', 'N/A')}")
        result.append(f"content: {feed.get('content', 'N/A')}")

        # 图片信息
        images = feed.get('images', [])
        if images:
            result.append(f"images: {len(images)}")
            for j, img in enumerate(images, 1):
                result.append(f"  image_{j}: {img}")
        else:
            result.append("images: []")

        # 视频信息
        videos = feed.get('videos', [])
        if videos:
            result.append(f"videos: {len(videos)}")
            for j, video in enumerate(videos, 1):
                result.append(f"  video_{j}: {video}")
        else:
            result.append("videos: []")

        # 转发内容
        rt_con = feed.get('rt_con', '')
        result.append(f"rt_con: {rt_con if rt_con else 'N/A'}")

        # 评论信息
        comments = feed.get('comments', [])
        if comments:
            result.append(f"comments: {len(comments)}")
            for j, comment in enumerate(comments, 1):
                result.append(f"  comment_{j}:")
                result.append(f"    qq_account: {comment.get('qq_account', 'N/A')}")
                result.append(f"    nickname: {comment.get('nickname', 'N/A')}")
                result.append(f"    comment_tid: {comment.get('comment_tid', 'N/A')}")
                result.append(f"    content: {comment.get('content', 'N/A')}")
                parent_tid = comment.get('parent_tid')
                result.append(f"    parent_tid: {parent_tid if parent_tid else 'None'}")
                if j < len(comments):  # 不在最后一个评论后加空行
                    result.append("")
        else:
            result.append("comments: []")

    result.append("=" * 80)
    result.append(f"总数: {len(feed_list)}")

    return "\n".join(result)


if __name__ == "__main__":
    import sys
    import tempfile
    import types

    class _Logger:
        def debug(self, msg):
            print("[DEBUG]", msg)

        def info(self, msg):
            print("[INFO]", msg)

        def warning(self, msg):
            print("[WARN]", msg)

        def error(self, msg):
            print("[ERROR]", msg)

    plugin_context = types.SimpleNamespace(ctx=types.SimpleNamespace(logger=_Logger()))

    async def _test():
        # 使用临时目录，避免覆盖真实的 processed_list.json
        tmp_dir = tempfile.mkdtemp()
        module = sys.modules[__name__]
        module._processed_list_path = lambda: str(Path(tmp_dir) / "processed_list.json")

        # 写入600条说说，验证容量裁剪到500
        for i in range(600):
            await _mark_processed(f"tid{i}", f"c{i % 150}")
        pl = await _get_processed_list()
        print("entries after 600 inserts:", len(pl))  # 应为500
        print("oldest evicted:", "tid0" not in pl and "tid99" not in pl)
        print("newest kept:", "tid599" in pl)

        # touch最旧的条目后再插入新条目，验证LRU不淘汰活跃条目
        oldest = next(iter(pl))
        await _mark_processed(oldest)
        await _mark_processed("tid_new")
        print("touched entry survived:", oldest in pl)

        # 验证评论裁剪到100条
        for j in range(150):
            await _mark_processed("tid_comments", j)
        print("comments trimmed to:", len(pl["tid_comments"]))  # 应为100

        # 验证落盘后可重新加载
        module._processed_list_cache = None
        reloaded = await _get_processed_list()
        print("reload consistent:", len(reloaded) == len(pl) or len(reloaded) <= 500)

    asyncio.run(_test())
