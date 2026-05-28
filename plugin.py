from maibot_sdk import Command, MaiBotPlugin, Tool, API
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .config import MaizonePluginConfig
from .cookie import renew_cookies, set_api_capability, set_cookie_logger
from .qzone_api import create_qzone_api, set_qzoneapi_logger
from .image import set_images_plugin_context
from .utils import set_utils_plugin_context, read_feed, send_feed
from .tasks import FeedMonitor, ScheduleSender, set_tasks_logger

class MaizonePlugin(MaiBotPlugin):
    config_model = MaizonePluginConfig
    personality = ""
    reply_style = ""
    async def on_load(self):
        """插件加载：检查配置、测试napcat连接、注册定时任务等"""
        set_api_capability(self.ctx.api)
        set_tasks_logger(self.ctx.logger)
        set_qzoneapi_logger(self.ctx.logger)
        set_cookie_logger(self.ctx.logger)
        set_utils_plugin_context(self)
        set_images_plugin_context(self)
        # ===== 检查文本模型是否可用 =====
        available_models = await self.ctx.llm.get_available_models()
        # self.ctx.logger.info(f"可用文本模型：{available_models}")
        text_model = self.config.plugin.text_model # type: ignore
        if text_model not in available_models:
            self.ctx.logger.error(f"文本模型{text_model}不可用，请检查配置")

        # ===== 测试通过adapter或napcat获取cookie =====
        napcat_host = self.config.plugin.http_host # type: ignore
        napcat_port = self.config.plugin.http_port # type: ignore
        napcat_token = self.config.plugin.napcat_token # type: ignore
        if not await renew_cookies(napcat_host, napcat_port, napcat_token, ['adapter'], False) and not await renew_cookies(napcat_host, napcat_port, napcat_token, ['napcat'], False):
            self.ctx.logger.error("通过Napcat获取Cookie失败，请检查配置，或忍受手动扫码登录的麻烦")
        else:
            self.ctx.logger.info("Napcat成功连接")

        # ===== 从主程序获取人格，表达方式等配置 =====
        self.ctx.logger.info("正在加载人格配置...")
        global_config = await self.ctx.config.get("personality", "fail")
        # self.ctx.logger.info(f"已加载人格配置：{global_config}")
        self.personality = global_config.get("personality", "未知")
        self.reply_style = global_config.get("reply_style", "未知")
        self.ctx.logger.info(f"已加载人格配置：personality={self.personality}, reply_style={self.reply_style}")
        # ===== 定时任务注册 =====
        if self.config.auto_read.enable_auto_read: # type: ignore
            self.feed_monitor = FeedMonitor(self) 
            await self.feed_monitor.start()
        if self.config.auto_send.enable_auto_send: # type: ignore
            self.schedule_sender = ScheduleSender(self)
            await self.schedule_sender.start()
        
    async def on_unload(self):
        # 插件卸载：取消定时任务、清理资源等
        if hasattr(self, "feed_monitor"):
            await self.feed_monitor.stop()
        if hasattr(self, "schedule_sender"):
            await self.schedule_sender.stop()

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str):
        # 插件配置更新写入config.toml
        del scope
        del config_data 
        del version
            
    # 权限检查
    def check_permission(self, qq_account: str, tool: str) -> bool:
        """检查qq_account是否有权限使用tool工具
        参数：
        qq_account: QQ账号
        tool: 可为send_feed、read_fead
        """
        send_authority_type = self.config.authority.send_authority_type # type: ignore
        send_whitelist = self.config.authority.send_whitelist # type: ignore
        send_blacklist = self.config.authority.send_blacklist # type: ignore
        read_authority_type = self.config.authority.read_authority_type # type: ignore
        read_whitelist = self.config.authority.read_whitelist # type: ignore
        read_blacklist = self.config.authority.read_blacklist # type: ignore
        if tool == "send_feed":
            if send_authority_type == 'whitelist':
                return qq_account in send_whitelist
            elif send_authority_type == 'blacklist':
                return qq_account not in send_blacklist
            else:
                self.ctx.logger.error('send_authority_type错误')
                return False
        elif tool == "read_feed":
            if read_authority_type == 'whitelist':
                return qq_account in read_whitelist
            elif read_authority_type == 'blacklist':
                return qq_account not in read_blacklist
            else:
                self.ctx.logger.error('read_authority_type错误')
                return False
        else:
            self.ctx.logger.error('tool参数错误')
            return False

    # ========== 发送说说 ==========
    @Command("sendfeed",pattern=r"^/sendfeed\s+(?P<topic>.+)$")
    async def handle_send_feed(self, **kwargs):
        matched = kwargs.get("matched_groups", {})
        topic = matched.get("topic", "").strip()
        stream_id = kwargs["stream_id"]
        user_id = kwargs["user_id"]
        # ===== 检查权限 =====
        if not self.check_permission(user_id, "send_feed"):
            await self.ctx.send.text("Permission denied", stream_id)
            return False, "权限不足", 1
        # ===== 发送说说 =====
        success, message = await send_feed(topic)
        if not success:
            self.ctx.logger.error(message)
        await self.ctx.send.text(message, stream_id)
        return success, message, 1
        

    @Tool(
        name="send_feed",
        description="根据主题生成说说并发布到QQ空间",
        parameters=[
            ToolParameterInfo(name="topic", param_type=ToolParamType.STRING, description="说说主题", required=True),
            ToolParameterInfo(name="nickname", param_type=ToolParamType.STRING, description="要求发送说说的用户的昵称", required=True)
        ],
    )
    async def handle_send_feed_tool(self, topic: str, nickname: str, **kwargs):
        users = await self.ctx.db.get(model_name="PersonInfo", filters={"person_name": nickname})
        user_id = users[0].get("user_id") if users else ""
        # ===== 检查权限 =====
        if not self.check_permission(user_id, "send_feed"):
            # 由主程序回复
            return False, "该用户无权命令发送说说", 1
        # ===== 发送说说 =====
        success, message = await send_feed(topic)
        return success, message, 1

    # ========== 阅读空间 ==========
    @Command("readfeed",pattern=r"^/readfeed\s+(?P<target_name>.+)$")
    async def handle_read_feed(self, **kwargs):
        matched = kwargs.get("matched_groups", {})
        target_name = matched.get("target_name", "").strip()
        target_info = await self.ctx.db.get(model_name="PersonInfo", filters={"person_name": target_name}) # type: ignore
        target_qq = target_info[0].get("user_id") if target_info else ""
        stream_id = kwargs["stream_id"]
        user_id = kwargs["user_id"]
        # ===== 检查权限 =====
        self.ctx.logger.info(f"检查权限，用户ID：{user_id}")
        if not self.check_permission(user_id, "read_feed"):
            await self.ctx.send.text("Permission denied", stream_id)
            return False, "权限不足", 1
        # ===== 阅读空间 =====
        self.ctx.logger.info(f"开始阅读{target_name}的说说，QQ号：{target_qq}")
        success, message = await read_feed(target_qq)
        if not success:
            self.ctx.logger.error(message)
            await self.ctx.send.text(str(message), stream_id)
            return success, str(message), 1
        await self.ctx.send.text(f"已阅读{len(message)}条说说", stream_id)
        return success, str(message), 1
    
    @Tool(
        name="read_feed",
        description="阅读QQ空间说说",
        parameters=[
            ToolParameterInfo(name="nickname", param_type=ToolParamType.STRING, description="要求阅读说说的用户的昵称", required=True),
            ToolParameterInfo(name="target_name", param_type=ToolParamType.STRING, description="要求阅读说说的目标用户昵称", required=True)
        ],
    )
    async def handle_read_feed_tool(self, nickname: str, target_name: str, **kwargs):
        user_info = await self.ctx.db.get(model_name="PersonInfo", filters={"person_name": nickname})
        user_id = user_info[0].get("user_id") if user_info else ""
        target_info = await self.ctx.db.get(model_name="PersonInfo", filters={"person_name": target_name})
        target_qq = target_info[0].get("user_id") if target_info else ""
        # ===== 检查权限 =====
        if not self.check_permission(user_id, "read_feed"):
            # 由主程序回复
            return False, "该用户无权命令阅读说说", 1
        # ===== 阅读空间 =====
        success, message = await read_feed(target_qq)
        return success, str(message), 1
    
    @API(
        name="send_feed_api",
        description="发送说说，参数：message：文本内容（可选），images：图片二进制数据列表（可选）",
        version="1",
        public=True
    )
    async def send_feed_api(self, message: str = "", images: list[bytes] = [], **kwargs):
        """API版本的发送说说工具，供其他插件调用"""
        await renew_cookies(self.config.plugin.http_host, self.config.plugin.http_port, self.config.plugin.napcat_token, ['adapter', 'napcat'], True) # type: ignore
        qzone = create_qzone_api()
        if qzone is None:
            return {"result": False, "message": "无法创建QzoneAPI实例，发送说说失败"}
        fid = await qzone.publish_emotion(content=message, images=images)
        if fid is None:
            return {"result": False, "message": "发送说说失败"}
        else:
            return {"result": True, "message": f"说说发送成功，动态ID：{fid}"}
    
    @API(
        name="get_feeds_list_api",
        description="获取指定QQ号的说说列表，参数：target_qq：目标QQ号，num：获取数量（默认5），filter：是否过滤已评论过的说说（默认False）",
        version="1",
        public=True
    )
    async def get_feeds_list_api(self, target_qq: str, num: int = 5, filter: bool = False, **kwargs):
        """API版本的获取说说列表工具"""
        await renew_cookies(self.config.plugin.http_host, self.config.plugin.http_port, self.config.plugin.napcat_token, ['adapter', 'napcat'], True) # type: ignore
        qzone = create_qzone_api()
        if qzone is None:
            return {"result": False, "message": "无法创建QzoneAPI实例，获取列表失败"}
        feeds_list = await qzone.get_list(target_qq, num, filter)
        if not feeds_list or (len(feeds_list) == 1 and "error" in feeds_list[0]):
            return {"result": False, "message": f"获取说说列表失败：{feeds_list[0].get('error', '未知错误') if feeds_list else '返回列表为空'}"}
        else:
            return {"result": True, "message": f"成功获取{len(feeds_list)}条说说", "data": feeds_list}


def create_plugin() -> MaizonePlugin:
    """创建插件实例。"""
    return MaizonePlugin() 