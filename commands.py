import datetime
from pathlib import Path
from typing import Optional

from src.common.logger import get_logger
from src.plugin_system import BaseCommand
from src.plugin_system.apis import llm_api, config_api
from .cookie_manager import renew_cookies
from .qzone_api import create_qzone_api
from .utils import send_feed

logger = get_logger("Maizone.commands")
# ===== 插件Command组件 =====
class SendFeedCommand(BaseCommand):
    """发说说Command - 响应/send_feed命令"""

    command_name = "send_feed"
    command_description = "发一条说说"

    command_pattern = r"^/send_feed(?:\s+(?P<topic>\w+))?$"
    command_help = "发一条主题为<topic>或随机的说说"
    command_examples = ["/send_feed", "/send_feed topic"]
    intercept_message = True

    def check_permission(self, qq_account: str) -> bool:
        """检查qq号为qq_account的用户是否拥有权限"""
        permission_list = self.get_config("send.permission")
        permission_type = self.get_config("send.permission_type")
        logger.info(f'[{self.command_name}]{permission_type}:{str(permission_list)}')
        if permission_type == 'whitelist':
            return qq_account in permission_list
        elif permission_type == 'blacklist':
            return qq_account not in permission_list
        else:
            logger.error('permission_type错误，可能为拼写错误')
            return False

    async def execute(self) -> tuple[bool, Optional[str], bool]:
        #权限检查
        user_id = self.message.message_info.user_info.user_id
        if not self.check_permission(user_id):
            logger.info(f"{user_id}无{self.command_name}权限")
            await self.send_text(f"{user_id}权限不足，无权使用此命令")
            return False, f"{user_id}权限不足，无权使用此命令", True
        else:
            logger.info(f"{user_id}拥有{self.command_name}权限")

        topic = self.matched_groups.get("topic")
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]
        if not model_config:
            return False, "未配置LLM模型", True
        # 人格配置
        bot_personality = config_api.get_global_config("personality.personality", "一个机器人")
        bot_expression = config_api.get_global_config("personality.reply_style", "内容积极向上")
        # 核心配置
        port = self.get_config("plugin.http_port", "9999")
        napcat_token = self.get_config("plugin.napcat_token", "")
        host = self.get_config("plugin.http_host", "127.0.0.1")
        cookie_methods = self.get_config("plugin.cookie_methods", ["napcat", "clientkey", "qrcode", "local"])
        # 生成图片相关配置
        enable_image = self.get_config("send.enable_image", "true")
        image_dir = str(Path(__file__).parent.resolve() / "images")
        apikey = self.get_config("models.api_key", "")
        image_mode = self.get_config("send.image_mode", "random").lower()
        ai_probability = self.get_config("send.ai_probability", 0.5)
        image_number = self.get_config("send.image_number", 1)
        # 说说生成相关配置
        history_number = self.get_config("send.history_number", 5)
        # 当前时间
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 更新cookies
        try:
            await renew_cookies(host, port, napcat_token, cookie_methods)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败", True
        qzone = create_qzone_api()
        prompt_pre = self.get_config("send.prompt", "你是'{bot_personality}'，现在是'{current_time}'你想写一条主题是'{topic}'的说说发表在qq空间上，"
                                          "{bot_expression}，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，只输出一条说说正文的内容，不要输出多余内容"
                                          "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )")
        if topic:
            data = {
                "current_time": current_time,
                "bot_personality": bot_personality,
                "topic": topic,
                "bot_expression": bot_expression
            }
            prompt = prompt_pre.format(**data)
        else:
            data = {
                "current_time": current_time,
                "bot_personality": bot_personality,
                "bot_expression": bot_expression,
                "topic": "随机"
            }
            prompt = prompt_pre.format(**data)

        prompt += "\n以下是你以前发过的说说，写新说说时注意不要在相隔不长的时间发送相同主题的说说]\n"
        prompt += await qzone.get_send_history(history_number)
        prompt += "\n不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"

        show_prompt = self.get_config("models.show_prompt", False)
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
            return False, "生成说说内容失败", True

        logger.info(f"成功生成说说内容：'{story}'")

        if image_mode != "only_emoji" and not apikey:
            logger.warning("未配置apikey，无法生成图片，将只使用表情包")
            image_mode = "only_emoji"  # 如果没有apikey，则只使用表情包

        # 发送说说
        success = await send_feed(story, image_dir, enable_image, image_mode, ai_probability, image_number)
        if not success:
            return False, "发送说说失败", True
        await self.send_text(f"已发送说说：\n{story}")
        return True, 'success', True

