import datetime
import asyncio
import random
import time
from .utils import read_feed, send_feed, monitor_read_feed, reply_feed

# ===== logger =====
class NoLogger:
    def info(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        pass
    def debug(self, msg):
        pass
logger = NoLogger()
def set_tasks_logger(custom_logger):
    global logger
    logger = custom_logger


def _is_in_silent_period(silent_hours_config: str) -> bool:
    """
    检查当前时间是否在静默时间段内

    Args:
        silent_hours_config: 静默时间段配置，格式如"23:00-07:00,12:00-14:00"

    Returns:
        bool: 是否在静默时间段
    """
    if not silent_hours_config or not silent_hours_config.strip():
        return False

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

        # 返回是否在静默时间段
        return is_silent

    except Exception as e:
        logger.error(f"解析静默时间段配置失败: {str(e)}")
        return False


def _parse_time_to_minutes(time_str: str) -> int | None:
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

class FeedMonitor:
    """
    说说监控任务，根据配置：阅读空间好友最新说说、阅读优先列表说说、回复自己说说下的评论
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.config = plugin.config
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
        # 获取配置
        interval = self.config.auto_read.interval
        silent_duration = self.config.auto_read.silent_duration
        authority_type = self.config.authority.auto_read_authority_type
        white_list = self.config.authority.auto_read_whitelist
        while self.is_running:
            try:
                # 等待指定时间
                await asyncio.sleep(interval * 60)
                # 检查是否在静默时间段内
                if _is_in_silent_period(silent_duration):
                    continue
                # 对白名单中的QQ单独优先处理
                for qq in white_list:
                    logger.info(f"开始处理白名单中的QQ: {qq}")
                    success, message = await read_feed(qq)
                    if not success:
                        logger.warning(f"未进行任何处理: {message}")
                    else:
                        logger.info(f"已阅读{qq}的说说，数量：{len(message)}条")
                # 若是白名单模式，则跳过其他QQ
                if authority_type == "whitelist":
                    continue
                # 否则继续处理QQ
                else:
                    await monitor_read_feed()
                # 自动回复
                if self.config.auto_reply.enable_auto_reply:
                    await reply_feed()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"监控任务出错: {str(e)}")
                # 出错后等待一段时间再重试
                await asyncio.sleep(300)


class ScheduleSender:
    """
    定时发送任务，根据配置：定时发送说说
    """
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

    def _check_today_send_decision(self):
        """每天0点决定今天是否发送说说"""
        p = self.plugin.config.auto_send.daily_probability

        # 生成0-1之间的随机数，如果小于p则发送，否则不发送
        if random.random() < p:
            self.today_send_enabled = True
            logger.info(f"今天允许发送说说 (概率: {p:.2f})")
        else:
            self.today_send_enabled = False
            logger.info(f"今天不发送说说 (概率: {p:.2f})")

    def _generate_fluctuate_table(self):
        """生成随机波动时间表"""
        schedule_times = self.plugin.config.auto_send.schedule
        fluctuate_minutes = self.plugin.config.auto_send.fluctuation

        # 清空当前波动表
        self.fluctuate_table = []

        # 如果波动分钟数为0，直接使用计划时间
        if fluctuate_minutes == 0:
            self.fluctuate_table = schedule_times.copy()
            self.fluctuate_table.sort()
            logger.info(f"无波动，使用计划时间: {self.fluctuate_table}")
            return

        # 为每个计划时间生成一个随机波动时间
        for base_time in schedule_times:
            # 解析基础时间
            base_hour, base_minute = map(int, base_time.split(":"))
            base_total_minutes = base_hour * 60 + base_minute
            # 生成随机偏移量
            offset = random.randint(-fluctuate_minutes, fluctuate_minutes)
            # 处理溢出
            total_minutes = base_total_minutes + offset
            if total_minutes < 0:
                total_minutes += 24 * 60
            elif total_minutes >= 24 * 60:
                total_minutes -= 24 * 60
            # 转换回时间格式
            h = total_minutes // 60
            m = total_minutes % 60
            fluctuate_time = f"{h:02d}:{m:02d}"
            # 添加到波动表
            if fluctuate_time not in self.fluctuate_table:
                self.fluctuate_table.append(fluctuate_time)

        # 按时间排序波动表
        self.fluctuate_table.sort()
        logger.info(f"波动后的发送时间表: {self.fluctuate_table}")

    def _should_reset_schedule(self):
        """检查是否需要重置时间表（每天0点）"""
        current_date = datetime.datetime.now().date()
        return current_date != self.last_reset_date

    async def _schedule_loop(self):
        """定时发送循环"""
        while self.is_running:
            try:
                # 检查是否需要重置时间表（每天0点）
                if self._should_reset_schedule():
                    logger.info("检测到日期变化，重置发送时间表")
                    self.last_reset_date = datetime.datetime.now().date()
                    self._generate_fluctuate_table()
                    self._check_today_send_decision()  # 重新决定今天是否发送

                # 获取当前时间
                current_time = datetime.datetime.now().strftime("%H:%M")
                if current_time in self.fluctuate_table and self.today_send_enabled:
                    if time.time() - self.last_send_time >= 60:  # 避免一分钟内重复发送
                        logger.info(f"当前时间 {current_time} ，准备发送说说")
                        self.last_send_time = time.time()
                        await self.send_scheduled_feed()
                        self.fluctuate_table.remove(current_time)
                        logger.info(f"剩余发送时间点: {self.fluctuate_table}")                        
                elif current_time in self.fluctuate_table and not self.today_send_enabled:
                    # 如果到达发送时间但今天不允许发送，也移除该时间点
                    logger.info(f"到达发送时间 {current_time}，但今天不发送说说")
                    self.fluctuate_table.remove(current_time)
                    logger.info(f"剩余发送时间点: {self.fluctuate_table}")
                
                await asyncio.sleep(60)  # 每分钟检查一次
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时发送任务出错: {e}")
                await asyncio.sleep(60)
    async def send_scheduled_feed(self):
        """发送计划发送的说说"""
        # 获取配置
        enable_random_topic = self.plugin.config.auto_send.random_topic
        fixed_topic = self.plugin.config.auto_send.fixed_topic
        if enable_random_topic:
            topic = "随机主题"
        else:
            topic = random.choice(fixed_topic)
        # 发送说说
        success, message = await send_feed(topic)
        if success:
            logger.info(f"已成功发送定时说说: {topic}")
        else:
            logger.error(f"发送定时说说失败: {message}")
