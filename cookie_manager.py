import os
import json
import asyncio
from pathlib import Path

import httpx

from src.common.logger import get_logger
from src.plugin_system.apis import config_api

logger = get_logger('Maizone.cookie')


def get_cookie_file_path(uin: str) -> str:
    """构建cookie的保存路径"""
    uin = uin.lstrip("0")
    base_dir = Path(__file__).parent.resolve()
    return str(base_dir / f"cookies-{uin}.json")


def parse_cookie_string(cookie_str: str) -> dict:
    """将cookie字符串解析为字典"""
    return {pair.split("=", 1)[0]: pair.split("=", 1)[1] for pair in cookie_str.split("; ")}


async def fetch_cookies_by_napcat(host: str, domain: str, port: str, napcat_token: str = "") -> dict:
    """通过Napcat http服务器获取cookie字典"""
    url = f"http://{host}:{port}/get_cookies"
    max_retries = 2
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            headers = {"Content-Type": "application/json"}
            if napcat_token:
                headers["Authorization"] = f"Bearer {napcat_token}"

            payload = {"domain": domain}

            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()

                if resp.status_code != 200:
                    error_msg = f"Napcat服务返回错误状态码: {resp.status_code}"
                    if resp.status_code == 403:
                        error_msg += " (Token验证失败)"
                    raise RuntimeError(error_msg)

                data = resp.json()
                if data.get("status") != "ok" or "cookies" not in data.get("data", {}):
                    raise RuntimeError(f"获取 cookie 失败: {data}")
                cookie_data = data["data"]
                cookie_str = cookie_data["cookies"]
                parsed_cookies = parse_cookie_string(cookie_str)
                return parsed_cookies

        except httpx.RequestError as e:
            if attempt < max_retries - 1:
                logger.warning(f"无法连接到Napcat服务(尝试 {attempt + 1}/{max_retries}): {url}，错误: {str(e)}")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            logger.error(f"无法连接到Napcat服务(最终尝试): {url}，错误: {str(e)}")
            raise RuntimeError(f"无法连接到Napcat服务: {url}")
        except Exception as e:
            logger.error(f"获取cookie异常: {str(e)}")
            raise

    raise RuntimeError(f"无法连接到Napcat服务: 超过最大重试次数({max_retries})")

async def fetch_cookies_by_clientkey() -> dict:
    """通过令牌获取cookie字典"""
    uin = config_api.get_global_config('bot.qq_account', "")
    local_key_url = "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?s_url=https%3A%2F%2Fhuifu.qq.com%2Findex.html&style=20&appid=715021417" \
                    "&proxy_url=https%3A%2F%2Fhuifu.qq.com%2Fproxy.html"
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(local_key_url, headers={"User-Agent": UA})
        pt_local_token = resp.cookies["pt_local_token"]
        client_key_url = f"https://localhost.ptlogin2.qq.com:4301/pt_get_st?clientuin={uin}&callback=ptui_getst_CB&r=0.7284667321181328&pt_local_tk={pt_local_token}"
        resp = await client.get(client_key_url,
                                headers={"User-Agent": UA, "Referer": "https://ssl.xui.ptlogin2.qq.com/"},
                                cookies=resp.cookies)
        if resp.status_code == 400:
            raise Exception(f"获取clientkey失败: {resp.text}")
        clientkey = resp.cookies["clientkey"]
        login_url = f"https://ssl.ptlogin2.qq.com/jump?ptlang=1033&clientuin={uin}&clientkey={clientkey}" \
                    f"&u1=https%3A%2F%2Fuser.qzone.qq.com%2F{uin}%2Finfocenter&keyindex=19"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(login_url, headers={"User-Agent": UA}, follow_redirects=False)
            resp = await client.get(resp.headers["Location"],
                                    headers={"User-Agent": UA, "Referer": "https://ssl.ptlogin2.qq.com/"},
                                    cookies=resp.cookies, follow_redirects=False)
            cookies = {cookie.name: cookie.value for cookie in resp.cookies.jar}
            return cookies
async def renew_cookies(host: str = "127.0.0.1", port: str = "9999", napcat_token: str = ""):
    """
    尝试更新cookie并保存到本地文件
    1. 通过napcat获取cookie
    2. 如果napcat获取失败，尝试通过clientkey获取cookie
    3. 如果clientkey获取失败，尝试不更新读取本地cookie文件
    """
    # 尝试通过napcat获取cookie
    uin = config_api.get_global_config('bot.qq_account', "")
    file_path = get_cookie_file_path(uin)
    directory = os.path.dirname(file_path)
    try:
        domain = "user.qzone.qq.com"
        cookie_dict = await fetch_cookies_by_napcat(host, domain, port, napcat_token)
    # 尝试通过clientkey获取cookie
    except Exception as e:
        logger.error(f"Napcat获取cookie异常: {str(e)}。尝试通过ClientKey获取cookie")
        try:
            cookie_dict = await fetch_cookies_by_clientkey()
        # 尝试寻找本地cookie文件
        except Exception as e:
            logger.error(f"ClientKey获取cookie异常: {str(e)}，尝试读取本地cookie文件")
            try:
                if not os.path.exists(file_path):
                    raise FileNotFoundError(f"未找到本地cookie文件: {file_path}")
                with open(file_path, "r", encoding="utf-8") as f:
                    cookie_dict = json.load(f)
            except FileNotFoundError as e:
                logger.error(f"本地cookie文件不存在: {str(e)}")
                raise RuntimeError("获取cookie失败")

    # 将cookie字典保存到路径
    try:

        if not os.path.exists(directory):
            os.makedirs(directory)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(cookie_dict, f, indent=4, ensure_ascii=False)
        logger.info(f"[OK] cookies 已保存至: {file_path}")
    # 异常处理
    except PermissionError as e:
        logger.error(f"文件写入权限不足: {str(e)}")
        raise
    except FileNotFoundError as e:
        logger.error(f"文件路径不存在: {str(e)}")
        raise
    except OSError as e:
        logger.error(f"文件写入失败: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"处理cookie时发生异常: {str(e)}")
        raise RuntimeError(f"处理cookie时发生异常: {str(e)}")