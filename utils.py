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

async def _save_processed_list(processed_list: Dict[str, List[str]]) -> bool:
    """
    保存已处理说说及评论字典到文件
    Args:
        processed_list (Dict[str, List[str]]): 已处理说说及评论字典，格式为 { "说说tid": ["已处理评论tid1", "已处理评论tid2", ...], ... }
    Returns:
        bool: 如果保存成功返回True，否则返回False。
    """
    logger = plugin_context.ctx.logger # type: ignore
    async with _processed_list_lock:
        try:
            # 限制大小为500条说说，每条说说最多100条评论，超过时去除最旧的数据
            # 对每条说说的评论列表修剪到最新 100 条
            for tid, comments in list(processed_list.items()):
                if isinstance(comments, list) and len(comments) > 100:
                    processed_list[tid] = comments[-100:]

            # 如果说说条目超过 500，则保留最后 500 条（最新的 500 条）
            if len(processed_list) > 500:
                items = list(processed_list.items())
                trimmed_items = items[-500:]
                processed_list = dict(trimmed_items)

            file_path = str(Path(__file__).parent.resolve() / "processed_list.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(processed_list, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存已处理说说失败: {str(e)}")
            return False


async def _load_processed_list() -> Dict[str, List[str]]:
    """
    从文件加载已处理说说及评论字典
    Returns:
        Dict[str, List[str]]: 已处理说说及评论字典，格式为 { "说说tid": ["已处理评论tid1", "已处理评论tid2", ...], ... }
        如果未找到已处理说说列表，则返回一个空字典。
    """
    logger = plugin_context.ctx.logger # type: ignore
    async with _processed_list_lock:
        file_path = str(Path(__file__).parent.resolve() / "processed_list.json")

        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                    return loaded_data
            except Exception as e:
                logger.error(f"加载已处理说说失败: {str(e)}")
                return {}
        logger.warning("未找到已处理说说列表，将创建新列表")
        return {}
    
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
    except Exception as e:
        target_user_info = [{"person_name": "未知用户", "memory_points": "无印象"}]
    target_name = target_user_info[0].get("person_name") if target_user_info else "未知用户"
    impression = str(target_user_info[0].get("memory_points", "")) if target_user_info else "无印象"
    bot_personality = plugin_context.personality  # type: ignore
    bot_expression = plugin_context.reply_style  # type: ignore
    processed_list = await _load_processed_list()
    for feed in feeds_list:
        if feed["tid"] in processed_list:
            continue
        await asyncio.sleep(3 + random.random())
        content = feed["content"]
        if feed["images"]:
            for image in feed["images"]:
                content = content + image
        fid = feed["tid"]
        rt_con = feed.get("rt_con", "")
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        # 更新已处理列表
        if fid not in processed_list:
            processed_list[fid] = []
    # 储存本轮处理结果
    success = await _save_processed_list(processed_list)
    if not success:
        logger.error("更新已处理列表失败")
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
    processed_list = await _load_processed_list()
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
        # 提取说说信息
        await asyncio.sleep(3 + random.random())
        content = feed["content"]
        if feed["images"]:
            for image in feed["images"]:
                content = content + image
        fid = feed["tid"]
        target_qq = feed["target_qq"]
        rt_con = feed.get("rt_con", "")
        comments_list = feed["comments"]
        if fid in processed_list:
            # 该说说已处理过，跳过
            continue
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
        # 更新已处理列表
        if fid not in processed_list:
            processed_list[fid] = []
    # 储存本轮处理结果
    success = await _save_processed_list(processed_list)
    if not success:
        logger.error("更新已处理列表失败")
        return False, feeds_list

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
    processed_list = await _load_processed_list()
    feeds_list = await qzone.get_list(qzone.uin, reply_number, False)
    reply_count = 0
    for feed in feeds_list:
        await asyncio.sleep(3 + random.random())
        content = feed["content"]
        if feed["images"]:
            for image in feed["images"]:
                content = content + image
        fid = feed["tid"]
        target_qq = feed["target_qq"]
        rt_con = feed.get("rt_con", "")
        comments_list = feed["comments"]
        # 检查需要回复的评论
        list_to_reply = []
        if comments_list:
            for comment in comments_list:
                comment_qq = comment.get('qq_account', '')
                if int(comment_qq) != int(qzone.uin): #只考虑不是自己的评论
                    if comment['comment_tid'] not in processed_list.get(fid, []): #只考虑未处理过的评论
                        list_to_reply.append(comment) #添加到待回复列表
        # 无新评论需要回复则跳过
        if len(list_to_reply) == 0:
            continue
        # 逐条回复
        for comment in list_to_reply:
            comment_qq = comment.get('qq_account', '')
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
            # 更新已处理列表
            if fid in processed_list:
                if comment['comment_tid'] not in processed_list[fid]:
                    processed_list[fid].append(comment['comment_tid'])
            else:
                processed_list[fid] = [comment['comment_tid']]
    # 储存本轮处理结果
    success = await _save_processed_list(processed_list)
    if not success:
        logger.error("保存处理结果失败")
        return False, "保存处理结果失败"
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
        # 构造测试数据：600 条说说，其中部分评论数 > 100
        pl = {}
        for i in range(600):
            tid = f"tid{i}"
            comments = [f"c{j}" for j in range((i % 150) + 1)]
            pl[tid] = comments

        ok = await _save_processed_list(pl)
        print("save ok:", ok)
        loaded = await _load_processed_list()
        print("loaded count:", len(loaded))
        max_comments = max((len(v) for v in loaded.values()), default=0)
        print("max comments per tid after trim:", max_comments)
        keys = list(loaded.keys())
        print("first key:", keys[0] if keys else None, "last key:", keys[-1] if keys else None)

    asyncio.run(_test())
