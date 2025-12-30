import sys, os
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
    plugin_author = "internetsb"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ['httpx', 'Pillow', 'bs4', 'json5']

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
            "napcat_token": ConfigField(type=str, default="", description="Napcat服务认证Token"),
            "cookie_methods": ConfigField(type=list, default=['napcat', 'clientkey', 'qrcode', 'local', ], description="获取Cookie的方法，顺序尝试，可选napcat,clientkey,qrcode,local"),
        },
        "models": {
            "text_model": ConfigField(type=str, default="replyer",
                                      description="生成文本的模型（从麦麦model_config读取），默认即可"),
            "image_provider": ConfigField(type=str, default="volcengine",
                                          description="图片生成服务提供商（默认支持ModelScope或SiliconFlow或volcengine）"),
            "image_model": ConfigField(type=str, default="doubao-seedream-4-5-251128",
                                       description="图片生成模型（从对应服务商官网获取）"),
            "image_ref": ConfigField(type=bool, default=False,
                                     description="是否启用人设参考图（请重命名为done_ref，图片后缀不变，放入images文件夹）"),
            "api_key": ConfigField(type=str, default="", description="相应提供商的API密钥（用于生成说说配图）"),
            "image_size": ConfigField(type=str, default="",
                                      description="生成图片的尺寸，如1024x768，是否支持请参看具体模型说明，为空则不限制"),
            "image_prompt": ConfigField(type=str,
                                        default="请根据以下QQ空间说说内容配图，并构建生成配图的风格和prompt。说说主人信息：'{personality}'。说说内容:'{"
                                                "message}'。请注意：仅回复用于生成图片的prompt，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )",
                                        description="图片生成提示词，"
                                                    "占位符包括{personality}（说说主人信息），{message}（说说内容）,{current_time}（当前时间）"),
            "image_ref_prompt": ConfigField(type=str,
                                            default="说说主人的人设参考图片将随同提示词一起发送给生图AI，可使用'in the style of'或'根据图中人物'等描述引导生成风格",
                                            description="当image_ref为True时，附加在image_prompt后面的提示词"),
            "show_prompt": ConfigField(type=bool, default=False, description="是否显示生成prompt内容"),
        },
        "send": {
            "permission": ConfigField(type=list, default=['114514', '1919810', '1523640161'],
                                      description="权限QQ号列表（请以相同格式添加）"),
            "permission_type": ConfigField(type=str, default='whitelist',
                                           description="whitelist:在列表中的QQ号有权限，blacklist:在列表中的QQ号无权限"),
            "enable_image": ConfigField(type=bool, default=False, description="是否启用带图片的说说"),
            "image_mode": ConfigField(type=str, default='random',
                                      description="图片使用方式: only_ai(仅AI生成)/only_emoji(仅表情包)/random(随机混合)"),
            "ai_probability": ConfigField(type=float, default=0.5, description="random模式下使用AI图片的概率(0-1)"),
            "image_number": ConfigField(type=int, default=1,
                                        description="使用的图片或表情包数量(范围1至4)(仅部分模型支持多图，如Kolors)"),
            "history_number": ConfigField(type=int, default=5,
                                          description="生成说说时参考的历史说说数量，越多越能避免重复内容，但会增加token消耗"),
            "prompt": ConfigField(type=str,
                                  default="你是'{bot_personality}'，现在是'{current_time}'你想写一条主题是'{topic}'的说说发表在qq空间上，"
                                          "{bot_expression}，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，只输出一条说说正文的内容，不要输出多余内容"
                                          "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )",
                                  description="生成说说的提示词，占位符包括{current_time}（当前时间），{bot_personality}（人格），{topic}（说说主题），{"
                                              "bot_expression}（表达方式）"),
        },
        "read": {
            "permission": ConfigField(type=list, default=['114514', '1919810', ],
                                      description="权限QQ号列表（请以相同格式添加）"),
            "permission_type": ConfigField(type=str, default='blacklist',
                                           description="whitelist:在列表中的QQ号有权限，blacklist:在列表中的QQ号无权限"),
            "read_number": ConfigField(type=int, default=5, description="一次读取最新的几条说说"),
            "like_possibility": ConfigField(type=float, default=1.0, description="麦麦读说说后点赞的概率（0到1）"),
            "comment_possibility": ConfigField(type=float, default=1.0, description="麦麦读说说后评论的概率（0到1）"),
            "prompt": ConfigField(type=str,
                                  default="你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
                                          "在qq空间上在'{created_time}'发了一条内容是'{content}'的说说，你想要发表你的一条评论，现在是'{current_time}'"
                                          "你对'{target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
                                          "{bot_expression}，回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容"
                                          "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容",
                                  description="对无转发内容说说进行评论的提示词，占位符包括{current_time}（当前时间），{bot_personality}（人格），"
                                              "{target_name}（说说主人名称），{created_time}（说说发布时间），"
                                              "{content}（说说内容），{impression}（对说说主人的印象点），{bot_expression}（表达方式）"),
            "rt_prompt": ConfigField(type=str,
                                     default="你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
                                             "在qq空间上在'{created_time}'转发了一条内容为'{rt_con}'的说说，你的好友的评论为'{content}'，你对'{"
                                             "target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
                                             "现在是'{current_time}'，你想要发表你的一条评论，{bot_expression}，"
                                             "回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
                                             "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容",
                                     description="对转发的说说进行评论的提示词，占位符包括{current_time}（当前时间），{bot_personality}（人格），{"
                                                 "target_name}（说说主人名称），{created_time}（说说发布时间），{"
                                                 "content}（说说评论内容），{rt_con}（转发说说内容），{impression}（对说说主人的印象点），{"
                                                 "bot_expression}（表达方式）"),
        },
        "monitor": {
            "enable_auto_monitor": ConfigField(type=bool, default=False,
                                               description="是否启用刷空间（自动阅读所有好友说说）"),
            "enable_auto_reply": ConfigField(type=bool, default=False,
                                             description="是否启用自动回复自己说说的评论（当enable_auto_monitor为True）"),
            "self_readnum": ConfigField(type=int, default=5,
                                        description="需要回复评论的自己最新说说数量"),
            "interval_minutes": ConfigField(type=int, default=15, description="阅读间隔(分钟)"),
            "silent_hours": ConfigField(type=str, default="22:00-07:00",
                                        description="不刷空间的时间段（24小时制，格式\"HH:MM-HH:MM\"，多个时间段用逗号分隔，如\"23:00-07:00,12:00-14:00\"）"),
            "like_during_silent": ConfigField(type=bool, default=False,
                                              description="在静默时间段内是否仍然点赞（true=静默时间段内仍然点赞，false=静默时间段内不点赞）"),
            "comment_during_silent": ConfigField(type=bool, default=False,
                                                 description="在静默时间段内是否仍然评论（true=静默时间段内仍然评论，false=静默时间段内不评论）"),
            "like_possibility": ConfigField(type=float, default=1.0, 
                                          description="定时任务读说说后点赞的概率（0到1）"),
            "comment_possibility": ConfigField(type=float, default=1.0, 
                                            description="定时任务读说说后评论的概率（0到1）"),
            "processed_comments_cache_size": ConfigField(type=int, default=100,
                                                        description="已处理评论缓存的最大大小，与对评论的回复有关"),
            "processed_feeds_cache_size": ConfigField(type=int, default=100,
                                                      description="已处理说说数据的最大大小，与对说说的评论有关"),
            "reply_prompt": ConfigField(type=str,
                                        default="你是'{bot_personality}'，你的好友'{nickname}'在'{created_time}'评论了你QQ空间上的一条内容为"
                                                "'{content}'的说说，你的好友对该说说的评论为:'{comment_content}'，"
                                                "现在是'{current_time}'，你想要对此评论进行回复，你对该好友的印象是:"
                                                "'{impression}'，若与你的印象点相关，可以适当回复相关内容，无关则忽略此印象，"
                                                "{bot_expression}，回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
                                                "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容",
                                        description="自动回复评论的提示词，占位符包括{current_time}（当前时间），{bot_personality}（人格），{"
                                                    "nickname}（评论者昵称），{created_time}（评论时间），{"
                                                    "content}（说说内容），{comment_content}（评论内容），{impression}（对评论者的印象点），{"
                                                    "bot_expression}（表达方式）"),
        },
        "schedule": {
            "enable_schedule": ConfigField(type=bool, default=False, description="是否启用定时发送说说"),
            "probability": ConfigField(type=float, default=1.0, description="每天发送说说的概率"),
            "schedule_times": ConfigField(type=list, default=["08:00", "20:00"],
                                          description="定时发送时间列表，按照示例添加或修改"),
            "fluctuation_minutes": ConfigField(type=int, default=0,
                                               description="发送时间上下浮动范围（分钟），0表示不浮动"),
            "random_topic": ConfigField(type=bool, default=True,
                                        description="是否使用随机主题（可能会导致重复说说的发布，请关注history_number的设置）"),
            "fixed_topics": ConfigField(type=list,
                                        default=["今日穿搭", "日常碎片PLOG", "生活仪式感", "治愈系天空", "理想的家",
                                                 "周末去哪儿", "慢生活", "今天吃什么呢", "懒人食谱", "居家咖啡馆",
                                                 "探店美食", "说走就走的旅行", "小众旅行地", "治愈系风景", "一起去露营",
                                                 "逛公园", "博物馆奇遇", "穿搭灵感", "复古穿搭", "今日妆容", "护肤日常",
                                                 "小众品牌", "我家宠物好可爱", "阳台花园", "运动打卡", "瑜伽日常",
                                                 "轻食记", "看书打卡", "我的观影报告", "咖啡店日记", "手帐分享",
                                                 "画画日常", "手工DIY", "沙雕日常", "沉浸式体验", "开箱视频",
                                                 "提升幸福感的小物", "圣诞氛围感", "冬日限定快乐", "灵感碎片",
                                                 "艺术启蒙", "色彩美学", "每日一诗", "哲学小谈", "存在主义咖啡馆",
                                                 "艺术史趣闻", "审美积累", "现代主义漫步", "东方美学"],
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
