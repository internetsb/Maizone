import asyncio
import datetime
import random
from pathlib import Path
from typing import Tuple

from src.plugin_system import BaseAction, ActionActivationType
from src.plugin_system.apis import llm_api, config_api, person_api, generator_api
from src.common.logger import get_logger

from .qzone_api import create_qzone_api
from .cookie_manager import renew_cookies
from .utils import send_feed, read_feed, comment_feed, like_feed
from .scheduled_tasks import _save_processed_list, _load_processed_list


async def reply_send(action: BaseAction, chat_stream, extra_info: str) -> bool:
    """生成回复并发送"""
    success, response = await generator_api.generate_reply(
        chat_stream=chat_stream,
        chat_id=chat_stream.stream_id,
        extra_info=extra_info
    )
    for reply in response.reply_set.reply_data:
        reply_content = reply.content
        await action.send_text(content=reply_content, typing=True)
    return True


logger = get_logger('Maizone.actions')


# ===== 插件Action组件 =====
class SendFeedAction(BaseAction):
    """发说说Action - 只在用户要求发说说时激活"""

    action_name = "send_feed"
    action_description = "发一条相应主题的说说，自带reply动作，无需第二动作回复"

    activation_type = ActionActivationType.KEYWORD
    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD

    activation_keywords = ["说说", "空间", "动态"]
    keyword_case_sensitive = False
    action_parameters = {
        "topic": "要发送的说说主题或完整内容",
        "user_name": "要求你发说说的好友的qq名称",
    }
    action_require = [
        "用户要求发说说时使用",
        "当有人希望你更新qq空间时使用",
        "当你认为适合发说说时使用",
    ]
    associated_types = ["text"]

    def check_permission(self, qq_account: str) -> bool:
        """检查qq号为qq_account的用户是否拥有权限"""
        permission_list = self.get_config("send.permission")
        permission_type = self.get_config("send.permission_type")
        logger.info(f'[{self.action_name}]{permission_type}:{str(permission_list)}')
        if permission_type == 'whitelist':
            return qq_account in permission_list
        elif permission_type == 'blacklist':
            return qq_account not in permission_list
        else:
            logger.error('permission_type错误，可能为拼写错误')
            return False

    async def execute(self) -> Tuple[bool, str]:
        #检查权限
        user_name = self.action_data.get("user_name", "")
        person_id = person_api.get_person_id_by_name(user_name)
        show_prompt = self.get_config("models.show_prompt", False)
        user_id = await person_api.get_person_value(person_id, "user_id")
        if not user_id or user_id == "unknown":  # 若用户未知，拒绝执行
            logger.error(f"未找到用户 {user_name} 的user_id")
            if not await reply_send(self, self.chat_stream, f'你不认识{user_name}，请用符合你人格特点的方式拒绝请求'):
                return False, "生成回复失败"
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="拒绝执行发送说说动作：无法获取未知用户QQ",
                action_done=False,
            )
            return False, "未找到用户的user_id"

        if not self.check_permission(user_id):  # 若权限不足，拒绝执行
            logger.info(f"{user_id}无{self.action_name}权限")
            if not await reply_send(self, self.chat_stream, f'{user_name}无权命令你发说说，请用符合人格的方式进行拒绝的回复'):
                return False, "生成回复失败"
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="拒绝执行发送说说动作：用户权限不足",
                action_done=False,
            )
            return False, "无权限"
        else:
            logger.info(f"{user_id}拥有{self.action_name}权限")

        # 获取说说主题
        topic = self.action_data.get("topic", "")
        logger.info(f"说说主题:{topic}")
        # 获取模型配置
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer")
        model_config = models[text_model]

        if not model_config:
            return False, "未配置LLM模型"
        # 人格配置
        bot_personality = config_api.get_global_config("personality.personality", "一个机器人")
        bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")
        # 核心配置
        port = self.get_config("plugin.http_port", "9999")
        napcat_token = self.get_config("plugin.napcat_token", "")
        host = self.get_config("plugin.http_host", "127.0.0.1")
        cookie_methods = self.get_config("plugin.cookie_methods", ["napcat", "clientkey", "qrcode", "local"])
        # 生成图片相关配置
        image_dir = str(Path(__file__).parent.resolve() / "images")
        apikey = self.get_config("models.api_key", "")
        image_mode = self.get_config("send.image_mode", "random").lower()
        ai_probability = self.get_config("send.ai_probability", 0.5)
        image_number = self.get_config("send.image_number", 1)
        # 说说生成相关配置
        history_num = self.get_config("send.history_number", 5)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            await renew_cookies(host, port, napcat_token, cookie_methods)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="执行发送说说动作失败：登陆失败，cookies出错",
                action_done=False,
            )
            return False, "更新cookies失败"
        # 创建qzone_api实例
        qzone = create_qzone_api()

        prompt_pre = self.get_config("send.prompt", "你是'{bot_personality}'，现在是'{current_time}'你想写一条主题是'{topic}'的说说发表在qq空间上，"
                                          "{bot_expression}，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，只输出一条说说正文的内容，不要输出多余内容"
                                          "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )")
        data = {
            "current_time": current_time,
            "bot_personality": bot_personality,
            "topic": topic,
            "bot_expression": bot_expression
        }
        prompt = prompt_pre.format(**data)
        prompt += "\n以下是你以前发过的说说，写新说说时注意不要在相隔不长的时间发送相同主题的说说\n"
        prompt += await qzone.get_send_history(history_num)
        prompt += "\n只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"

        if show_prompt:
            logger.info(f"生成说说prompt内容：{prompt}")

        success, story, reasoning, model_name = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="story.generate",
            temperature=0.3,
            max_tokens=4096
        )

        if not success:
            return False, "生成说说内容失败"

        logger.info(f"生成说说内容：'{story}'，即将发送")
        if image_mode != "only_emoji" and not apikey:
            logger.warning("未配置apikey，无法生成图片，切换到only_emoji模式")
            image_mode = "only_emoji"  # 如果没有apikey，则只使用表情包

        # 发送说说
        enable_image = self.get_config("send.enable_image", "true")
        success = await send_feed(story, image_dir, enable_image, image_mode, ai_probability, image_number)
        if not success:
            return False, "发送说说失败"
        logger.info(f"成功发送说说: {story}")
        await self.store_action_info(
            action_build_into_prompt=True,
            action_prompt_display=f"执行了发送说说动作，你刚刚发了一条说说，内容为{story}",
            action_done=True,
        )
        # 生成回复
        if not await reply_send(self, self.chat_stream, f'你刚刚发了一条说说，内容为{story}，请生成一句话的回复'):
            return False, "生成回复失败"
        return True, 'success'


class ReadFeedAction(BaseAction):
    """读说说Action - 只在用户要求读说说时激活"""

    action_name = "read_feed"
    action_description = "读取好友最近的动态/说说/qq空间并评论点赞，自带reply动作，无需第二动作回复"

    activation_type = ActionActivationType.KEYWORD
    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD

    activation_keywords = ["说说", "空间", "动态"]
    keyword_case_sensitive = False
    action_parameters = {
        "target_name": "需要阅读动态的好友的qq名称",
        "user_name": "要求你阅读动态的好友的qq名称"
    }

    action_require = [
        "需要阅读某人动态、说说、QQ空间时使用",
        "当有人希望你评价某人的动态、说说、QQ空间",
        "当你认为适合阅读说说、动态、QQ空间时使用",
    ]
    associated_types = ["text"]

    def check_permission(self, qq_account: str) -> bool:
        """检查qq号为qq_account的用户是否拥有权限"""
        permission_list = self.get_config("read.permission")
        permission_type = self.get_config("read.permission_type")
        logger.info(f'[{self.action_name}]{permission_type}:{str(permission_list)}')
        if permission_type == 'whitelist':
            return qq_account in permission_list
        elif permission_type == 'blacklist':
            return qq_account not in permission_list
        else:
            logger.error('permission_type错误，可能为拼写错误')
            return False

    async def execute(self) -> Tuple[bool, str]:
        #检查权限
        user_name = self.action_data.get("user_name", "")
        person_id = person_api.get_person_id_by_name(user_name)
        show_prompt = self.get_config("models.show_prompt", False)
        user_id = await person_api.get_person_value(person_id, "user_id")
        if not user_id or user_id == "unknown":
            logger.error(f"未找到用户 {user_name} 的user_id")
            if not await reply_send(self, self.chat_stream, f'你不认识{user_name}，请用符合你人格特点的方式拒绝请求'):
                return False, "生成回复失败"
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="拒绝执行阅读说说动作：无法获取未知用户QQ",
                action_done=False,
            )
            return False, "未找到用户的user_id"
        if not self.check_permission(user_id):  # 若权限不足
            logger.info(f"{user_id}无{self.action_name}权限")
            if not await reply_send(self, self.chat_stream, f'{user_name}无权命令你读说说，请用符合人格的方式进行拒绝的回复'):
                return False, "生成回复失败"
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="拒绝执行阅读说说动作：用户权限不足",
                action_done=False,
            )
            return False, "无权限"
        else:
            logger.info(f"{user_id}拥有{self.action_name}权限")

        target_name = self.action_data.get("target_name", "")

        port = self.get_config("plugin.http_port", "9999")
        napcat_token = self.get_config("plugin.napcat_token", "")
        host = self.get_config("plugin.http_host", "")
        cookie_methods = self.get_config("plugin.cookie_methods", ["napcat", "clientkey", "qrcode", "local"])

        # 更新cookies
        try:
            await renew_cookies(host, port, napcat_token, cookie_methods)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="执行阅读说说动作失败：登录失败，cookies出错",
                action_done=False,
            )
            return False, "更新cookies失败"
        #根据昵称获取qq号
        person_id = person_api.get_person_id_by_name(target_name)
        logger.info(f'获取到person_id={person_id}')
        target_qq = await person_api.get_person_value(person_id, "user_id")
        logger.info(f'获取到user_id={target_qq}')
        impression = await person_api.get_person_value(person_id, "memory_points", ["无"])  # 获取记忆点
        #获取指定好友最近的说说
        num = self.get_config("read.read_number", 5)
        like_possibility = self.get_config("read.like_possibility", 1.0)
        comment_possibility = self.get_config("read.comment_possibility", 1.0)
        feeds_list = await read_feed(target_qq, num)
        if not feeds_list:
            logger.error("读取说说失败：返回列表为空")
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="执行阅读说说动作失败：未能读取到任何说说内容",
                action_done=False,
            )
            if not await reply_send(self, self.chat_stream,
                                    "执行阅读说说动作失败：未能读取到说说，可能是对方没有公开动态或接口返回为空"):
                return False, "生成回复失败"
            return False, "未读取到说说"

        first_feed = feeds_list[0]
        if isinstance(first_feed, dict) and first_feed.get("error"):
            error_message = first_feed.get("error")
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=f"执行阅读说说动作失败：未能读取到说说，错误原因：{error_message}",
                action_done=False,
            )
            if not await reply_send(self, self.chat_stream,
                                    f"执行阅读说说动作失败：未能读取到说说，原因：{error_message}"):
                return False, "生成回复失败"
            return False, error_message

        logger.info(f"成功读取到{len(feeds_list)}条说说")
        #模型配置
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer")
        model_config = models[text_model]
        if not model_config:
            return False, "未配置LLM模型"
        #人格配置
        bot_personality = config_api.get_global_config("personality.personality", "一个机器人")
        bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")
        #时间
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 获取当前时间
        #逐条点赞回复
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
            if random.random() <= comment_possibility:
                #评论说说
                if not rt_con:
                    prompt_pre = self.get_config("read.prompt", "你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
                                          "在qq空间上在'{created_time}'发了一条内容是'{content}'的说说，你想要发表你的一条评论，现在是'{current_time}'"
                                          "你对'{target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
                                          "{bot_expression}，回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容"
                                          "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容")
                    data = {
                        "current_time": current_time,
                        "created_time": feed['created_time'],
                        "bot_personality": bot_personality,
                        "bot_expression": bot_expression,
                        "target_name": target_name,
                        "content": content,
                        "impression": impression
                    }
                    prompt = prompt_pre.format(**data)
                else:
                    prompt_pre = self.get_config("read.rt_prompt", "你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
                                             "在qq空间上在'{created_time}'转发了一条内容为'{rt_con}'的说说，你的好友的评论为'{content}'，你对'{" 
                                             "target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
                                             "现在是'{current_time}'，你想要发表你的一条评论，{bot_expression}，"
                                             "回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
                                             "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容")
                    data = {
                        "current_time": current_time,
                        "created_time": feed['created_time'],
                        "bot_personality": bot_personality,
                        "bot_expression": bot_expression,
                        "target_name": target_name,
                        "content": content,
                        "rt_con": rt_con,
                        "impression": impression
                    }
                    prompt = prompt_pre.format(**data)
                logger.info(f"正在评论'{target_name}'的说说：{content[:30]}...")

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

                success = await comment_feed(target_qq, fid, comment)
                if not success:
                    logger.error(f"评论说说'{content}'失败")
                    return False, "评论说说失败"
                logger.info(f"发送评论'{comment}'成功")

            # 点赞说说
            if random.random() <= like_possibility:
                success = await like_feed(target_qq, fid)
                if not success:
                    logger.error(f"点赞说说'{content}'失败")
                    return False, "点赞说说失败"
                logger.info(f"点赞说说'{content[:10]}..'成功")
            processed_list[fid] = []
            # 为防止字典无限增长，限制字典大小
            while len(processed_list) > self.get_config("monitor.processed_feeds_cache_size", 100):
                oldest_fid = next(iter(processed_list))
                processed_list.pop(oldest_fid)
        await _save_processed_list(processed_list)
        if not await reply_send(self, self.chat_stream, f'你刚刚成功读了以下说说：{feeds_list}'):
            return False, "生成回复失败"
        await self.store_action_info(
            action_build_into_prompt=True,
            action_prompt_display=f"执行阅读说说动作完成，你刚刚成功读了以下说说：{feeds_list}",
            action_done=True,
        )
        return False, 'success'
