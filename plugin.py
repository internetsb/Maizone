from typing import List, Tuple, Type, Any
import httpx
import json
import os
import time
import random
import base64
import requests
import asyncio
import re
import traceback
import typing
import subprocess
import sys

from src.plugin_system import (
    BasePlugin, register_plugin, BaseAction,
    ComponentInfo, ActionActivationType,
    BaseCommand
)
from src.common.logger import get_logger
from src.plugin_system.apis import llm_api, config_api, emoji_api, person_api, generator_api
from src.plugin_system.base.config_types import ConfigField

logger = get_logger('Maizone')


# ===== QZone API 功能 =====
def get_cookie_file_path(uin: str) -> str:
    """构建cookie路径"""
    return os.path.join(os.getcwd(),'plugins/Maizone/',f"cookies-{uin}.json")


def parse_cookie_string(cookie_str: str) -> dict:
    """解析cookie字符串为字典"""
    return {pair.split("=", 1)[0]: pair.split("=", 1)[1] for pair in cookie_str.split("; ")}


def extract_uin_from_cookie(cookie_str: str) -> str:
    """从cookie中获得uin"""
    for item in cookie_str.split("; "):
        if item.startswith("uin=") or item.startswith("o_uin="):
            return item.split("=")[1].lstrip("o")
    raise ValueError("无法从 Cookie 字符串中提取 uin")


async def fetch_cookies(domain: str, port: str) -> dict:
    """获取cookie"""
    url = f"http://127.0.0.1:{port}/get_cookies?domain={domain}"
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok" or "cookies" not in data.get("data", {}):
            raise RuntimeError(f"获取 cookie 失败: {data}")
        return data["data"]


async def renew_cookies(port: str):
    """更新cookie"""
    domain = "user.qzone.qq.com"
    cookie_data = await fetch_cookies(domain, port)
    cookie_str = cookie_data["cookies"]
    parsed_cookies = parse_cookie_string(cookie_str)
    uin = extract_uin_from_cookie(cookie_str)
    file_path = get_cookie_file_path(uin)
    with open(file_path, "w") as f:
        json.dump(parsed_cookies, f, indent=4, ensure_ascii=False)
    logger.info(f"[OK] cookies 已保存至: {file_path}")


def generate_gtk(skey: str) -> str:
    """生成gtk"""
    hash_val = 5381
    for i in range(len(skey)):
        hash_val += (hash_val << 5) + ord(skey[i])
    return str(hash_val & 2147483647)


def get_picbo_and_richval(upload_result):
    """获取picbo和richval"""
    json_data = upload_result

    if 'ret' not in json_data:
        raise Exception("获取图片picbo和richval失败")

    if json_data['ret'] != 0:
        raise Exception("上传图片失败")
    picbo_spt = json_data['data']['url'].split('&bo=')
    if len(picbo_spt) < 2:
        raise Exception("上传图片失败")
    picbo = picbo_spt[1]

    richval = ",{},{},{},{},{},{},,{},{}".format(json_data['data']['albumid'], json_data['data']['lloc'],
                                                 json_data['data']['sloc'], json_data['data']['type'],
                                                 json_data['data']['height'], json_data['data']['width'],
                                                 json_data['data']['height'], json_data['data']['width'])

    return picbo, richval


class QzoneLogin:
    """qq空间二维码登录"""
    def __init__(self):
        pass

    def getptqrtoken(self, qrsig):
        e = 0
        for i in range(1, len(qrsig) + 1):
            e += (e << 5) + ord(qrsig[i - 1])
        return str(2147483647 & e)

    async def check_cookies(self, cookies: dict) -> bool:
        # 占位符：验证cookies
        return True

    async def login_via_qrcode(
            self,
            qrcode_callback: typing.Callable[[bytes], typing.Awaitable[None]],
            max_timeout_times: int = 3,
    ) -> dict:
        qrcode_url = "https://ssl.ptlogin2.qq.com/ptqrshow?appid=549000912&e=2&l=M&s=3&d=72&v=4&t=0.31232733520361844&daid=5&pt_3rd_aid=0"
        login_check_url = "https://xui.ptlogin2.qq.com/ssl/ptqrlogin?u1=https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone&ptqrtoken={}&ptredirect=0&h=1&t=1&g=1&from_ui=1&ptlang=2052&action=0-0-1656992258324&js_ver=22070111&js_type=1&login_sig=&pt_uistyle=40&aid=549000912&daid=5&has_onekey=1&&o1vId=1e61428d61cb5015701ad73d5fb59f73"
        check_sig_url = "https://ptlogin2.qzone.qq.com/check_sig?pttype=1&uin={}&service=ptqrlogin&nodirect=1&ptsigx={}&s_url=https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone&f_url=&ptlang=2052&ptredirect=100&aid=549000912&daid=5&j_later=0&low_login_hour=0&regmaster=0&pt_login_type=3&pt_aid=0&pt_aaid=16&pt_light=0&pt_3rd_aid=0"

        for i in range(max_timeout_times):
            req = requests.get(qrcode_url)
            qrsig = ''
            set_cookie = req.headers['Set-Cookie']
            set_cookies_set = req.headers['Set-Cookie'].split(";")
            for set_cookies in set_cookies_set:
                if set_cookies.startswith("qrsig"):
                    qrsig = set_cookies.split("=")[1]
                    break
            if qrsig == '':
                raise Exception("qrsig is empty")

            ptqrtoken = self.getptqrtoken(qrsig)
            await qrcode_callback(req.content)

            while True:
                await asyncio.sleep(2)
                req = requests.get(login_check_url.format(ptqrtoken), cookies={"qrsig": qrsig})
                if req.text.find("二维码已失效") != -1:
                    break
                if req.text.find("登录成功") != -1:
                    response_header_dict = req.headers
                    url = eval(req.text.replace("ptuiCB", ""))[2]
                    m = re.findall(r"ptsigx=[A-z \d]*&", url)
                    ptsigx = m[0].replace("ptsigx=", "").replace("&", "")
                    m = re.findall(r"uin=[\d]*&", url)
                    uin = m[0].replace("uin=", "").replace("&", "")
                    res = requests.get(check_sig_url.format(uin, ptsigx), cookies={"qrsig": qrsig},
                                       headers={'Cookie': response_header_dict['Set-Cookie']})
                    final_cookie = res.headers['Set-Cookie']
                    final_cookie_dict = {}
                    for set_cookie in final_cookie.split(";, "):
                        for cookie in set_cookie.split(";"):
                            spt = cookie.split("=")
                            if len(spt) == 2 and final_cookie_dict.get(spt[0]) is None:
                                final_cookie_dict[spt[0]] = spt[1]
                    return final_cookie_dict
        raise Exception("{}次尝试失败".format(max_timeout_times))


class QzoneAPI:
    #QQ空间cgi常量
    UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION_PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    DOLIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"

    def __init__(self, cookies_dict: dict = {}):
        self.cookies = cookies_dict
        self.gtk2 = ''
        self.uin = 0
        self.qzonetoken = ''

        if 'p_skey' in self.cookies:
            self.gtk2 = generate_gtk(self.cookies['p_skey'])

        if 'uin' in self.cookies:
            self.uin = int(self.cookies['uin'][1:])

    async def do(
            self,
            method: str,
            url: str,
            params: dict = {},
            data: dict = {},
            headers: dict = {},
            cookies: dict = None,
            timeout: int = 10
    ) -> requests.Response:
        """发送requests请求，返回response"""
        if cookies is None:
            cookies = self.cookies

        return requests.request(
            method=method,
            url=url,
            params=params,
            data=data,
            headers=headers,
            cookies=cookies,
            timeout=timeout
        )

    async def token_valid(self, retry=3) -> bool:
        for i in range(retry):
            try:
                return True
            except Exception as e:
                traceback.print_exc()
                if i == retry - 1:
                    return False

    def image_to_base64(self, image: bytes) -> str:
        pic_base64 = base64.b64encode(image)
        return str(pic_base64)[2:-1]

    async def upload_image(self, image: bytes) -> str:
        res = await self.do(
            method="POST",
            url=self.UPLOAD_IMAGE_URL,
            data={
                "filename": "filename",
                "zzpanelkey": "",
                "uploadtype": "1",
                "albumtype": "7",
                "exttype": "0",
                "skey": self.cookies["skey"],
                "zzpaneluin": self.uin,
                "p_uin": self.uin,
                "uin": self.uin,
                "p_skey": self.cookies['p_skey'],
                "output_type": "json",
                "qzonetoken": "",
                "refer": "shuoshuo",
                "charset": "utf-8",
                "output_charset": "utf-8",
                "upload_hd": "1",
                "hd_width": "2048",
                "hd_height": "10000",
                "hd_quality": "96",
                "backUrls": "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,http://119.147.64.75/cgi-bin/upload/cgi_upload_image",
                "url": "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk=" + self.gtk2,
                "base64": "1",
                "picfile": self.image_to_base64(image),
            },
            headers={
                'referer': 'https://user.qzone.qq.com/' + str(self.uin),
                'origin': 'https://user.qzone.qq.com'
            },
            timeout=60
        )
        if res.status_code == 200:
            return eval(res.text[res.text.find('{'):res.text.rfind('}') + 1])
        else:
            raise Exception("上传图片失败")

    async def publish_emotion(self, content: str, images: list[bytes] = []) -> str:
        """发送说说"""
        if images is None:
            images = []

        post_data = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": "https://user.qzone.qq.com/" + str(self.uin)
        }

        if len(images) > 0:
            pic_bos = []
            richvals = []
            for img in images:
                uploadresult = await self.upload_image(img)
                picbo, richval = get_picbo_and_richval(uploadresult)
                pic_bos.append(picbo)
                richvals.append(richval)

            post_data['pic_bo'] = ','.join(pic_bos)
            post_data['richtype'] = '1'
            post_data['richval'] = '\t'.join(richvals)

        res = await self.do(
            method="POST",
            url=self.EMOTION_PUBLISH_URL,
            params={
                'g_tk': self.gtk2,
                'uin': self.uin,
            },
            data=post_data,
            headers={
                'referer': 'https://user.qzone.qq.com/' + str(self.uin),
                'origin': 'https://user.qzone.qq.com'
            }
        )
        if res.status_code == 200:
            return res.json()['tid']
        else:
            raise Exception("发表说说失败: " + res.text)

    async def like(self, fid: str, target_qq: str) -> bool:
        """点赞指定说说"""
        uin = self.uin
        post_data ={
            'qzreferrer': f'https://user.qzone.qq.com/{uin}',  # 来源
            'opuin': uin,  # 操作者QQ
            'unikey': f'http://user.qzone.qq.com/{target_qq}/mood/{fid}',  # 动态唯一标识
            'curkey': f'http://user.qzone.qq.com/{target_qq}/mood/{fid}',  # 要操作的动态对象
            'appid': 311,  # 应用ID(说说:311)
            'from': 1,  # 来源
            'typeid': 0,  # 类型ID
            'abstime': int(time.time()),  # 当前时间戳
            'fid': fid,  # 动态ID
            'active': 0,  # 活动ID
            'format': 'json',  # 返回格式
            'fupdate': 1,  # 更新标记
        }
        res = await self.do(
            method="POST",
            url=self.DOLIKE_URL,
            params={
                'g_tk': self.gtk2,
            } ,
            data=post_data,
            headers={
                'referer': 'https://user.qzone.qq.com/' + str(self.uin),
                'origin': 'https://user.qzone.qq.com'
            },
        )
        if res.status_code == 200:
            return True
        else:
            raise Exception("点赞失败: " + res.text)

    async def comment(self, fid: str, target_qq: str, content: str) -> bool:
        """评论指定说说"""
        uin = self.uin
        post_data ={
            "topicId" : f'{target_qq}_{fid}__1', #说说ID
            "uin": uin,  # botQQ
            "hostUin": target_qq,  # 目标QQ
            "feedsType": 100,  # 说说类型
            "inCharset": "utf-8",  # 字符集
            "outCharset": "utf-8",  # 字符集
            "plat": "qzone",  # 平台
            "source": "ic",  # 来源
            "platformid": 52,  # 平台id
            "format": "fs",  # 返回格式
            "ref": "feeds",  # 引用
            "content": content,  # 评论内容
        }
        res = await self.do(
            method="POST",
            url=self.COMMENT_URL,
            params={
                "g_tk": self.gtk2,
            } ,
            data=post_data,
            headers={
                'referer': 'https://user.qzone.qq.com/' + str(self.uin),
                'origin': 'https://user.qzone.qq.com'
            },
        )
        if res.status_code == 200:
            return True
        else:
            raise Exception("评论失败: " + res.text)

    async def get_list(self,target_qq : str,num : int) -> list[dict[str, Any]]:
        """获得qq号为target_qq的好友说说列表"""
        logger.info(f'target_qq: {target_qq}')
        res = await self.do(
            method="GET",
            url=self.LIST_URL,
            params={
                'g_tk': self.gtk2,
                "uin": target_qq,  # 目标QQ
                "ftype": 0,  # 全部说说
                "sort": 0,  # 最新在前
                "pos": 0,  # 起始位置
                "num": num,  # 获取条数
                "replynum": 100,  # 评论数
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "jsonp",
                "need_comment" : 1,
                "need_private_comment": 1
            } ,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Referer": f"https://user.qzone.qq.com/{target_qq}",
                "Host": "user.qzone.qq.com",
                "Connection": "keep-alive"
            },
        )

        if res.status_code != 200:
            raise Exception("访问失败: " + str(res.status_code))

        data = res.text
        if data.startswith('_preloadCallback(') and data.endswith(');'):
            # 去掉首尾的 _preloadCallback( 和 );
            json_str = data[len('_preloadCallback('):-2]
        else:
            json_str = data

        try:
            # 2. 解析JSON数据
            json_data = json.loads(json_str)

            # print(json_data)
            # 3. 提取说说内容
            feeds_list = []
            for msg in json_data.get("msglist", []):
                tid = msg.get("tid", "")
                content = msg.get("content", "")
                logger.info(f"正在阅读说说内容: {content}")

                is_comment = False
                person_id = person_api.get_person_id("qq", self.uin)
                uin_nickname = await person_api.get_person_value(person_id, "nickname", "未知用户")
                if 'conmentlist' in msg:
                    for comment in msg.get("commentlist", []):
                        qq_nickname = comment.get("name")
                        if uin_nickname in qq_nickname:
                            logger.info('已评论过此说说，即将跳过')
                            is_comment = True
                            break

                if tid and content and not is_comment:
                    # 存储结果
                    feeds_list.append({"tid": tid, "content": content})

            return feeds_list

        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}")
            return []
        except Exception as e:
            print(f"处理出错: {e}")
            return []




async def send_feed(message: str, image_directory: str, qq_account: str, enable_image : bool):
    cookie_file = get_cookie_file_path(qq_account)
    qrcode_file = os.path.join(os.getcwd(),'plugins/Maizone/qrcode.png')

    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
        except:
            cookies = None
    else:
        cookies = None

    if not cookies:
        login = QzoneLogin()

        async def qrcode_callback(qrcode: bytes):
            with open(qrcode_file, "wb") as f:
                f.write(qrcode)
            if sys.platform == "win32":
                os.startfile(qrcode_file)
            elif sys.platform == "darwin":
                subprocess.run(['open', qrcode_file])
            else:
                subprocess.run(['xdg-open', qrcode_file])

        try:
            cookies = await login.login_via_qrcode(qrcode_callback)
            logger.info(f"Cookies after login: {cookies}")
            with open(cookie_file, 'w') as f:
                json.dump(cookies, f)
            if os.path.exists(qrcode_file):
                os.remove(qrcode_file)
        except Exception as e:
            logger.error("生成二维码失败")
            logger.error(traceback.format_exc())
            return False

    qzone = QzoneAPI(cookies)
    if not await qzone.token_valid():
        logger.error("Cookies 过期或无效")
        return False

    images = []
    if os.path.exists(image_directory) and enable_image:
        image_files = sorted(
            [os.path.join(image_directory, f) for f in os.listdir(image_directory)
             if os.path.isfile(os.path.join(image_directory, f))]
        )
        for image_file in image_files:
            with open(image_file, "rb") as img:
                images.append(img.read())
            os.remove(image_file)
    if not images and enable_image:
        image = await emoji_api.get_by_description(message)
        if image:
            image_base64, description, scene = image
        image_data = base64.b64decode(image_base64)
        images.append(image_data)

    try:
        tid = await qzone.publish_emotion(message, images)
        logger.info(f"成功发送， tid: {tid}")
        return True
    except Exception as e:
        logger.error("发送失败")
        logger.error(traceback.format_exc())
        return False

async def read_feed(qq_account: str,target_qq: str, num : int):
    cookie_file = get_cookie_file_path(qq_account)
    qrcode_file = os.path.join(os.getcwd(),'plugins/Maizone/qrcode.png')

    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
        except:
            cookies = None
    else:
        cookies = None

    if not cookies:
        login = QzoneLogin()

        async def qrcode_callback(qrcode: bytes):
            with open(qrcode_file, "wb") as f:
                f.write(qrcode)
            if sys.platform == "win32":
                os.startfile(qrcode_file)
            elif sys.platform == "darwin":
                subprocess.run(['open', qrcode_file])
            else:
                subprocess.run(['xdg-open', qrcode_file])

        try:
            cookies = await login.login_via_qrcode(qrcode_callback)
            logger.info(f"Cookies after login: {cookies}")
            with open(cookie_file, 'w') as f:
                json.dump(cookies, f)
            if os.path.exists(qrcode_file):
                os.remove(qrcode_file)
        except Exception as e:
            logger.error("生成二维码失败")
            logger.error(traceback.format_exc())
            return False

    qzone = QzoneAPI(cookies)
    if not await qzone.token_valid():
        logger.error("Cookies 过期或无效")
        return False

    try:
        feeds_list = await qzone.get_list(target_qq,num)
        return feeds_list
    except Exception as e:
        logger.error("获取list失败")
        logger.error(traceback.format_exc())
        return []

async def like_feed(qq_account: str, target_qq: str,fid: str):
    cookie_file = get_cookie_file_path(qq_account)
    qrcode_file = os.path.join(os.getcwd(),'plugins/Maizone/qrcode.png')

    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
        except:
            cookies = None
    else:
        cookies = None

    if not cookies:
        login = QzoneLogin()

        async def qrcode_callback(qrcode: bytes):
            with open(qrcode_file, "wb") as f:
                f.write(qrcode)
            if sys.platform == "win32":
                os.startfile(qrcode_file)
            elif sys.platform == "darwin":
                subprocess.run(['open', qrcode_file])
            else:
                subprocess.run(['xdg-open', qrcode_file])

        try:
            cookies = await login.login_via_qrcode(qrcode_callback)
            logger.info(f"Cookies after login: {cookies}")
            with open(cookie_file, 'w') as f:
                json.dump(cookies, f)
            if os.path.exists(qrcode_file):
                os.remove(qrcode_file)
        except Exception as e:
            logger.error("生成二维码失败")
            logger.error(traceback.format_exc())
            return False

    qzone = QzoneAPI(cookies)
    if not await qzone.token_valid():
        logger.error("Cookies 过期或无效")
        return False

    success = await qzone.like(fid, target_qq)
    if not success:
        logger.error("点赞失败")
        logger.error(traceback.format_exc())
        return success
    return True

async def comment_feed(qq_account: str, target_qq: str,fid: str,content: str):
    cookie_file = get_cookie_file_path(qq_account)
    qrcode_file = os.path.join(os.getcwd(),'plugins/Maizone/qrcode.png')

    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
        except:
            cookies = None
    else:
        cookies = None

    if not cookies:
        login = QzoneLogin()

        async def qrcode_callback(qrcode: bytes):
            with open(qrcode_file, "wb") as f:
                f.write(qrcode)
            if sys.platform == "win32":
                os.startfile(qrcode_file)
            elif sys.platform == "darwin":
                subprocess.run(['open', qrcode_file])
            else:
                subprocess.run(['xdg-open', qrcode_file])

        try:
            cookies = await login.login_via_qrcode(qrcode_callback)
            logger.info(f"Cookies after login: {cookies}")
            with open(cookie_file, 'w') as f:
                json.dump(cookies, f)
            if os.path.exists(qrcode_file):
                os.remove(qrcode_file)
        except Exception as e:
            logger.error("生成二维码失败")
            logger.error(traceback.format_exc())
            return False

    qzone = QzoneAPI(cookies)
    if not await qzone.token_valid():
        logger.error("Cookies 过期或无效")
        return False

    success = await qzone.comment(fid, target_qq,content)
    if not success:
        logger.error("评论失败")
        logger.error(traceback.format_exc())
        return False
    return True
# ===== 插件Command组件 =====
class SendFeedCommand(BaseCommand):
    """发说说Command - 响应/send_feed命令"""

    command_name = "send_feed"
    command_description = "发一条说说"

    command_pattern = r"^/send_feed(?:\s+(?P<topic>\w+))?$"
    command_help = "发一条主题为<topic>或随机的说说"
    command_examples = ["/send_feed", "/send_feed topic"]
    intercept_message = True

    async def execute(self) -> Tuple[bool, str]:
        topic = self.matched_groups.get("topic")
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer_1")
        model_config = getattr(models, text_model, None)
        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")

        if topic:
            prompt = f"你是{bot_personality}，你的表达风格是{bot_expression}，请写一条主题为{topic}的说说发表在qq空间上，确保符合人设，口语化，不要将理由写在括号中，不违反法律法规"
        else:
            prompt = f"你是{bot_personality}，你的表达风格是{bot_expression}，请写一条任意主题的说说发表在qq空间上，确保符合人设，口语化，不要将理由写在括号中，不违反法律法规"

        success, story, reasoning, model_name = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="story.generate",
            temperature=0.3,
            max_tokens=1000
        )

        if not success:
            return False, "生成说说内容失败"

        logger.info(f"成功生成说说内容：'{story}'")


        port = self.get_config("plugin.http_port", "9999")
        qq_account = config_api.get_global_config("bot.qq_account", "")
        image_dir = self.get_config("send.image_directory", "./images")

        # 更新cookies
        try:
            await renew_cookies(port)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败"

        # 发送说说
        enable_image = self.get_config("send.enable_image", "true")
        success = await send_feed(story, image_dir, qq_account, enable_image)
        if not success:
            return False, "发送说说失败"
        await self.send_text(f"已发送说说{story}")
        return True, 'success'


# ===== 插件Action组件 =====
class SendFeedAction(BaseAction):
    """发说说Action - 只在用户要求发说说时激活"""

    action_name = "send_feed"
    action_description = "发一条相应主题的说说"

    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD

    activation_keywords = ["说说", "QQ空间", "动态"]
    keyword_case_sensitive = False

    action_parameters = {"topic": "要发送的说说主题"}
    action_require = [
        "用户要求发说说时使用",
        "当有人希望你更新qq空间时使用",
        "当你认为适合发说说时使用",
    ]
    associated_types = ["text","emoji"]

    async def execute(self) -> Tuple[bool, str]:
        topic = self.action_data.get("topic", "")
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer_1")
        model_config = getattr(models, text_model, None)
        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")
        prompt = f"你是{bot_personality}，你的表达风格是{bot_expression}，请写一条主题为{topic}的说说发表在qq空间上，确保符合人设，不要将理由写在括号中，不违反法律法规"
        success, story, reasoning, model_name = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="story.generate",
            temperature=0.3,
            max_tokens=1000
        )

        if not success:
            return False, "生成说说内容失败"

        logger.info(f"生成说说内容：'{story}'，即将发送")
        port = self.get_config("plugin.http_port", "9999")
        qq_account = config_api.get_global_config("bot.qq_account", "")
        image_dir = self.get_config("send.image_directory", "./plugins/Maizone/images")

        # 更新cookies
        try:
            await renew_cookies(port)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败"


        # 发送说说
        enable_image = self.get_config("send.enable_image", "true")
        success = await send_feed(story, image_dir, qq_account, enable_image)
        if not success:
            return False, "发送说说失败"
        logger.info(f"成功发送说说: {story}")
        # 生成回复
        success, reply_set = await generator_api.generate_reply(
            chat_stream=self.chat_stream,
        )

        if success and reply_set:
            reply_type, reply_content = reply_set[0]
            await self.send_text(reply_content)

            return True, 'success'
        return False, '生成回复失败'

class ReadFeedAction(BaseAction):
    """读说说Action - 只在用户要求读说说时激活"""

    action_name = "read_feed"
    action_description = "读取好友最近的动态/说说/qq空间并评论点赞"

    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD

    activation_keywords = ["说说", "QQ空间", "动态"]
    keyword_case_sensitive = False

    action_parameters = {
        "target_name": "需要阅读动态的好友的qq名称",
    }

    action_require = [
        "需要阅读某人动态、说说、QQ空间时使用",
        "当有人希望你评价某人的动态、说说、QQ空间",
        "当你认为适合阅读说说、动态、QQ空间时使用",
    ]
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        target_name = self.action_data.get("target_name", "")

        port = self.get_config("plugin.http_port", "9999")
        qq_account = config_api.get_global_config("bot.qq_account", "")

        # 更新cookies
        try:
            await renew_cookies(port)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败"
        #根据昵称获取qq号
        person_id = person_api.get_person_id_by_name(target_name)
        logger.info(f'获取到person_id={person_id}')
        target_qq = await person_api.get_person_value(person_id, "user_id")
        logger.info(f'获取到user_id={target_qq}')
        #获取指定好友最近的说说
        num = self.get_config("read.read_number", 5)
        like_possibility = self.get_config("read.like_possibility", 1.0)
        comment_possibility = self.get_config("read.comment_possibility", 1.0)
        feeds_list = await read_feed(qq_account,target_qq,num)
        logger.info(f"成功读取到{len(feeds_list)}条说说")
        #生成评论
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer_1")
        model_config = getattr(models, text_model, None)
        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")
        for feed in feeds_list:
            time.sleep(3)
            content = feed["content"]
            fid = feed["tid"]
            if random.random() <= comment_possibility:
                #评论说说
                prompt = f"你是{bot_personality}，你的表达风格是{bot_expression}，请对你的好友{target_name}qq空间上内容为'{content}'的说说发表你的评论，确保符合人设，口语化，不要将理由写在括号中，不违反法律法规"
                logger.info(f'正在评论{target_name}的说说：{content[:20]}...')
                success, comment, reasoning, model_name = await llm_api.generate_with_model(
                    prompt=prompt,
                    model_config=model_config,
                    request_type="story.generate",
                    temperature=0.3,
                    max_tokens=1000
                )

                if not success:
                    return False, "生成评论内容失败"

                logger.info(f"成功生成评论内容：'{comment}'，即将发送")

                success = await comment_feed(qq_account,target_qq,fid,comment)
                if not success:
                    logger.error(f"评论说说{content}失败")
                    return False, "评论说说失败"
                logger.info(f"发送评论'{comment}'成功")
            # 点赞说说
            if random.random() <= like_possibility:
                success = await like_feed(qq_account,target_qq,fid)
                if not success:
                    logger.error(f"点赞说说{content}失败")
                    return False, "点赞说说失败"
                logger.info(f'点赞说说{content[:10]}..成功')

        # 生成回复
        success, reply_set = await generator_api.generate_reply(
            chat_stream=self.chat_stream,
        )

        if success and reply_set:
            reply_type, reply_content = reply_set[0]
            await self.send_text(reply_content)
            return True, 'success'

        return False, '生成回复失败'

# ===== 插件注册 =====
@register_plugin
class MaizonePlugin(BasePlugin):
    """Maizone插件 - 让麦麦发QQ空间"""

    plugin_name = "Maizone"
    plugin_description = "让麦麦实现QQ空间点赞、评论、发说说"
    plugin_version = "0.4.0"
    plugin_author = "internetsb"
    enable_plugin = True
    config_file_name = "config.toml"

    config_section_descriptions = {
        "plugin": "插件启用配置",
        "models": "插件模型配置",
        "send": "发送说说配置",
        "read": "阅读说说配置",
    }

    config_schema = {
        "plugin": {
            "http_port": ConfigField(type=str, default='9999', description="Napcat设定http服务器端口号"),
            "cookie_directory": ConfigField(type=str, default='./plugins/Maizone', description="生成cookie的目录"),
        },
        "models": {
            "text_model": ConfigField(type=str, default="replyer_1", description="生成文本的模型（从全局变量读取）"),
            "ai_image_model": {
                "url": ConfigField(type=str, default="", description="AI图片生成URL"),
                "api_key": ConfigField(type=str, default="", description="AI图片生成API密钥"),
            }
        },
        "send": {
            "enable_image": ConfigField(type=bool, default=False, description="是否启用带图片的说说"),
            "enable_ai_image": ConfigField(type=bool, default=False, description="是否启用Ai生成带图片的说说（暂时没用）"),
            "image_directory": ConfigField(type=str, default="./plugins/Maizone/images", description="图片存储目录")
        },
        "read": {
            "read_number" : ConfigField(type=int,default=5,description="一次读取最新的几条说说"),
            "like_possibility" : ConfigField(type=float,default=1.0,description="麦麦读说说后点赞的概率（0到1）"),
            "comment_possibility" : ConfigField(type=float,default=1.0,description="麦麦读说说后评论的概率（0到1）"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (SendFeedCommand.get_command_info(), SendFeedCommand),
            (SendFeedAction.get_action_info(), SendFeedAction),
            (ReadFeedAction.get_action_info(), ReadFeedAction),
        ]
