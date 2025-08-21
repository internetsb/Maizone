import asyncio
from typing import List, Tuple, Type

from src.plugin_system import BasePlugin, register_plugin, ComponentInfo
from src.plugin_system.base.config_types import ConfigField

from .actions import SendFeedAction, ReadFeedAction
from .commands import SendFeedCommand
from .scheduled_tasks import FeedMonitor, ScheduleSender


@register_plugin
class MaizonePlugin(BasePlugin):
    """Maizone插件 - 让麦麦发QQ空间"""
    plugin_name = "MaizonePlugin"
    plugin_description = "让麦麦实现QQ空间点赞、评论、发说说"
    plugin_version = "2.0.0"
    plugin_author = "internetsb"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ['httpx', 'bs4', 'json5']

    config_section_descriptions = {
        "plugin": "插件启用配置",
        "models": "插件模型配置",
        "send": "发送说说配置",
        "read": "阅读说说配置",
        "monitor": "自动刷空间配置",
        "schedule": "定时发送说说配置",
    }

    config_schema = {
        "plugin": {
            "enable": ConfigField(type=bool, default=True, description="是否启用插件"),
            "http_host": ConfigField(type=str, default='127.0.0.1', description="Napcat设定http服务器地址"),
            "http_port": ConfigField(type=str, default='9999', description="Napcat设定http服务器端口号"),
            "napcat_token": ConfigField(type=str, default="", description="Napcat服务认证Token（默认为空）"),
        },
        "models": {
            "text_model": ConfigField(type=str, default="replyer", description="生成文本的模型（从全局配置读取）"),
            "show_prompt": ConfigField(type=bool, default=False, description="是否显示生成prompt内容"),
            "api_key": ConfigField(type=str, default="", description="SiliconFlow API密钥（用于生成说说配图）"),
        },
        "send": {
            "permission": ConfigField(type=list, default=['114514', '1919810', ],
                                      description="权限QQ号列表（请以相同格式添加）"),
            "permission_type": ConfigField(type=str, default='whitelist',
                                           description="whitelist:在列表中的QQ号有权限，blacklist:在列表中的QQ号无权限"),
            "enable_image": ConfigField(type=bool, default=False, description="是否启用带图片的说说"),
            "image_mode": ConfigField(type=str, default='random',
                                      description="图片使用方式: only_ai(仅AI生成)/only_emoji(仅表情包)/random(随机混合)"),
            "ai_probability": ConfigField(type=float, default=0.5, description="random模式下使用AI图片的概率(0-1)"),
            "image_number": ConfigField(type=int, default=1, description="使用的图片数量(范围1至4)"),
        },
        "read": {
            "permission": ConfigField(type=list, default=['114514', '1919810', ],
                                      description="权限QQ号列表（请以相同格式添加）"),
            "permission_type": ConfigField(type=str, default='blacklist',
                                           description="whitelist:在列表中的QQ号有权限，blacklist:在列表中的QQ号无权限"),
            "read_number": ConfigField(type=int, default=5, description="一次读取最新的几条说说"),
            "like_possibility": ConfigField(type=float, default=1.0, description="麦麦读说说后点赞的概率（0到1）"),
            "comment_possibility": ConfigField(type=float, default=1.0, description="麦麦读说说后评论的概率（0到1）"),
        },
        "monitor": {
            "enable_auto_monitor": ConfigField(type=bool, default=False,
                                               description="是否启用刷空间（自动阅读所有好友说说）"),
            "enable_auto_reply": ConfigField(type=bool, default=False,
                                             description="是否启用自动回复自己说说的评论（当enable_auto_monitor为True）（警告：谨慎开启此项）"),
            "interval_minutes": ConfigField(type=int, default=15, description="阅读间隔(分钟)"),
        },
        "schedule": {
            "enable_schedule": ConfigField(type=bool, default=False, description="是否启用定时发送说说"),
            "schedule_times": ConfigField(type=list, default=["08:00", "20:00"],
                                          description="定时发送时间列表，按照示例添加修改"),
            "random_topic": ConfigField(type=bool, default=True, description="是否使用随机主题"),
            "fixed_topics": ConfigField(type=list, default=["日常生活", "心情分享", "有趣见闻"],
                                        description="固定主题列表（当random_topic为False时从中随机选择）"),
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.monitor = None
        self.scheduler = None

        if self.get_config("plugin.enable", True):
            self.enable_plugin = True

            if self.get_config("monitor.enable_auto_monitor", False):
                self.monitor = FeedMonitor(self)
                asyncio.create_task(self._start_monitor_after_delay())

            if self.get_config("schedule.enable_schedule", False):
                self.scheduler = ScheduleSender(self)
                asyncio.create_task(self._start_scheduler_after_delay())
        else:
            self.enable_plugin = False

    async def _start_monitor_after_delay(self):
        """延迟启动监控任务"""
        await asyncio.sleep(10)
        if self.monitor:
            await self.monitor.start()

    async def _start_scheduler_after_delay(self):
        """延迟启动日程任务"""
        await asyncio.sleep(10)
        if self.scheduler:
            await self.scheduler.start()

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (SendFeedCommand.get_command_info(), SendFeedCommand),
            (SendFeedAction.get_action_info(), SendFeedAction),
            (ReadFeedAction.get_action_info(), ReadFeedAction),
        ]