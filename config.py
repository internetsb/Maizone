from maibot_sdk import Field, PluginConfigBase
from pathlib import Path
from typing import Literal

config_file_path = Path(__file__).parent / "config.toml"


class PluginSectionConfig(PluginConfigBase):
    """连接 NapCat/SnowLuma、选择文本模型与 Cookie 获取方式。"""

    __ui_label__ = "基础配置"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="插件总开关，关闭后命令、工具和定时任务都不会执行。",
        json_schema_extra={
            "label": "启用插件",
            "hint": "关闭后所有功能和定时任务停止。",
        },
    )
    config_version: float = Field(
        default=1.0,
        description="配置版本号，由插件维护，无需手动修改。",
        json_schema_extra={
            "label": "配置版本",
            "hidden": True,
        },
    )
    text_model: str = Field(
        default="replyer",
        description="调用主程序模型时使用的任务名，决定生成说说和评论使用哪套模型配置。",
        json_schema_extra={
            "label": "文本模型任务名",
            "hint": "填 model_config.toml 中的任务名，常用 replyer、utils、tool_use。",
            "placeholder": "replyer",
        },
    )
    http_host: str = Field(
        default="127.0.0.1",
        description="备用 NapCat HTTP 服务地址，用于自动获取 Cookie。",
        json_schema_extra={
            "label": "NapCat HTTP 地址",
            "hint": "Docker 部署可填 napcat；留空或连接失败时改用扫码登录。",
            "placeholder": "127.0.0.1",
        },
    )
    http_port: int = Field(
        default=9999,
        description="备用 NapCat HTTP 服务端口，需与 NapCat 中的配置一致。",
        json_schema_extra={
            "label": "NapCat HTTP 端口",
            "hint": "与 NapCat 的 HTTP 服务器端口一致，默认 9999。",
            "placeholder": "9999",
        },
    )
    napcat_token: str = Field(
        default="",
        description="备用 NapCat HTTP 服务的访问 Token。",
        json_schema_extra={
            "label": "NapCat Token",
            "hint": "NapCat 未设置 Token 时可留空。",
            "placeholder": "与 NapCat 配置保持一致",
        },
    )
    cookie_methods: list[str] = Field(
        default=["adapter", "napcat", "qrcode", "local"],
        description="Cookie 获取方式，按列表顺序依次尝试。",
        json_schema_extra={
            "label": "Cookie 获取方式",
            "hint": "按顺序尝试，可填 adapter、napcat、qrcode、local；排在前面的优先。",
            "placeholder": "adapter",
        },
    )


class SendConfig(PluginConfigBase):
    """手动发说说时的历史回顾、配图、聊天素材与提示词设置。"""

    __ui_label__ = "发说说"
    __ui_icon__ = "send"
    __ui_order__ = 1

    history_number: int = Field(
        default=5,
        description="生成新说说时回顾的历史说说数量，用于减少内容重复。",
        json_schema_extra={
            "label": "回顾历史说说数",
            "hint": "0 表示不回顾；数值越大越不容易重复，但 token 消耗越高。",
            "placeholder": "5",
        },
    )
    enable_image: bool = Field(
        default=True,
        description="是否在发说说时附带配图。",
        json_schema_extra={
            "label": "附加配图",
            "hint": "关闭后只发文字说说。",
        },
    )
    image_mode: Literal["only_emoji", "only_ai", "random"] = Field(
        default="only_emoji",
        description="配图来源：表情包、AI 生图或随机混合。",
        json_schema_extra={
            "label": "配图来源",
            "hint": "only_emoji=仅表情包；only_ai=仅 AI 生图；random=按概率混合。",
        },
    )
    ai_probability: float = Field(
        default=0.5,
        description="random 模式下使用 AI 生图的概率，取值 0~1。",
        json_schema_extra={
            "label": "AI 生图概率",
            "hint": "仅 random 模式生效，0~1；0.5 表示约一半概率使用 AI 生图。",
            "placeholder": "0.5",
        },
    )
    image_number: int = Field(
        default=1,
        description="每条说说附带的图片数量。",
        json_schema_extra={
            "label": "每条说说配图数量",
            "hint": "表情包数量不足或生图失败时会自动减少。",
            "placeholder": "1",
        },
    )
    prompt: str = Field(
        default="你是'{bot_personality}'，现在是'{current_time}'你想写一条主题是'{topic}'的说说发表在qq空间上，"
                "{bot_expression}，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，只输出一条说说正文的内容，不要输出多余内容"
                "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )",
        description="生成说说正文的提示词，支持占位符替换。",
        json_schema_extra={
            "label": "说说生成提示词",
            "hint": "可用占位符：{bot_personality}、{current_time}、{topic}、{bot_expression}。",
            "x-widget": "textarea",
            "rows": 8,
        },
    )
    enable_chat_memory: bool = Field(
        default=True,
        description="是否读取近期聊天记录，作为说说素材。",
        json_schema_extra={
            "label": "引用聊天记录",
            "hint": "开启后会按下方筛选范围读取聊天记录，供模型参考。",
        },
    )
    filter_mode: Literal["all", "whitelist", "blacklist"] = Field(
        default="all",
        description="聊天记录的会话筛选方式。",
        json_schema_extra={
            "label": "聊天记录筛选范围",
            "hint": "all=全部会话；whitelist=仅下方列表；blacklist=排除下方列表。",
        },
    )
    target_chats: list[str] = Field(
        default=[],
        description="筛选模式对应的会话列表。",
        json_schema_extra={
            "label": "指定会话列表",
            "hint": "每行一项，格式 group:群号 或 private:QQ号，例如 group:123456。",
            "placeholder": "group:123456",
        },
    )
    time_range: Literal["today", "last_n_hours"] = Field(
        default="today",
        description="读取聊天记录的时间范围。",
        json_schema_extra={
            "label": "聊天记录时间范围",
            "hint": "today=今天 00:00 到现在；last_n_hours=最近 N 小时。",
        },
    )
    last_hours: int = Field(
        default=6,
        description="time_range 为 last_n_hours 时读取的小时数。",
        json_schema_extra={
            "label": "最近 N 小时",
            "hint": "仅在时间范围为 last_n_hours 时生效，单位小时。",
            "placeholder": "6",
        },
    )
    min_messages: int = Field(
        default=5,
        description="使用聊天素材所需的最少消息条数。",
        json_schema_extra={
            "label": "最少聊天条数",
            "hint": "不足该条数时视为今日平淡，改用下方兜底提示。",
            "placeholder": "5",
        },
    )
    max_messages: int = Field(
        default=50,
        description="读取聊天记录的最大条数。",
        json_schema_extra={
            "label": "最多聊天条数",
            "hint": "超出后截断；仅在处理方式为 direct 时生效。",
            "placeholder": "50",
        },
    )
    per_message_max_chars: int = Field(
        default=200,
        description="单条聊天记录允许的最大字符数。",
        json_schema_extra={
            "label": "单条消息最大字数",
            "hint": "超出后截断为“前缀...”；填 0 表示不截断。",
            "placeholder": "200",
        },
    )
    chat_memory_mode: Literal["direct", "summary", "hybrid"] = Field(
        default="hybrid",
        description="聊天记录的处理方式。",
        json_schema_extra={
            "label": "聊天记录处理方式",
            "hint": "direct=直接取最近 N 条；summary=LLM 摘要；hybrid=最近 N 条+更早摘要。",
        },
    )
    summary_model: str = Field(
        default="replyer",
        description="summary 或 hybrid 模式下生成摘要使用的模型任务名。",
        json_schema_extra={
            "label": "摘要模型任务名",
            "hint": "填 model_config.toml 中的任务名，如 replyer、utils。",
            "placeholder": "replyer",
        },
    )
    summary_prompt: str = Field(
        default="请用简洁的语言概括以下聊天记录的核心内容，提取今天发生的主要事件和话题，不超过200字：\n{chat_logs}",
        description="summary 或 hybrid 模式下使用的摘要提示词。",
        json_schema_extra={
            "label": "聊天摘要提示词",
            "hint": "必须保留占位符 {chat_logs}，插件会把聊天记录填入此处。",
            "x-widget": "textarea",
            "rows": 4,
        },
    )
    hybrid_recent_count: int = Field(
        default=10,
        description="hybrid 模式下直接保留的最近消息条数。",
        json_schema_extra={
            "label": "hybrid 保留条数",
            "hint": "仅 hybrid 模式生效；更早的消息会被摘要压缩。",
            "placeholder": "10",
        },
    )
    fallback_prompt: str = Field(
        default="今天比较平淡，没有太多特别的对话，你可以根据主题自由发挥。",
        description="聊天记录不足时提供给模型的兜底提示。",
        json_schema_extra={
            "label": "素材不足提示",
            "hint": "聊天条数少于“最少聊天条数”时使用这段提示。",
            "x-widget": "textarea",
            "rows": 2,
        },
    )


class ReadConfig(PluginConfigBase):
    """手动读取好友说说时的数量、点赞评论概率与提示词。"""

    __ui_label__ = "读说说"
    __ui_icon__ = "visibility"
    __ui_order__ = 2

    read_number: int = Field(
        default=5,
        description="每次读取目标用户的最新说说数量。",
        json_schema_extra={
            "label": "读取说说数量",
            "hint": "建议 1~10，读取越多请求耗时越长。",
            "placeholder": "5",
        },
    )
    like_probability: float = Field(
        default=1.0,
        description="读取每条说说后点赞的概率，取值 0~1。",
        json_schema_extra={
            "label": "点赞概率",
            "hint": "0~1；1 表示每条都点赞，0 表示不点赞。",
            "placeholder": "1.0",
        },
    )
    comment_probability: float = Field(
        default=1.0,
        description="读取每条说说后评论的概率，取值 0~1。",
        json_schema_extra={
            "label": "评论概率",
            "hint": "0~1；1 表示每条都评论，0 表示不评论。",
            "placeholder": "1.0",
        },
    )
    allow_skip_comment: bool = Field(
        default=True,
        description="是否允许模型根据内容选择不评论。",
        json_schema_extra={
            "label": "允许跳过评论",
            "hint": "开启后模型输出“不回复”即跳过；关闭后只要命中评论概率就会评论。",
        },
    )
    prompt: str = Field(
        default="你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
                "在qq空间上在'{created_time}'发了一条内容是'{content}'的说说，现在是'{current_time}'"
                "你对'{target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
                "{bot_expression}，回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容"
                "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。若没必要评论只输出不回复，否则只输出评论正文",
        description="对普通说说生成评论的提示词。",
        json_schema_extra={
            "label": "普通说说评论提示词",
            "hint": "可用占位符：{bot_personality}、{target_name}、{created_time}、{content}、{impression}、{current_time}、{bot_expression}。",
            "x-widget": "textarea",
            "rows": 7,
        },
    )
    rt_prompt: str = Field(
        default="你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
                "在qq空间上在'{created_time}'转发了一条内容为'{rt_con}'的说说，你的好友的评论为'{content}'，你对'{"
                "target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
                "现在是'{current_time}'，{bot_expression}，"
                "回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
                "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。若没必要评论只输出不回复，否则只输出评论正文",
        description="对转发说说生成评论的提示词。",
        json_schema_extra={
            "label": "转发说说评论提示词",
            "hint": "可用占位符：{bot_personality}、{target_name}、{created_time}、{rt_con}、{content}、{impression}、{current_time}、{bot_expression}。",
            "x-widget": "textarea",
            "rows": 7,
        },
    )


class AutoSendConfig(PluginConfigBase):
    """按时间表自动发说说，可设置概率、时间浮动与主题来源。"""

    __ui_label__ = "自动发说说"
    __ui_icon__ = "schedule"
    __ui_order__ = 3

    enable_auto_send: bool = Field(
        default=False,
        description="是否启用定时自动发说说。",
        json_schema_extra={
            "label": "启用自动发说说",
            "hint": "开启后按下方时间表发送，并继承“发说说”页的配图与提示词设置。",
        },
    )
    daily_probability: float = Field(
        default=0.3,
        description="每天决定是否发说说的概率，取值 0~1。",
        json_schema_extra={
            "label": "每天发送概率",
            "hint": "每天 0 点重新抽取；0.3 表示约三成日子会发。",
            "placeholder": "0.3",
        },
    )
    schedule: list[str] = Field(
        default=["08:00", "20:00"],
        description="每天发送说说的时间点。",
        json_schema_extra={
            "label": "发送时间表",
            "hint": "每行一个 24 小时制时间，格式 HH:MM，例如 08:00。",
            "placeholder": "08:00",
        },
    )
    fluctuation: int = Field(
        default=60,
        description="在时间表基础上随机浮动的时间范围。",
        json_schema_extra={
            "label": "时间浮动范围",
            "hint": "单位分钟；60 表示在计划时间前后 60 分钟内随机。",
            "placeholder": "60",
        },
    )
    random_topic: bool = Field(
        default=True,
        description="是否让模型自行决定说说主题。",
        json_schema_extra={
            "label": "由模型决定主题",
            "hint": "关闭后从下方固定主题列表中随机选择。",
        },
    )
    fixed_topic: list[str] = Field(
        default=["散文鉴赏", "今天也要加油", "日常碎片", "哲学小谈", "今天吃什么", "看书打卡", "想不到发什么了", "运动健身", "打游戏", "丧文化", "灵感碎片"],
        description="关闭模型决定主题后使用的固定主题列表。",
        json_schema_extra={
            "label": "固定主题列表",
            "hint": "每行一个主题，随机选取；仅在关闭“由模型决定主题”时生效。",
            "placeholder": "今天吃什么",
        },
    )


class AutoReadConfig(PluginConfigBase):
    """定时浏览好友空间并点赞评论，可设置间隔与静默时段。"""

    __ui_label__ = "自动读说说"
    __ui_icon__ = "autorenew"
    __ui_order__ = 4

    enable_auto_read: bool = Field(
        default=True,
        description="是否启用定时自动读取说说。",
        json_schema_extra={
            "label": "启用自动读说说",
            "hint": "开启后按下方间隔定时浏览，并继承“读说说”页的点赞评论设置。",
        },
    )
    interval: int = Field(
        default=15,
        description="两次自动读取之间的间隔时间。",
        json_schema_extra={
            "label": "读取间隔",
            "hint": "单位分钟；建议不低于 5，避免请求过于频繁。",
            "placeholder": "15",
        },
    )
    silent_duration: str = Field(
        default="22:00-07:00",
        description="不执行自动读取和自动回复的时间段。",
        json_schema_extra={
            "label": "静默时间段",
            "hint": "格式 HH:MM-HH:MM，支持跨天；多个时段用英文逗号分隔。",
            "placeholder": "22:00-07:00",
        },
    )


class AutoReplyConfig(PluginConfigBase):
    """定时回复自己说说下收到的新评论。"""

    __ui_label__ = "自动回评论"
    __ui_icon__ = "reply"
    __ui_order__ = 5

    enable_auto_reply: bool = Field(
        default=True,
        description="是否在自动读取时回复自己说说下的新评论。",
        json_schema_extra={
            "label": "启用自动回评论",
            "hint": "跟随“自动读说说”的间隔与静默时间段执行。",
        },
    )
    reply_number: int = Field(
        default=5,
        description="检查自己最新多少条说说下的评论。",
        json_schema_extra={
            "label": "检查最新说说数",
            "hint": "数值越大检查范围越广，请求耗时也越长。",
            "placeholder": "5",
        },
    )
    reply_probability: float = Field(
        default=1.0,
        description="对每条新评论执行回复的概率，取值 0~1。",
        json_schema_extra={
            "label": "回复概率",
            "hint": "0~1；1 表示每条评论都回复，0 表示不回复。",
            "placeholder": "1.0",
        },
    )
    prompt: str = Field(
        default="你是'{bot_personality}'，你的好友'{nickname}'在'{created_time}'评论了你QQ空间上的一条内容为"
                "'{content}'的说说，你的好友对该说说的评论为:'{comment_content}'，"
                "现在是'{current_time}'，你想要对此评论进行回复，你对该好友的印象是:"
                "'{impression}'，若与你的印象点相关，可以适当回复相关内容，无关则忽略此印象，"
                "{bot_expression}，回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
                "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容",
        description="自动回复评论使用的提示词。",
        json_schema_extra={
            "label": "评论回复提示词",
            "hint": "可用占位符：{bot_personality}、{nickname}、{created_time}、{content}、{comment_content}、{current_time}、{impression}、{bot_expression}。",
            "x-widget": "textarea",
            "rows": 7,
        },
    )


class ImageGenerateConfig(PluginConfigBase):
    """使用 OpenAI 兼容接口生成说说配图。"""

    __ui_label__ = "AI 生图"
    __ui_icon__ = "image"
    __ui_order__ = 6

    base_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        description="OpenAI 兼容生图服务的接口地址。",
        json_schema_extra={
            "label": "生图服务地址",
            "hint": "需包含 /v1 或服务要求的版本路径，默认使用火山引擎 Ark。",
            "placeholder": "https://ark.cn-beijing.volces.com/api/v3",
        },
    )
    model: str = Field(
        default="doubao-seedream-5-0-260128",
        description="调用生图服务时使用的模型名称。",
        json_schema_extra={
            "label": "生图模型",
            "hint": "填写服务商提供的模型 ID，需与服务地址匹配。",
            "placeholder": "doubao-seedream-5-0-260128",
        },
    )
    api_key: str = Field(
        default="your_api_key",
        description="生图服务的 API Key。",
        json_schema_extra={
            "label": "生图 API Key",
            "hint": "请填写真实 Key；使用火山引擎时可在方舟控制台创建。",
            "placeholder": "sk-...",
            "x-widget": "password",
        },
    )
    enable_reference: bool = Field(
        default=False,
        description="是否把参考图随提示词一起提交，启用图生图。",
        json_schema_extra={
            "label": "启用参考图",
            "hint": "需要生图模型支持图生图；关闭时只使用文字提示词。",
        },
    )
    reference: str = Field(
        default="",
        description="参考图地址，支持网络 URL 或本地图片路径。",
        json_schema_extra={
            "label": "参考图地址",
            "hint": "填 http(s) 开头 URL，或插件目录内的本地路径；启用参考图时必填。",
            "placeholder": "https://example.com/avatar.png 或 ./reference.jpg",
        },
    )
    prompt: str = Field(
        default="请根据以下QQ空间说说内容配图，并构建生成配图的风格和prompt。说说主人信息：'{personality}'。说说内容:'{"
                "message}'。请注意：仅回复用于生成图片的prompt，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )",
        description="让模型根据说说内容生成生图提示词的提示词。",
        json_schema_extra={
            "label": "生图提示词",
            "hint": "可用占位符：{personality}、{message}；模型只应输出图片提示词。",
            "x-widget": "textarea",
            "rows": 5,
        },
    )
    ref_prompt: str = Field(
        default="说说主人的人设参考图片将随同提示词一起发送给生图AI，可使用'以参考图片风格'或'根据图中人物'等描述引导生成风格",
        description="启用参考图时追加到生图提示词后的说明。",
        json_schema_extra={
            "label": "参考图附加提示词",
            "hint": "仅在启用参考图时生效，用于引导模型参考人物或画风。",
            "x-widget": "textarea",
            "rows": 3,
        },
    )


class AuthorityConfig(PluginConfigBase):
    """用黑名单或白名单控制谁能发说说、读说说与自动阅读。"""

    __ui_label__ = "权限配置"
    __ui_icon__ = "security"
    __ui_order__ = 7

    send_authority_type: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        description="发说说指令的权限控制方式。",
        json_schema_extra={
            "label": "发说说权限模式",
            "hint": "blacklist=名单内禁止；whitelist=仅名单内允许。",
        },
    )
    send_whitelist: list[str] = Field(
        default=["123456", "350234"],
        description="允许使用发说说指令的 QQ 号列表。",
        json_schema_extra={
            "label": "发说说白名单",
            "hint": "每行一个 QQ 号；仅在权限模式为 whitelist 时生效，留空则无人可用。",
            "placeholder": "123456",
        },
    )
    send_blacklist: list[str] = Field(
        default=["123456", "350234"],
        description="禁止使用发说说指令的 QQ 号列表。",
        json_schema_extra={
            "label": "发说说黑名单",
            "hint": "每行一个 QQ 号；仅在权限模式为 blacklist 时生效。",
            "placeholder": "123456",
        },
    )
    read_authority_type: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        description="读说说指令的权限控制方式。",
        json_schema_extra={
            "label": "读说说权限模式",
            "hint": "blacklist=名单内禁止；whitelist=仅名单内允许。",
        },
    )
    read_whitelist: list[str] = Field(
        default=["123456", "350234"],
        description="允许使用读说说指令的 QQ 号列表。",
        json_schema_extra={
            "label": "读说说白名单",
            "hint": "每行一个 QQ 号；仅在权限模式为 whitelist 时生效，留空则无人可用。",
            "placeholder": "123456",
        },
    )
    read_blacklist: list[str] = Field(
        default=["123456", "350234"],
        description="禁止使用读说说指令的 QQ 号列表。",
        json_schema_extra={
            "label": "读说说黑名单",
            "hint": "每行一个 QQ 号；仅在权限模式为 blacklist 时生效。",
            "placeholder": "123456",
        },
    )
    auto_read_authority_type: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        description="自动阅读任务的权限控制方式。",
        json_schema_extra={
            "label": "自动阅读权限模式",
            "hint": "blacklist=跳过名单内的好友；whitelist=只阅读名单内的好友。",
        },
    )
    auto_read_whitelist: list[str] = Field(
        default=[],
        description="自动阅读优先处理并仅允许读取的 QQ 号列表。",
        json_schema_extra={
            "label": "自动阅读白名单",
            "hint": "每行一个 QQ 号；白名单内的好友会被优先读取，留空则只按权限模式处理。",
            "placeholder": "123456",
        },
    )
    auto_read_blacklist: list[str] = Field(
        default=["123456", "350234"],
        description="自动阅读时跳过的 QQ 号列表。",
        json_schema_extra={
            "label": "自动阅读黑名单",
            "hint": "每行一个 QQ 号；仅在权限模式为 blacklist 时生效。",
            "placeholder": "123456",
        },
    )


class MaizonePluginConfig(PluginConfigBase):
    """插件总配置。"""
    plugin: PluginSectionConfig = Field(
        default_factory=PluginSectionConfig,
        description="插件基础配置"
    )
    send: SendConfig = Field(
        default_factory=SendConfig,
        description="指令发说说配置"
    )
    read: ReadConfig = Field(
        default_factory=ReadConfig,
        description="指令读说说配置"
    )
    auto_send: AutoSendConfig = Field(
        default_factory=AutoSendConfig,
        description="自动发说说配置"
    )
    auto_read: AutoReadConfig = Field(
        default_factory=AutoReadConfig,
        description="自动读说说配置"
    )
    auto_reply: AutoReplyConfig = Field(
        default_factory=AutoReplyConfig,
        description="自动回评论配置"
    )
    image: ImageGenerateConfig = Field(
        default_factory=ImageGenerateConfig,
        description="AI生图配置"
    )
    authority: AuthorityConfig = Field(
        default_factory=AuthorityConfig,
        description="权限配置"
    )
