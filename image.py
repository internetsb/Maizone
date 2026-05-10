import random
import base64
import asyncio
from pathlib import Path
from openai import OpenAI
import requests

plugin_context = None
def set_images_plugin_context(ctx):
    global plugin_context
    plugin_context = ctx

async def generate_image(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    reference: str | None = None,
) -> bytes:
    """
    Openai格式生成一张图片并返回其 bytes。
    参数:
        - base_url: AI生图服务地址
        - api_key: AI生图服务API Key
        - model: AI生图模型
        - prompt: 图片生成提示
        - reference: 参考图URL或本地路径
    返回:
        - 图片的二进制数据
    """
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,  # 强制只生成一张
    }
    if reference is not None:
        # 判断是URL还是本地路径
        if reference.startswith("http://") or reference.startswith("https://"):
            # 直接使用URL作为参考图
            body["extra_body"] = {
                "image": reference
            }
        else:
            # 读取本地文件并转换为base64
            path = Path(reference)
            with open(str(path.absolute()), "rb") as f:
                img_data = f.read()
            format = path.suffix[1:].lower() if path.suffix else "png"
            encoded_string = base64.b64encode(img_data).decode('utf-8')
            body["extra_body"] = {
                "image": f"data:image/{format};base64,{encoded_string}"
            }

    client = OpenAI(base_url=base_url, api_key=api_key)
    plugin_context.ctx.logger.info(f"正在使用模型 {model} 生成图片: {prompt}") # type: ignore
    response = client.images.generate(**body)
    if response is None or not response.data:
        plugin_context.ctx.logger.error("图片生成失败，未收到有效响应") # type: ignore
        return b''
    
    img = response.data[0]
    if img.url:
        plugin_context.ctx.logger.info("下载图片中...") # type: ignore
        r = requests.get(img.url, timeout=30)
        r.raise_for_status()
        return r.content
    elif img.b64_json:
        plugin_context.ctx.logger.info("解码 base64 图片...") # type: ignore
        return base64.b64decode(img.b64_json)
    else:
        raise ValueError("图片数据为空")

async def generate_ai_images(message: str, image_number: int) -> list[bytes]:
    """根据message生成指定数量的AI图片，返回图片的二进制数据列表"""
    images = []
    images_prompt = []  # 已使用过的配图
    for _ in range(image_number):
        image_bytes = await generate_ai_image(message, images_prompt)
        images.append(image_bytes)
    return images


async def generate_ai_image(message: str, images_prompt: list[str] | None = None) -> bytes:
    """生成单张 AI 图片并返回二进制数据。

    Args:
        message: 用于生成图片的上下文文本
        images_prompt: 可选的已使用提示词列表（会在生成后追加新的提示词）

    Returns:
        bytes: 图片二进制数据
    """
    # 获取配置
    personality = plugin_context.personality  # type: ignore
    base_url = plugin_context.config.image.base_url  # type: ignore
    api_key = plugin_context.config.image.api_key  # type: ignore
    model = plugin_context.config.image.model  # type: ignore
    text_model = plugin_context.config.plugin.text_model  # type: ignore
    enable_reference = plugin_context.config.image.enable_reference  # type: ignore
    reference = plugin_context.config.image.reference if enable_reference else None  # type: ignore
    prompt_template = plugin_context.config.image.prompt  # type: ignore
    ref_prompt = plugin_context.config.image.ref_prompt if enable_reference else ""  # type: ignore

    used = images_prompt if images_prompt is not None else []
    # 构建提示词
    prompt = prompt_template.format(personality=personality, message=message) + ref_prompt
    prompt += f"已使用过的配图提示词：{used if used else '无'}。"
    response = await plugin_context.ctx.llm.generate(prompt, model=text_model)  # type: ignore
    image_prompt = response.get("response", "")
    plugin_context.ctx.logger.info(f"生成的图片提示词：{image_prompt}")  # type: ignore
    if images_prompt is not None:
        images_prompt.append(image_prompt)

    # 生成图片
    image_bytes = await generate_image(base_url, api_key, model, image_prompt, reference)
    return image_bytes

async def generate_emoji_image(message: str) -> bytes: 
    """根据message生成表情包图片，返回图片的二进制数据"""
    emoji = await plugin_context.ctx.emoji.get_by_description(description=message) # type: ignore
    base64_data = emoji.get("base64", "")
    if base64_data:
        return base64.b64decode(base64_data)
    plugin_context.ctx.logger.error(f"未找到对应的表情包：{message}") # type: ignore
    return b''

async def generate_emoji_images(message: str, number: int) -> list[bytes]:
    """根据message生成指定数量的表情包图片，返回图片的二进制数据列表"""
    images = []
    for _ in range(number):
        image = await generate_emoji_image(message)
        images.append(image)
    return images

async def generate_images(message: str, image_mode: str = "only_emoji", image_number: int = 1, ai_probability: float = 0.5) -> list[bytes]:
    """根据message生成图片
    image_mode: only_emoji（仅表情包）、only_ai（仅AI生成）、random（随机）
    ai_probability: 当image_mode为random时，生成AI图片的概率，取值范围0-1
    """
    images_list: list[bytes] = []
    if image_mode == "only_emoji":
        # 仅表情包
        images_list = await generate_emoji_images(message, image_number)
    elif image_mode == "only_ai":
        # 仅AI生成
        images_list = await generate_ai_images(message, image_number)
    elif image_mode == "random":
        # 随机
        for _ in range(image_number):
            if random.random() < ai_probability:
                images_list.append(await generate_ai_image(message))
            else:
                images_list.append(await generate_emoji_image(message))
    else:
        plugin_context.ctx.logger.error(f"未知的image_mode：{image_mode}") # type: ignore
        return []
    return images_list

def test_generate_images():
    # 测试AI生图功能
    baseurl = "https://ark.cn-beijing.volces.com/api/v3"
    apikey = "your_api_key"
    model = "doubao-seedream-5-0-260128"
    prompt = "生成人物全身照"
    reference = r"D:\IE\QQbot\MaiBot-1.0.0-pre.15\plugins\maizone\reference.jpg"
    image_bytes = asyncio.run(generate_image(baseurl, apikey, model, prompt, reference))
    with open("test_ai_image.png", "wb") as f:
        f.write(image_bytes)

if __name__ == "__main__":
    test_generate_images() 