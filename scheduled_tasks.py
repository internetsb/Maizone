import asyncio
import time
import random
import datetime
import traceback
from pathlib import Path

from src.common.logger import get_logger
from src.plugin_system.apis import llm_api, config_api

from .qzone_api import create_qzone_api
from .cookie_manager import renew_cookies
from .utils import monitor_read_feed, reply_feed, comment_feed, like_feed, send_feed

logger = get_logger("Maizone.定时任务")
# ===== 定时任务功能 =====
class FeedMonitor:
    """定时监控好友说说的类"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.is_running = False
        self.task = None
        self.last_check_time = 0

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
        while self.is_running:
            try:
                # 获取配置
                interval = self.plugin.get_config("monitor.interval_minutes", 5)
                read_num = 3

                # 等待指定时间
                await asyncio.sleep(interval * 60)

                # 执行监控任务
                await self.check_feeds(read_num)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"监控任务出错: {str(e)}")
                traceback.print_exc()
                # 出错后等待一段时间再重试
                await asyncio.sleep(300)

    async def check_feeds(self, read_num: int):
        """检查好友说说"""

        qq_account = config_api.get_global_config("bot.qq_account", "")
        port = self.plugin.get_config("plugin.http_port", "9999")
        napcat_token = self.plugin.get_config("plugin.napcat_token", "")
        host = self.plugin.get_config("plugin.http_host", "")
        show_prompt = self.plugin.get_config("models.show_prompt", False)
        #模型配置
        models = llm_api.get_available_models()
        text_model = self.plugin.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]
        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")

        # 更新cookies
        try:
            await renew_cookies(host, port, napcat_token)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败"

        try:
            logger.info(f"监控任务: 正在获取说说列表")
            feeds_list = await monitor_read_feed(read_num)
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
                # 检查自己的说说评论并回复
                if target_qq == qq_account:
                    enable_auto_reply = self.plugin.get_config("monitor.enable_auto_reply", False)
                    if not enable_auto_reply:
                        continue
                    # 获取未回复的评论
                    ignored_tids = []  # 已回复的评论tid
                    list_to_reply = []  # 待回复的评论
                    if comments_list:
                        #print(comments_list)
                        for comment in comments_list:
                            if comment['parent_tid'] and comment['qq_account'] == qq_account:
                                ignored_tids.append(comment['parent_tid'])
                        list_to_reply = [
                            comment for comment in comments_list
                            if comment['parent_tid'] is None  # 只考虑主评论
                               and comment['comment_tid'] not in ignored_tids  # 没有被bot回复过
                        ]

                    if not list_to_reply:
                        continue
                    for comment in list_to_reply:
                        # 逐条回复评论
                        prompt = f"""
                        你是'{bot_personality}'，你的好友'{comment['nickname']}'评论了你QQ空间上的一条内容为“{content}”说说，
                        你的好友对该说说的评论为:“{comment["content"]}”，你想要对此评论进行回复
                        {bot_expression}，回复的平淡一些，简短一些，说中文，
                        不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                        """
                        logger.info(f"正在回复{comment['nickname']}的评论：{comment['content']}...")

                        if show_prompt:
                            logger.info(f"回复评论prompt内容：{prompt}")

                        success, reply, reasoning, model_name = await llm_api.generate_with_model(
                            prompt=prompt,
                            model_config=model_config,
                            request_type="story.generate",
                            temperature=0.3,
                            max_tokens=1000
                        )

                        if not success:
                            return False, "生成回复内容失败"

                        logger.info(f"正在回复{comment['nickname']}的评论：{comment['content']}...")

                        await renew_cookies(host, port, napcat_token)
                        success = await reply_feed(fid, target_qq, comment['nickname'], reply, comment['comment_tid'])
                        if not success:
                            logger.error(f"回复评论{comment['content']}失败")
                            return False, "回复评论失败"
                        logger.info(f"发送回复'{reply}'成功")
                        await asyncio.sleep(10 + random.random() * 10)
                    continue
                # 评论他人说说
                if not rt_con:
                    prompt = f"""
                    你是'{bot_personality}'，你正在浏览你好友'{target_qq}'的QQ空间，
                    你看到了你的好友'{target_qq}'qq空间上内容是'{content}'的说说，你想要发表你的一条评论，
                    {bot_expression}，回复的平淡一些，简短一些，说中文，
                    不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                    """
                else:
                    prompt = f"""
                    你是'{bot_personality}'，你正在浏览你好友'{target_qq}'的QQ空间，
                    你看到了你的好友'{target_qq}'在qq空间上转发了一条内容为'{rt_con}'的说说，你的好友的评论为'{content}'
                    你想要发表你的一条评论，{bot_expression}，回复的平淡一些，简短一些，说中文，
                    不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                    """
                logger.info(f"正在评论'{target_qq}'的说说：{content[:30]}...")

                if show_prompt:
                    logger.info(f"评论说说prompt内容：{prompt}")

                success, comment, reasoning, model_name = await llm_api.generate_with_model(
                    prompt=prompt,
                    model_config=model_config,
                    request_type="story.generate",
                    temperature=0.3,
                    max_tokens=1000
                )

                if not success:
                    return False, "生成评论内容失败"

                logger.info(f"成功生成评论内容：'{comment}'，即将发送")

                success = await comment_feed(target_qq, fid, comment)
                if not success:
                    logger.error(f"评论说说{content}失败")
                    return False, "评论说说失败"
                logger.info(f"发送评论'{comment}'成功")
                # 点赞说说
                success = await like_feed(target_qq, fid)
                if not success:
                    logger.error(f"点赞说说{content}失败")
                    return False, "点赞说说失败"
                logger.info(f'点赞说说{content[:10]}..成功')
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

    async def start(self):
        """启动定时发送任务"""
        if self.is_running:
            return
        self.is_running = True
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

    async def _schedule_loop(self):
        """定时发送循环"""
        while self.is_running:
            try:
                # 获取配置
                schedule_times = self.plugin.get_config("schedule.schedule_times", ["08:00", "20:00"])
                current_time = datetime.datetime.now().strftime("%H:%M")

                # 检查是否到达发送时间
                if current_time in schedule_times:
                    # 避免同一分钟内重复发送
                    if time.time() - self.last_send_time > 60:
                        logger.info("定时任务：正在发送说说")
                        self.last_send_time = time.time()
                        await self.send_scheduled_feed()

                # 每分钟检查一次
                await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时发送任务出错: {str(e)}")

    async def send_scheduled_feed(self):
        """发送定时说说"""
        # 模型配置
        models = llm_api.get_available_models()
        text_model = self.plugin.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]
        if not model_config:
            logger.error("未配置LLM模型")
            return

        # 获取主题设置
        random_topic = self.plugin.get_config("schedule.random_topic", True)
        fixed_topics = self.plugin.get_config("schedule.fixed_topics", ["日常生活", "心情分享", "有趣见闻"])
        # 人格配置
        bot_personality = config_api.get_global_config("personality.personality_core", "一个蓝发猫娘")
        bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")
        # 核心配置
        qq_account = config_api.get_global_config("bot.qq_account", "")
        port = self.plugin.get_config("plugin.http_port", "9999")
        napcat_token = self.plugin.get_config("plugin.napcat_token", "")
        host = self.plugin.get_config("plugin.http_host", "127.0.0.1")
        # 生成图片相关配置
        image_dir = str(Path(__file__).parent.resolve() / "images")
        enable_image = self.plugin.get_config("send.enable_image", True)
        apikey = self.plugin.get_config("models.api_key", "")
        image_mode = self.plugin.get_config("send.image_mode", "random").lower()
        ai_probability = self.plugin.get_config("send.ai_probability", 0.5)
        image_number = self.plugin.get_config("send.image_number", 1)
        # 说说生成相关配置
        history_number = self.plugin.get_config("send.history_number", 5)
        # 更新cookies
        try:
            await renew_cookies(host, port, napcat_token)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return
        qzone = create_qzone_api()
        # 生成说说内容
        if random_topic:
            prompt = f"""
            你是'{bot_personality}'，你想写一条说说发表在qq空间上，主题不限
            {bot_expression}
            不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，
            只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
            """
        else:
            fixed_topic = random.choice(fixed_topics)
            prompt = f"""
            你是'{bot_personality}'，你想写一条主题是'{fixed_topic}'的说说发表在qq空间上，
            {bot_expression}
            不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，
            只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
            """

        prompt += "\n以下是你最近发过的说说，写新说说时注意不要在相隔不长的时间发送相似内容的说说\n"
        prompt += await qzone.get_send_history(history_number)
        prompt += "\n只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"

        show_prompt = self.plugin.get_config("models.show_prompt", False)
        if show_prompt:
            logger.info(f"生成说说prompt内容：{prompt}")

        result = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="story.generate",
            temperature=0.3,
            max_tokens=1000
        )

        # 兼容不同的返回值格式
        if len(result) == 4:
            success, story, reasoning, model_name = result
        elif len(result) == 3:
            success, story, reasoning = result
        else:
            logger.error(f"LLM返回值格式不正确: {result}")
            return False, "生成说说内容失败", True

        if not success:
            return False, "生成说说内容失败", True

        logger.info(f"成功生成说说内容：'{story}'")
        # 检查apikey
        if image_mode != "only_emoji" and not apikey:
            logger.error('请填写apikey')
            image_mode = "only_emoji"  # 如果没有apikey，则只使用表情包

        # 发送说说
        success = await send_feed(story, image_dir, enable_image, image_mode, ai_probability, image_number,
                                  apikey)
        if success:
            logger.info(f"定时任务成功发送说说: {story}")
        else:
            logger.error("定时任务发送说说失败")