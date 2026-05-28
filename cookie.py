import asyncio
import json
import re
import time
import os
from pathlib import Path
import httpx

from maibot_sdk import API
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
def set_cookie_logger(custom_logger):
    global logger
    logger = custom_logger
# ===== API =====
api = None
def set_api_capability(api_capability):
    """设置插件API上下文，用于调用get_cookies API"""
    global api
    api = api_capability
    
# QQ空间二维码登录相关URL
_qrcode_url = "https://ssl.ptlogin2.qq.com/ptqrshow?appid=549000912&e=2&l=M&s=3&d=72&v=4&t=0.31232733520361844&daid=5&pt_3rd_aid=0"
_login_check_url = "https://xui.ptlogin2.qq.com/ssl/ptqrlogin?u1=https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone&ptqrtoken={}&ptredirect=0&h=1&t=1&g=1&from_ui=1&ptlang=2052&action=0-0-1656992258324&js_ver=22070111&js_type=1&login_sig=&pt_uistyle=40&aid=549000912&daid=5&has_onekey=1&&o1vId=1e61428d61cb5015701ad73d5fb59f73"
_check_sig_url = "https://ptlogin2.qzone.qq.com/check_sig?pttype=1&uin={}&service=ptqrlogin&nodirect=1&ptsigx={}&s_url=https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone&f_url=&ptlang=2052&ptredirect=100&aid=549000912&daid=5&j_later=0&low_login_hour=0&regmaster=0&pt_login_type=3&pt_aid=0&pt_aaid=16&pt_light=0&pt_3rd_aid=0"

# 内存中的上次cookie更新时间
_last_cookie_update_time = 0
qrcode_path = str(Path(__file__).parent.resolve() / "qrcode.png")
cookie_path = str(Path(__file__).parent.resolve() / "cookies.json")

# 支持的cookie更新方法
COOKIE_METHODS = ["adapter", "napcat", "qrcode", "local"]  # 可仅保留adapter

# ---------- 工具函数 ----------
def read_local_cookies() -> dict | None:
    """读取本地cookie文件"""
    if not os.path.exists(cookie_path):
        logger.error(f"未找到本地cookie文件: {cookie_path}")
        return None
    try:
        with open(cookie_path, "r", encoding="utf-8") as f:
            cookie_dict = json.load(f)
        logger.info("读取本地cookie文件成功")
        return cookie_dict
    except Exception as e:
        logger.error(f"读取本地cookie文件失败: {str(e)}")
        return None

def should_skip_qr_login(qrcode: bool = False) -> bool:
    """检查是否应该跳过二维码登录（一小时内跳过，若是扫码登录则放宽至20小时内）"""
    # 爬取的cookie有效期约24小时，可能需要修改
    global _last_cookie_update_time
    if _last_cookie_update_time == 0:
        return False

    current_time = time.time()
    # 检查是否在20小时内
    if qrcode:
        return (current_time - _last_cookie_update_time) < 20 * 3600
    # 检查是否在1小时内
    return (current_time - _last_cookie_update_time) < 3600

def update_last_cookie_update_time():
    """更新上次cookie更新时间"""
    global _last_cookie_update_time
    _last_cookie_update_time = time.time()

def parse_cookie_string(cookie_str: str) -> dict:
    """将cookie字符串解析为字典"""
    return {pair.split("=", 1)[0]: pair.split("=", 1)[1] for pair in cookie_str.split("; ")}

def getptqrtoken(qrsig):
    """协议特定计算算法"""
    e = 0
    for i in range(1, len(qrsig) + 1):
        e += (e << 5) + ord(qrsig[i - 1])
    return str(2147483647 & e)

# ---------- 获取cookie函数 ----------
async def fetch_cookies_by_napcat(host: str, domain: str, port: str, napcat_token: str = "", max_retries: int = 1, retry_delay: int = 10) -> dict | None:
    """通过Napcat http服务器获取cookie字典"""
    url = f"http://{host}:{port}/get_cookies"

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
                    logger.error(error_msg)
                    continue

                data = resp.json()
                if data.get("status") != "ok" or "cookies" not in data.get("data", {}):
                    logger.error(f"获取 cookie 失败: {data}")
                    continue
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
        except Exception as e:
            logger.error(f"获取cookie异常: {str(e)}")

    logger.error(f"无法连接到Napcat服务: 超过最大重试次数({max_retries})")
    return None


async def fetch_cookies_by_qrcode(max_timeout_times: int = 3) -> dict | None:
    """在插件目录下生成qrcode.png,通过二维码登录获取cookie字典，成功登录后删除二维码图片"""
    for i in range(max_timeout_times):
        # 获取二维码
        async with httpx.AsyncClient() as client:
            req = await client.get(_qrcode_url)
            qrsig = ''

            set_cookies_set = req.headers.get('Set-Cookie', '').split(";")
            for set_cookies in set_cookies_set:
                if set_cookies.startswith("qrsig"):
                    qrsig = set_cookies.split("=")[1]
                    break
            if qrsig == '':
                logger.error("qrsig is empty")
                continue

            # 获取ptqrtoken
            ptqrtoken = getptqrtoken(qrsig)

            # 保存二维码图片
            with open(qrcode_path, "wb") as f:
                f.write(req.content)
            logger.info(f"二维码已保存于{qrcode_path}，请两分钟内使用手机QQ扫描登录")

            # 检查是否登录成功
            for _ in range(60):  # 最多等待60次，约2分钟
                await asyncio.sleep(2)
                req = await client.get(_login_check_url.format(ptqrtoken), cookies={"qrsig": qrsig})
                if req.text.find("二维码已失效") != -1:
                    logger.info("二维码已失效，重新获取...")
                    break
                if req.text.find("登录成功") != -1:
                    # 检出检查登录的响应头
                    response_header_dict = req.headers

                    # 检出url
                    url = eval(req.text.replace("ptuiCB", ""))[2]

                    # 获取ptsigx
                    m = re.findall(r"ptsigx=[A-z \d]*&", url)
                    ptsigx = m[0].replace("ptsigx=", "").replace("&", "")

                    # 获取uin
                    m = re.findall(r"uin=[\d]*&", url)
                    uin = m[0].replace("uin=", "").replace("&", "")

                    # 获取skey和p_skey
                    res = await client.get(_check_sig_url.format(uin, ptsigx), cookies={"qrsig": qrsig},
                                            headers={'Cookie': response_header_dict.get('Set-Cookie', '')})

                    final_cookie = res.headers.get('Set-Cookie', '')

                    final_cookie_dict = {}
                    for set_cookie in final_cookie.split(";, "):
                        for cookie in set_cookie.split(";"):
                            spt = cookie.split("=")
                            if len(spt) == 2 and final_cookie_dict.get(spt[0]) is None:
                                final_cookie_dict[spt[0]] = spt[1]

                    # 删除二维码图片
                    if os.path.exists(qrcode_path):
                        os.remove(qrcode_path)

                    # 更新上次扫码登录时间
                    update_last_cookie_update_time()

                    return final_cookie_dict
                logger.debug("等待扫码登录...")
    logger.error("{}次尝试失败".format(max_timeout_times))
    return None

async def fetch_cookies_by_adapter() -> dict | None:
    """通过MaiBot Napcat Adapter API获取cookie字典"""
    domain = "user.qzone.qq.com"
    if api is None:
        logger.error("API上下文未设置，无法调用Napcat Adapter API")
        return None
    else:
        # 与Napcat处理方法一致
        result = await api.call("maibot-team.napcat-adapter.adapter.napcat.account.get_cookies", params={"domain": domain})
        if result.get("status") != "ok" or "cookies" not in result.get("data", {}):
            logger.error(f"获取 cookie 失败: {result}")
            return None
        cookie_data = result["data"]
        cookie_str = cookie_data["cookies"]
        parsed_cookies = parse_cookie_string(cookie_str)
        return parsed_cookies
            

# async def fetch_cookies_by_clientkey() -> dict:
#     """通过令牌获取cookie字典"""
#     uin = config_api.get_global_config('bot.qq_account', "")
#     local_key_url = "https://xui.ptlogin2.qq.com/cgi-bin-xlogin?appid=715021417&s_url=https%3A%2F%2Fhuifu.qq.com%2Findex.html"
#     UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"

#     async with httpx.AsyncClient(timeout=15.0) as client:
#         resp = await client.get(local_key_url, headers={"User-Agent": UA})
#         pt_local_token = resp.cookies.get("pt_local_token", "")
#         if not pt_local_token:
#             raise Exception("无法获取pt_local_token")

#         client_key_url = f"https://localhost.ptlogin2.qq.com:4301/pt_get_st?clientuin={uin}&callback=ptui_getst_CB&r=0.7284667321181328&pt_local_tk={pt_local_token}"
#         resp = await client.get(client_key_url,
#                                 headers={"User-Agent": UA, "Referer": "https://xui.ptlogin2.qq.com/"},
#                                 cookies=resp.cookies)
#         if resp.status_code == 400:
#             raise Exception(f"获取clientkey失败: {resp.text}")

#         clientkey = resp.cookies.get("clientkey", "")
#         if not clientkey:
#             raise Exception("无法获取clientkey")

#         login_url = f"https://ssl.ptlogin2.qq.com/jump?ptlang=1033&clientuin={uin}&clientkey={clientkey}" \
#                     f"&u1=https%3A%2F%2Fuser.qzone.qq.com%2F{uin}%2Finfocenter&keyindex=19"

#         resp = await client.get(login_url, headers={"User-Agent": UA}, follow_redirects=False)
#         resp = await client.get(resp.headers["Location"],
#                                 headers={"User-Agent": UA, "Referer": "https://ssl.ptlogin2.qq.com/"},
#                                 cookies=resp.cookies, follow_redirects=False)
#         cookies = {cookie.name: cookie.value for cookie in resp.cookies.jar}
#         return cookies

async def renew_cookies(
        host: str = "127.0.0.1",
        port: str = "9999",
        napcat_token: str = "",
        methods: list[str] = ["adapter","napcat", "qrcode", "local"],
        fallback_to_local: bool = True
) -> bool:
    """
    尝试更新cookie并保存到本地文件

    参数:
        host: Napcat服务主机地址
        port: Napcat服务端口
        napcat_token: Napcat认证令牌
        methods: 更新方法列表，按顺序尝试，支持: "napcat", "qrcode", "local"
        fallback_to_local: 当所有方法都失败时是否回退到本地cookie文件
    返回:
        bool: 是否成功更新cookie
    """
    # 1小时内跳过更新cookie
    if should_skip_qr_login():
        logger.info("上次cookie更新时间在1小时内，跳过更新")
        return False

    # 获取配置的更新方法
    if methods is None:
        methods = ["napcat", "qrcode", "local"]

    # 验证方法列表
    valid_methods = [method for method in methods if method in COOKIE_METHODS]
    if not valid_methods:
        logger.warning("没有有效的cookie更新方法，使用默认方法")
        valid_methods = ["napcat", "qrcode", "local"]

    logger.info(f"使用cookie更新方法: {valid_methods}")

    file_path = cookie_path
    directory = os.path.dirname(file_path)

    cookie_dict = None
    last_error = None

    # 按配置的方法顺序尝试获取cookie
    for method in valid_methods:
        try:
            if method == "adapter":
                logger.info("尝试通过Napcat Adapter获取cookie...")
                cookie_dict = await fetch_cookies_by_adapter()
                if cookie_dict:
                    logger.info("Napcat Adapter获取cookie成功")
                    break
                else:
                    logger.info("Napcat Adapter获取cookie失败，尝试下一个方法")
                    continue

            if method == "napcat":
                logger.info("尝试通过Napcat获取cookie...")
                domain = "user.qzone.qq.com"
                cookie_dict = await fetch_cookies_by_napcat(host, domain, port, napcat_token)
                if cookie_dict:
                    logger.info("Napcat获取cookie成功")
                    break
                else:
                    logger.info("Napcat获取cookie失败，尝试下一个方法")
                    continue

            # elif method == "clientkey":
            #     logger.info("尝试通过ClientKey获取cookie...")
            #     cookie_dict = await fetch_cookies_by_clientkey()
            #     logger.info("ClientKey获取cookie成功")
            #     break

            elif method == "qrcode":
                # 检查是否应该跳过二维码登录
                if should_skip_qr_login(qrcode=True):
                    logger.info("上次扫码登录在20小时内，跳过二维码登录")
                    continue

                logger.info("尝试通过二维码登录获取cookie...")
                cookie_dict = await fetch_cookies_by_qrcode()
                if cookie_dict:
                    logger.info("二维码登录成功")
                    break
                else:
                    logger.info("二维码登录失败，尝试下一个方法")
                    continue

            elif method == "local":
                logger.info("尝试读取本地cookie文件...")
                cookie_dict = read_local_cookies()
                if cookie_dict:
                    logger.info("读取本地cookie文件成功")
                    break
                else:
                    logger.info("读取本地cookie文件失败，尝试下一个方法")
                    continue

        except Exception as e:
            logger.error(f"{method}方法获取cookie失败: {str(e)}")
            last_error = e
            continue

    # 如果所有方法都失败，尝试回退到本地文件（如果local不在methods中）
    if cookie_dict is None and fallback_to_local and "local" not in valid_methods:
        try:
            logger.info("所有配置方法都失败，尝试读取本地cookie文件作为回退")
            cookie_dict = read_local_cookies()
            if cookie_dict:
                logger.info("本地文件回退成功")
        except Exception as e:
            logger.error(f"回退到本地cookie文件失败: {str(e)}")

    # 如果仍然没有获取到cookie，记录错误
    if cookie_dict is None or not cookie_dict:
        if last_error:
            logger.error(f"所有cookie获取方法都失败，最后错误: {str(last_error)}")
        else:
            logger.error("所有cookie获取方法都失败")
        return False

    # 将cookie字典保存到文件
    try:
        if not os.path.exists(directory):
            os.makedirs(directory)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(cookie_dict, f, indent=4, ensure_ascii=False)
        logger.info(f"[OK] cookies 已保存至: {file_path}")
        update_last_cookie_update_time()

    except PermissionError as e:
        logger.error(f"文件写入权限不足: {str(e)}")
        return False
    except FileNotFoundError as e:
        logger.error(f"文件路径不存在: {str(e)}")
        return False
    except OSError as e:
        logger.error(f"文件写入失败: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"处理cookie时发生异常: {str(e)}")
        return False
    
    return True
    

async def main():
    """测试函数，可测试除adapter以外的其他cookie获取方法"""

    napcat_host = "127.0.0.1"
    napcat_port = "9999"
    napcat_token = ""
    domain = "user.qzone.qq.com"

    # cookies = await fetch_cookies_by_qrcode()
    cookies = await fetch_cookies_by_napcat(napcat_host, domain, napcat_port, napcat_token)
    # cookies = read_local_cookies()
    if cookies:
        print(cookies.get("uin", "").lstrip("o0"))
        print(json.dumps(cookies, indent=4))
    else:
        print("获取cookies失败")

if __name__ == "__main__":
    asyncio.run(main())
