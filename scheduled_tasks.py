import asyncio
import time
import random
import datetime
import traceback
import os
import json
from typing import List, Dict
from pathlib import Path

from src.common.logger import get_logger
from src.plugin_system.apis import llm_api, config_api, person_api

from .qzone_api import create_qzone_api
from .cookie_manager import renew_cookies
from .utils import monitor_read_feed, reply_feed, comment_feed, like_feed, send_feed

logger = get_logger("Maizone.定时任务")


def _is_in_silent_period(silent_hours_config: str, like_during_silent: bool = False, comment_during_silent: bool = False) -> tuple[bool, bool, bool]:
    """
    检查当前时间是否在静默时间段内

    Args:
        silent_hours_config: 静默时间段配置，格式如"23:00-07:00,12:00-14:00"
        like_during_silent: 静默时间段内是否允许点赞
        comment_during_silent: 静默时间段内是否允许评论

    Returns:
        tuple: (是否在静默时间段, 是否允许点赞, 是否允许评论)
    """
    if not silent_hours_config or not silent_hours_config.strip():
        return False, True, True

    try:
        now = datetime.datetime.now()
        current_time = now.hour * 60 + now.minute  # 当前时间转换为分钟数

        # 解析静默时间段
        periods = silent_hours_config.split(',')
        is_silent = False

        for period in periods:
            period = period.strip()
            if not period:
                continue

            # 解析时间段
            if '-' not in period:
                continue

            start_str, end_str = period.split('-', 1)
            start_time = _parse_time_to_minutes(start_str.strip())
            end_time = _parse_time_to_minutes(end_str.strip())

            if start_time is None or end_time is None:
                continue

            # 检查时间范围（处理跨天的情况）
            if start_time <= end_time:
                # 不跨天，如 12:00-14:00
                if start_time <= current_time <= end_time:
                    is_silent = True
                    break
            else:
                # 跨天，如 23:00-07:00
                if current_time >= start_time or current_time <= end_time:
                    is_silent = True
                    break

        # 如果在静默时间段内，返回相应的权限控制
        if is_silent:
            return True, like_during_silent, comment_during_silent

        return False, True, True

    except Exception as e:
        logger.error(f"解析静默时间段配置失败: {str(e)}")
        return False, True, True


def _parse_time_to_minutes(time_str: str) -> int:
    """
    将时间字符串转换为分钟数

    Args:
        time_str: 时间字符串，格式"HH:MM"

    Returns:
        int: 分钟数，解析失败返回None
    """
    try:
        if ':' not in time_str:
            return None

        hour_str, minute_str = time_str.split(':', 1)
        hour = int(hour_str.strip())
        minute = int(minute_str.strip())

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
        else:
            return None

    except (ValueError, AttributeError):
        return None


# ===== 定时任务功能 =====
def _save_processed_list(processed_list: Dict[str, List[str]]):
    """保存已处理说说及评论字典到文件"""
    try:
        file_path = str(Path(__file__).parent.resolve() / "processed_list.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(processed_list, f, ensure_ascii=False, indent=2)
            logger.debug("已保存已处理说说列表")
    except Exception as e:
        logger.error(f"保存已处理评论失败: {str(e)}")


def _load_processed_list() -> Dict[str, List[str]]:
    """从文件加载已处理说说及评论字典"""
    file_path = str(Path(__file__).parent.resolve() / "processed_list.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载已处理评论失败: {str(e)}")
    return {}


class FeedMonitor:
    """说说监控类"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.is_running = False
        self.task = None

    async def start(self):
        """启动监控任务"""
        if self.is_running:
            return
        self.is_running = True
        self.task = asyncio.create_task(self._monitor_loop())
        logger.info("说说监控任务已启动")

    async def stop(self):
        """停止监控任务"""
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("说说监控任务已停止")

    async def _monitor_loop(self):
        """监控循环"""
        # 获取配置
        interval = self.plugin.get_config("monitor.interval_minutes", 5)
        # 记录已处理评论，说说id映射已处理评论列表
        processed_list = _load_processed_list()
        while self.is_running:
            try:
                # 等待指定时间
                await asyncio.sleep(interval * 60)
                # 执行监控任务
                await self.check_feeds(processed_list)
                # 保存已处理评论到文件
                _save_processed_list(processed_list)
            except asyncio.CancelledError:
                _save_processed_list(processed_list)
                break
            except Exception as e:
                logger.error(f"监控任务出错: {str(e)}")
                _save_processed_list(processed_list)
                traceback.print_exc()
                # 出错后等待一段时间再重试
                await asyncio.sleep(300)

    async def check_feeds(self, processed_comments: Dict[str, List[str]]):
        """检查空间说说并回复未读说说和评论"""

        # 检查时间段控制
        silent_hours = self.plugin.get_config("monitor.silent_hours", "")
        like_during_silent = self.plugin.get_config("monitor.like_during_silent", True)
        comment_during_silent = self.plugin.get_config("monitor.comment_during_silent", False)

        is_silent, allow_like, allow_comment = _is_in_silent_period(silent_hours, like_during_silent, comment_during_silent)

        if is_silent:
            logger.info(f"当前时间在静默时间段内，点赞权限: {allow_like}, 评论权限: {allow_comment}")
            # 如果在静默时间段且不允许点赞和评论，直接返回
            if not allow_like and not allow_comment:
                logger.info("静默时间段内不允许点赞和评论，跳过本次刷空间")
                return True, "静默时间段内跳过刷空间"

        qq_account = config_api.get_global_config("bot.qq_account", "")
        port = self.plugin.get_config("plugin.http_port", "9999")
        napcat_token = self.plugin.get_config("plugin.napcat_token", "")
        host = self.plugin.get_config("plugin.http_host", "")
        cookie_methods = self.plugin.get_config("plugin.cookie_methods", ["napcat", "clientkey", "qrcode", "local"])
        show_prompt = self.plugin.get_config("models.show_prompt", False)
        self_readnum = self.plugin.get_config("monitor.self_readnum", 5)
        #模型配置
        models = llm_api.get_available_models()
        text_model = self.plugin.get_config("models.text_model", "replyer")
        model_config = models[text_model]
        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality", "一个机器人")
        bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")
        # 更新cookies
        try:
            await renew_cookies(host, port, napcat_token, cookie_methods)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败"

        try:
            logger.info(f"监控任务: 正在获取说说列表")
            feeds_list = await monitor_read_feed(self_readnum)
        except Exception as e:
            logger.error(f"获取说说列表失败: {str(e)}")
            return False, "获取说说列表失败"
            # 逐条点赞回复
        try:
            if len(feeds_list) == 0:
                logger.info('未读取到新说说')
                return True, "success"
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
                # 回复自己的说说评论
                if target_qq == qq_account:
                    enable_auto_reply = self.plugin.get_config("monitor.enable_auto_reply", False)
                    if not enable_auto_reply:
                        continue
                    # 获取未回复的评论
                    list_to_reply = []  # 待回复的评论
                    if comments_list:
                        for comment in comments_list:
                            comment_qq = comment.get('qq_account', '')
                            if int(comment_qq) != int(qq_account):  # 只考虑不是自己的评论
                                if comment['comment_tid'] not in processed_comments.get(fid, []):  # 只考虑未处理过的评论
                                    list_to_reply.append(comment)  # 添加到待回复列表
                                    processed_comments.setdefault(fid, []).append(comment['comment_tid'])  # 记录到已处理评论
                                    while len(processed_comments) > 100:
                                        # 为防止字典无限增长，限制字典大小
                                        oldest_fid = next(iter(processed_comments))
                                        processed_comments.pop(oldest_fid)

                    if len(list_to_reply) == 0:
                        continue
                    for comment in list_to_reply:
                        # 逐条回复评论
                        user_id = comment['qq_account']
                        person_id = person_api.get_person_id("qq", user_id)
                        impression = await person_api.get_person_value(person_id, "memory_points", ["无"])
                        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 获取当前时间
                        prompt_pre = self.plugin.get_config("monitor.reply_prompt", "")
                        data = {
                            "current_time": current_time,
                            "created_time": comment['created_time'],
                            "bot_personality": bot_personality,
                            "bot_expression": bot_expression,
                            "nickname": comment['nickname'],
                            "content": content,
                            "comment_content": comment['content'],
                            "impression": impression,
                        }
                        prompt = prompt_pre.format(**data)
                        logger.info(f"正在回复{comment['nickname']}的评论：{comment['content']}...")

                        if show_prompt:
                            logger.info(f"回复评论prompt内容：{prompt}")

                        success, reply, reasoning, model_name = await llm_api.generate_with_model(
                            prompt=prompt,
                            model_config=model_config,
                            request_type="story.generate",
                            temperature=0.3,
                            max_tokens=4096
                        )

                        if not success:
                            return False, "生成回复内容失败"

                        logger.info(f"正在回复{comment['nickname']}的评论：{comment['content']}...")

                        await renew_cookies(host, port, napcat_token, cookie_methods)
                        success = await reply_feed(fid, target_qq, comment['nickname'], reply, comment['comment_tid'])
                        if not success:
                            logger.error(f"回复评论{comment['content']}失败")
                            return False, "回复评论失败"
                        logger.info(f"发送回复'{reply}'成功")
                        await asyncio.sleep(5 + random.random() * 5)
                    continue
                # 评论他人说说
                if fid in processed_comments:
                    # 该说说已处理过，跳过
                    continue
                person_id = person_api.get_person_id("qq", target_qq)
                impression = await person_api.get_person_value(person_id, "memory_points", ["无"])
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 获取当前时间
                created_time = feed.get("created_time", "未知时间")

                if not rt_con:
                    prompt_pre = self.plugin.get_config("read.prompt", "")
                    data = {
                        "current_time": current_time,
                        "created_time": created_time,
                        "bot_personality": bot_personality,
                        "bot_expression": bot_expression,
                        "target_name": target_qq,
                        "content": content,
                        "impression": impression
                    }
                    prompt = prompt_pre.format(**data)
                else:
                    prompt_pre = self.plugin.get_config("read.rt_prompt", "")
                    data = {
                        "current_time": current_time,
                        "created_time": created_time,
                        "bot_personality": bot_personality,
                        "bot_expression": bot_expression,
                        "target_name": target_qq,
                        "content": content,
                        "rt_con": rt_con,
                        "impression": impression
                    }
                    prompt = prompt_pre.format(**data)
                logger.info(f"正在评论'{target_qq}'的说说：{content[:30]}...")

                if show_prompt:
                    logger.info(f"评论说说prompt内容：{prompt}")

                success, comment, reasoning, model_name = await llm_api.generate_with_model(
                    prompt=prompt,
                    model_config=model_config,
                    request_type="story.generate",
                    temperature=0.3,
                    max_tokens=4096
                )

                if not success:
                    return False, "生成评论内容失败"

                logger.info(f"成功生成评论内容：'{comment}'，即将发送")

                # 根据时间段控制决定是否评论
                if allow_comment:
                    success = await comment_feed(target_qq, fid, comment)
                    if not success:
                        logger.error(f"评论说说{content}失败")
                        return False, "评论说说失败"
                    logger.info(f"发送评论'{comment}'成功")
                else:
                    logger.info(f"静默时间段内，跳过评论")

                # 根据时间段控制决定是否点赞
                if allow_like:
                    # 点赞说说
                    success = await like_feed(target_qq, fid)
                    if not success:
                        logger.error(f"点赞说说{content}失败")
                        return False, "点赞说说失败"
                    logger.info(f'点赞说说{content[:10]}..成功')
                else:
                    logger.info(f"静默时间段内，跳过点赞")
                # 记录该说说已处理
                processed_comments[fid] = []
                while len(processed_comments) > 100:
                    # 为防止字典无限增长，限制字典大小
                    oldest_fid = next(iter(processed_comments))
                    processed_comments.pop(oldest_fid)
                _save_processed_list(processed_comments)  # 每处理一条说说即保存
            return True, 'success'
        except Exception as e:
            logger.error(f"点赞评论失败: {str(e)}")
            return False, "点赞评论失败"


class ScheduleSender:
    """定时发送说说的类"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.is_running = False
        self.task = None
        self.last_send_time = 0
        self.fluctuate_table = []  # 记录波动后的发送时间表
        self.last_reset_date = None  # 记录上次重置发送时间表日期
        self.today_send_enabled = True  # 记录今天是否允许发送说说

    async def start(self):
        """启动定时发送任务"""
        if self.is_running:
            return
        self.is_running = True
        self.last_reset_date = datetime.datetime.now().date()
        self._generate_fluctuate_table()  # 生成时间表
        self._check_today_send_decision()  # 初始化今天的发送决策
        self.task = asyncio.create_task(self._schedule_loop())
        logger.info("定时发送说说任务已启动")

    async def stop(self):
        """停止定时发送任务"""
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("定时发送说说任务已停止")

    def _should_reset_schedule(self) -> bool:
        """检查是否需要重置发送时间表"""
        current_date = datetime.datetime.now().date()
        return self.last_reset_date != current_date

    def _check_today_send_decision(self):
        """检查今天是否允许发送说说"""
        probability = self.plugin.get_config("schedule.probability", 1.0)
        self.today_send_enabled = random.random() < probability
        if self.today_send_enabled:
            logger.info(f"今日已决定发送说说，概率：{probability}")
        else:
            logger.info(f"今日已决定不发送说说，概率：{probability}")

    def _generate_fluctuate_table(self):
        """生成波动后的发送时间表"""
        schedule_times = self.plugin.get_config("schedule.schedule_times", ["20:00"])
        fluctuation_minutes = self.plugin.get_config("schedule.fluctuation_minutes", 0)

        self.fluctuate_table = []

        for time_str in schedule_times:
            try:
                # 解析原始时间
                hour, minute = map(int, time_str.split(':'))
                base_time = datetime.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

                # 添加随机波动
                if fluctuation_minutes > 0:
                    fluctuation = random.randint(-fluctuation_minutes, fluctuation_minutes)
                    final_time = base_time + datetime.timedelta(minutes=fluctuation)
                else:
                    final_time = base_time

                # 格式化为字符串
                final_time_str = final_time.strftime("%H:%M")
                self.fluctuate_table.append(final_time_str)
                logger.info(f"生成发送时间：{final_time_str}（原始：{time_str}，波动：{fluctuation_minutes}分钟）")

            except Exception as e:
                logger.error(f"解析发送时间失败：{time_str}，错误：{str(e)}")
                continue

    async def _schedule_loop(self):
        """定时发送循环"""
        while self.is_running:
            try:
                # 检查是否需要重置发送时间表（每天0点）
                if self._should_reset_schedule():
                    self.last_reset_date = datetime.datetime.now().date()
                    self._generate_fluctuate_table()  # 重新生成时间表
                    self._check_today_send_decision()  # 重新决定今天是否发送

                # 获取当前时间
                current_time = datetime.datetime.now().strftime("%H:%M")

                # 检查是否到达发送时间
                if current_time in self.fluctuate_table and self.today_send_enabled:
                    # 避免同一分钟内重复发送
                    if time.time() - self.last_send_time > 60:
                        await self.send_scheduled_feed()
                        # 从时间表中移除已发送的时间
                        self.fluctuate_table.remove(current_time)

                await asyncio.sleep(60)  # 每分钟检查一次

            except Exception as e:
                logger.error(f"定时发送循环出错: {str(e)}")
                await asyncio.sleep(60)  # 出错后等待1分钟再重试

    async def send_scheduled_feed(self):
        """发送定时说说"""
        try:
            # 获取配置
            enable_image = self.plugin.get_config("send.enable_image", True)
            image_mode = self.plugin.get_config("send.image_mode", "only_emoji")
            ai_probability = self.plugin.get_config("send.ai_probability", 0.5)
            image_number = self.plugin.get_config("send.image_number", 1)
            history_number = self.plugin.get_config("send.history_number", 5)
            random_topic = self.plugin.get_config("schedule.random_topic", True)
            fixed_topics = self.plugin.get_config("schedule.fixed_topics", [])

            # 获取模型配置
            models = llm_api.get_available_models()
            text_model = self.plugin.get_config("models.text_model", "replyer")
            model_config = models[text_model]
            if not model_config:
                logger.error("未配置LLM模型")
                return

            # 获取人格配置
            bot_personality = config_api.get_global_config("personality.personality", "一个机器人")
            bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 确定主题
            if random_topic:
                topic = "随机主题"
            else:
                topic = random.choice(fixed_topics) if fixed_topics else "日常分享"

            # 生成说说内容
            prompt_pre = self.plugin.get_config("send.prompt", "")
            data = {
                "current_time": current_time,
                "bot_personality": bot_personality,
                "topic": topic,
                "bot_expression": bot_expression
            }
            prompt = prompt_pre.format(**data)

            logger.info(f"正在生成定时说说，主题：{topic}")

            # 生成说说内容
            success, feed_content, reasoning, model_name = await llm_api.generate_with_model(
                prompt=prompt,
                model_config=model_config,
                request_type="story.generate",
                temperature=0.7,
                max_tokens=4096
            )

            if not success:
                logger.error("生成说说内容失败")
                return

            logger.info(f"成功生成说说内容：{feed_content}")

            # 发送说说
            await send_feed(feed_content, enable_image, image_mode, ai_probability, image_number, history_number)

            # 记录发送时间
            self.last_send_time = time.time()
            logger.info("定时说说发送成功")

        except Exception as e:
            logger.error(f"发送定时说说失败: {str(e)}")
            traceback.print_exc()