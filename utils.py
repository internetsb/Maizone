import os
import random
import datetime
import traceback
import base64
from pathlib import Path

import httpx

from .qzone_api import create_qzone_api
from src.common.logger import get_logger
from src.plugin_system.apis import llm_api, config_api, emoji_api
from src.plugin_system.core import component_registry

logger = get_logger('Maizone.硅基生图')

plugin_config = component_registry.get_plugin_config('MaizonePlugin')
models = llm_api.get_available_models()
prompt_model = config_api.get_plugin_config(plugin_config, "models.text_model", "replyer")  # 获取模型配置
model_config = models[prompt_model]
personality = config_api.get_global_config("personality.personality", "一只猫娘") # 人格
async def generate_image_by_sf(api_key: str, story: str, image_dir: str, batch_size: int = 1) -> bool:
    """
    用siliconflow API生成说说配图保存至对应路径

    Args:
        api_key (str): SiliconFlow API的密钥。
        story (str): 说说内容，用于生成配图的描述。
        image_dir (str): 图片保存的目录路径。
        batch_size (int): 每次生成的图片数量，默认为1。

    Returns:
        bool: 如果生成成功返回True，否则返回False。

    """
    logger.info(f"正在生成图片提示词...")
    # 生成图片提示词
    global personality
    prompt = f"""
        请根据以下QQ空间说说内容配图，并构建生成配图的风格和prompt。
        说说主人信息：'{personality}'。
        说说内容:'{story}'。 
        请注意：仅回复用于生成图片的prompt，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
        """

    success, image_prompt, reasoning, model_name = await llm_api.generate_with_model(
        prompt=prompt,
        model_config=model_config,
        request_type="story.generate",
        temperature=0.3,
        max_tokens=1000
    )
    if success:
        logger.info(f'即将生成说说配图：{image_prompt}')
    else:
        logger.error('生成说说配图prompt失败')
    # 生成图片
    try:
        # SiliconFlow API
        sf_url = "https://api.siliconflow.cn/v1/images/generations"
        sf_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        sf_data = {
            "model": "Kwai-Kolors/Kolors",
            "prompt": image_prompt,
            "negative_prompt": "lowres, bad anatomy, bad hands, text, error, cropped, worst quality, low quality, "
                               "normal quality, jpeg artifacts, signature, watermark, username, blurry",
            "image_size": "1024x1024",
            "batch_size": batch_size,
            "seed": random.randint(1, 9999999999),
            "num_inference_steps": 20,
            "guidance_scale": 7.5,
        }

        async with httpx.AsyncClient() as client:
            # 发送请求
            res = await client.post(sf_url, headers=sf_headers, json=sf_data, timeout=30.0)
            if res.status_code != 200:
                logger.error(f'生成图片出错，错误码[{res.status_code}]')
                logger.error(f'错误响应: {res.text}')
                return False

            json_data = res.json()
            image_urls = [img["url"] for img in json_data["images"]]

            # 确保目录存在
            Path(image_dir).mkdir(parents=True, exist_ok=True)

            # 下载并保存图片
            for i, img_url in enumerate(image_urls):
                try:
                    # 下载图片
                    img_response = await client.get(img_url, timeout=60.0)
                    img_response.raise_for_status()

                    filename = f"sf_{i}.png"
                    save_path = Path(image_dir) / filename
                    with open(save_path, "wb") as f:
                        f.write(img_response.content)
                    logger.info(f"图片已保存至: {save_path}")

                except Exception as e:
                    logger.error(f"下载图片失败: {str(e)}")
                    return False

        return True

    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        logger.error(traceback.format_exc())
        return False

async def send_feed(message: str, image_directory: str, enable_image: bool, image_mode: str,
                    ai_probability: float, image_number: int, apikey: str) -> bool:
    """
    发送说说及图片。

    Args:
        message (str): 要发送的说说内容。
        image_directory (str): 图片存储的目录路径。
        enable_image (bool): 是否启用图片功能。
        image_mode (str): 图片模式，可选值为 "only_ai", "only_emoji", "random"。
        ai_probability (float): 在随机模式下使用AI生成图片的概率，范围为0到1。
        image_number (int): 要生成的图片数量，范围为1到4。
        apikey (str): SiliconFlow API的密钥，用于AI图片生成。

    Returns:
        bool: 如果发送成功返回True，否则返回False。

    Raises:
        Exception: 如果在发送过程中发生错误，将记录日志并返回False。
    """
    qzone = create_qzone_api()

    images = []
    if not enable_image:
        # 如果未启用图片功能，直接发送纯文本
        try:
            tid = await qzone.publish_emotion(message, images)
            logger.info(f"成功发送说说，tid: {tid}")
            return True
        except Exception as e:
            logger.error("发送说说失败")
            logger.error(traceback.format_exc())
            return False

    # 验证配置有效性
    if image_mode not in ["only_ai", "only_emoji", "random"]:
        logger.error(f"无效的图片模式: {image_mode}，已默认更改为 random")
        image_mode = "random"
    ai_probability = max(0.0, min(1.0, ai_probability))  # 限制在0-1之间
    image_number = max(1, min(4, image_number))  # 限制在1-4之间

    # 决定图片来源
    use_ai = False
    if image_mode == "only_ai":
        use_ai = True
    elif image_mode == "only_emoji":
        use_ai = False
    else:  # random模式
        use_ai = random.random() < ai_probability

    # 获取图片
    if use_ai:
        # 使用AI生成图片
        if apikey:
            ai_success = await generate_image_by_sf(
                api_key=apikey,
                story=message,
                image_dir=image_directory,
                batch_size=image_number
            )
            if ai_success:
                # 获取目录下所有文件
                all_files = [f for f in os.listdir(image_directory)
                             if os.path.isfile(os.path.join(image_directory, f))]

                # 筛选未处理的图片（不以"done_"开头的文件）
                unprocessed_files = [f for f in all_files if not f.startswith("done_")]
                unprocessed_files_sorted = sorted(unprocessed_files)

                for image_file in unprocessed_files_sorted:
                    full_path = os.path.join(image_directory, image_file)
                    with open(full_path, "rb") as img:
                        images.append(img.read())

                    # 生成带时间戳的前缀 (格式: done_YYYYMMDD_HHMMSS_)
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    new_filename = f"done_{timestamp}_{image_file}"
                    new_path = os.path.join(image_directory, new_filename)
                    os.rename(full_path, new_path)
            else:
                logger.error("AI图片生成失败")
                return False
        else:
            logger.error("未配置SiliconFlow API Key，无法生成AI图片")
            return False
    else:
        # 使用表情包
        image = await emoji_api.get_by_description(message)
        if image:
            image_base64, description, scene = image
            image_data = base64.b64decode(image_base64)
            images.append(image_data)

    try:
        tid = await qzone.publish_emotion(message, images)
        logger.info(f"成功发送说说，tid: {tid}")
        return True
    except Exception as e:
        logger.error("发送说说失败")
        logger.error(traceback.format_exc())
        return False

async def read_feed(target_qq: str, num: int) -> list[dict]:
    """
    通过调用QZone API的`get_list`方法阅读指定QQ号的说说，返回说说列表。

    Args:
        target_qq (str): 目标QQ号，表示需要读取其说说的用户。
        num (int): 要获取的说说数量。

    Returns:
        list: 包含说说信息的列表。若发生错误，则返回{'error': '错误原因'}

    """
    qzone = create_qzone_api()

    try:
        feeds_list = await qzone.get_list(target_qq, num)
        logger.debug(f"获取到的说说列表: {feeds_list}")
        return feeds_list
    except Exception as e:
        logger.error("获取list失败")
        logger.error(traceback.format_exc())
        return []


async def monitor_read_feed(num: int) -> list[dict]:
    """
    通过调用QZone API的`monitor_get_list`方法定时阅读说说，返回说说列表。

    Args:
        num (int): 要获取的说说数量。

    Returns:
        list: 包含说说信息的列表。

    Raises:
        Exception: 如果在获取说说列表时发生错误，将记录错误日志并返回空列表。
    """
    qzone = create_qzone_api()

    try:
        feeds_list = await qzone.monitor_get_list(num)
        logger.debug(f"获取到的说说列表: {feeds_list}")
        return feeds_list
    except Exception as e:
        logger.error("获取list失败")
        logger.error(traceback.format_exc())
        return []


async def like_feed(target_qq: str, fid: str) -> bool:
    """
    调用QZone API的`like`方法点赞指定说说。

    Args:
        target_qq (str): 目标QQ号，表示需要点赞其说说的用户。
        fid (str): 说说的动态ID。

    Returns:
        bool: 如果点赞成功返回True，否则返回False。

    Raises:
        Exception: 如果在点赞过程中发生错误，将记录错误日志并返回False。
    """
    qzone = create_qzone_api()

    success = await qzone.like(fid, target_qq)
    if not success:
        logger.error("点赞失败")
        logger.error(traceback.format_exc())
        return success
    return True


async def comment_feed(target_qq: str, fid: str, content: str) -> bool:
    """
    通过调用QZone API的`comment`方法评论指定说说。

    Args:
        target_qq (str): 目标QQ号，表示需要评论其说说的用户。
        fid (str): 说说的动态ID。
        content (str): 评论的文本内容。

    Returns:
        bool: 如果评论成功返回True，否则返回False。

    Raises:
        Exception: 如果在评论过程中发生错误，将记录错误日志并返回False。
    """
    qzone = create_qzone_api()

    success = await qzone.comment(fid, target_qq, content)
    if not success:
        logger.error("评论失败")
        logger.error(traceback.format_exc())
        return False
    return True


async def reply_feed(fid: str,target_qq: str, target_nickname: str, content: str, comment_tid: str) -> bool:
    """
    通过调用QZone API的`reply`方法回复指定评论。

    Args:
        fid (str): 说说的动态ID。
        target_qq (str): 目标QQ号。
        target_nickname (str): 目标QQ昵称。
        content (str): 回复的文本内容。
        comment_tid (str): 评论的唯一标识ID。

    Returns:
        bool: 如果回复成功返回True，否则返回False。

    Raises:
        Exception: 如果在回复过程中发生错误，将记录错误日志并返回False。
    """
    qzone = create_qzone_api()

    success = await qzone.reply(fid, target_qq, target_nickname, content, comment_tid)
    if not success:
        logger.error("评论失败")
        logger.error(traceback.format_exc())
        return False
    return True