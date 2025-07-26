import base64
import json
import os
import random
import time
import datetime
import traceback
from typing import List, Tuple, Type, Any, Optional
from pathlib import Path

import httpx
import requests
import asyncio
import bs4
import json5

from src.chat.utils.utils_image import get_image_manager
from src.common.logger import get_logger
from src.plugin_system import (
    BasePlugin, register_plugin, BaseAction,
    ComponentInfo, ActionActivationType,
    BaseCommand
)
from src.plugin_system.apis import llm_api, config_api, emoji_api, person_api, generator_api
from src.plugin_system.base.config_types import ConfigField

logger = get_logger('Maizone')


# ===== QZone API 功能 =====

def get_cookie_file_path(uin: str) -> str:
    """构建cookie路径"""
    uin = uin.lstrip("0")   # 去除可能的前缀0
    return os.path.join(os.getcwd(), 'plugins/Maizone/', f"cookies-{uin}.json")


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
    url = f"ws://127.0.0.1:{port}/get_cookies?domain={domain}"
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "ok" or "cookies" not in data.get("data", {}):
                raise RuntimeError(f"获取 cookie 失败: {data}")
            return data["data"]
    except httpx.RequestError as e:
        logger.error(f"无法连接到Napcat服务: {url}，错误: {str(e)}")
        raise RuntimeError(f"无法连接到Napcat服务: {url}")
    except Exception as e:
        logger.error(f"获取cookie异常: {str(e)}")
        raise


async def renew_cookies(port: str):
    """更新cookie"""
    domain = "user.qzone.qq.com"
    cookie_data = await fetch_cookies(domain, port)
    cookie_str = cookie_data["cookies"]
    parsed_cookies = parse_cookie_string(cookie_str)
    uin = extract_uin_from_cookie(cookie_str)
    file_path = get_cookie_file_path(uin)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(parsed_cookies, f, indent=4, ensure_ascii=False)
        logger.info(f"[OK] cookies 已保存至: {file_path}")
    except OSError as e:
        logger.error(f"文件写入失败: {str(e)}")


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


def extract_code(html_content: str) -> Any | None:
    """从QQ空间响应HTML中提取code值"""
    try:
        # 创建BeautifulSoup对象
        soup = bs4.BeautifulSoup(html_content, 'html.parser')
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'frameElement.callback' in script.string:
                script_content = script.string
                start_index = script_content.find('frameElement.callback(') + len('frameElement.callback(')
                end_index = script_content.rfind(');')  # 找到最后一个分号作为结束
                if 0 < start_index < end_index:
                    json_str = script_content[start_index:end_index].strip()
                    if json_str.endswith(';'):
                        json_str = json_str[:-1]
                    data = json5.loads(json_str)
                    return data.get("code")
        # 如果没有找到匹配的内容
        return None
    except:
        return None


def image_to_base64(image: bytes) -> str:
    pic_base64 = base64.b64encode(image)
    return str(pic_base64)[2:-1]


class QzoneAPI:
    #QQ空间cgi常量
    UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION_PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    DOLIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    REPLY_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    ZONE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"

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

    async def get_image_base64_by_url(self, url: str) -> str:
        res = await self.do(
            method="GET",
            url=url,
            timeout=60
        )
        # 获取图片二进制数据
        image_data = res.content

        # 转换为Base64
        base64_str = base64.b64encode(image_data).decode('utf-8')
        return base64_str

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
                "backUrls": "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,"
                            "http://119.147.64.75/cgi-bin/upload/cgi_upload_image",
                "url": "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk=" + self.gtk2,
                "base64": "1",
                "picfile": image_to_base64(image),
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
        post_data = {
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
            },
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
        post_data = {
            "topicId": f'{target_qq}_{fid}__1',  #说说ID
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
            },
            data=post_data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                'referer': 'https://user.qzone.qq.com/' + str(self.uin),
                'origin': 'https://user.qzone.qq.com'
            },
        )
        if res.status_code == 200:
            return True
        else:
            raise Exception("评论失败: " + res.text)

    async def reply(self, fid: str, target_qq: str, target_nickname: str, content: str, comment_tid: str) -> bool:
        """回复指定评论"""
        uin = self.uin
        post_data = {
            "topicId": f'{uin}_{fid}__1',
            "uin": uin,
            "hostUin": uin,
            "feedsType": 100,
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "plat": "qzone",
            "source": "ic",
            "platformid": 52,
            "format": "fs",
            "ref": "feeds",
            "content": f"@{{uin:{target_qq},nick:{target_nickname},auto:1}} {content}",
            "commentId": comment_tid,
            "commentUin": target_qq,
            "richval": "",  # 富文本内容
            "richtype": "",  # 富文本类型
            "private": "0",  # 是否私密评论
            "paramstr": "2",
            "qzreferrer": f"https://user.qzone.qq.com/{self.uin}/main"  # 来源页
        }
        res = await self.do(
            method="POST",
            url=self.REPLY_URL,
            params={
                "g_tk": self.gtk2,
            },
            data=post_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "TE": "trailers",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "Referer": f"https://user.qzone.qq.com/",
                "Origin": "https://user.qzone.qq.com",
            },
        )
        if res.status_code == 200:
            if extract_code(res.text) != 0:
                logger.error("回复失败" + res.text)
                return False
            return True
        else:
            raise Exception(f"回复失败，错误码: {res.status_code}")

    async def get_list(self, target_qq: str, num: int) -> list[dict[str, Any]]:
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
                "need_comment": 1,
                "need_private_comment": 1
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/91.0.4472.124 Safari/537.36",
                "Referer": f"https://user.qzone.qq.com/{target_qq}",
                "Host": "user.qzone.qq.com",
                "Connection": "keep-alive"
            },
        )

        if res.status_code != 200:
            raise Exception("访问失败: " + str(res.status_code))

        data = res.text
        if data.startswith('_preloadCallback(') and data.endswith(');'):
            # 1. 去掉res首尾的 _preloadCallback( 和 );
            json_str = data[len('_preloadCallback('):-2]
        else:
            json_str = data

        try:
            # 2. 解析JSON数据
            json_data = json.loads(json_str)
            uin_nickname = json_data.get('logininfo').get('name')

            #print(json_data)

            if json_data.get('code') != 0:
                return [{"error": json_data.get('message')}]
            # 3. 提取说说内容
            feeds_list = []
            for msg in json_data.get("msglist", []):
                #已评论过的说说不再阅读
                is_comment = False
                if 'commentlist' in msg:
                    commentlist = msg.get("commentlist")
                    if isinstance(commentlist, list):  # 确保一定是可迭代的列表
                        for comment in commentlist:
                            qq_nickname = comment.get("name")
                            if uin_nickname == qq_nickname:
                                logger.info('已评论过此说说，即将跳过')
                                is_comment = True
                                break

                if not is_comment:
                    # 存储结果
                    timestamp = msg.get("created_time", "")
                    created_time = "unknown"
                    if timestamp:
                        time_tuple = time.localtime(timestamp)
                        # 格式化为字符串（年-月-日 时:分:秒）
                        created_time = time.strftime('%Y-%m-%d %H:%M:%S', time_tuple)
                    tid = msg.get("tid", "")
                    content = msg.get("content", "")
                    logger.info(f"正在阅读说说内容: {content[:20]}")
                    # 提取图片信息
                    images = []
                    if 'pic' in msg:
                        for pic in msg['pic']:
                            # 按优先级获取图片URL
                            url = pic.get('url1') or pic.get('pic_id') or pic.get('smallurl')
                            if url:
                                image_base64 = await self.get_image_base64_by_url(url)
                                image_manager = get_image_manager()
                                image_description = await image_manager.get_image_description(image_base64)
                                images.append(image_description)
                    # 读取视频临时手段
                    if 'video' in msg:
                        for video in msg['video']:
                            video_image_url = video.get('url1') or video.get('pic_url')
                            if video_image_url:
                                image_base64 = await self.get_image_base64_by_url(video_image_url)
                                image_manager = get_image_manager()
                                image_description = await image_manager.get_image_description(image_base64)
                                images.append(image_description)
                    # 提取视频信息(.m3u8)
                    videos = []
                    if 'video' in msg:
                        for video in msg['video']:
                            url = video.get('url3')
                            videos.append(url)
                    # 提取转发信息
                    rt_con = ""
                    if "rt_con" in msg:
                        rt_con = msg.get("rt_con").get("content")
                    #存储信息
                    feeds_list.append({"tid": tid,
                                       "created_time": created_time,
                                       "content": content,
                                       "images": images,
                                       "videos": videos,
                                       "rt_con": rt_con})
            if len(feeds_list) == 0:
                return [{"error": '你已经看过所有说说了，没有必要再看一遍'}]
            return feeds_list

        except Exception as e:
            #logger.error(str(json_data))
            return [{"error": f'{e},你没有看到任何东西'}]

    async def monitor_get_list(self, num: int) -> list[dict[str, Any]]:
        """获得qq号为target_qq的好友说说列表"""
        res = await self.do(
            method="GET",
            url=self.ZONE_LIST_URL,
            params={
                "uin": self.uin,  # QQ号
                "scope": 0,  # 访问范围
                "view": 1,  # 查看权限
                "filter": "all",  # 全部动态
                "flag": 1,  # 标记
                "applist": "all",  # 所有应用
                "pagenum": 1,  # 页码
                "count": num,  # 每页条数
                "aisortEndTime": 0,  # AI排序结束时间
                "aisortOffset": 0,  # AI排序偏移
                "aisortBeginTime": 0,  # AI排序开始时间
                "begintime": 0,  # 开始时间
                "format": "json",  # 返回格式
                "g_tk": self.gtk2,  # 令牌
                "useutf8": 1,  # 使用UTF8编码
                "outputhtmlfeed": 1  # 输出HTML格式
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/91.0.4472.124 Safari/537.36",
                "Referer": f"https://user.qzone.qq.com/{self.uin}",
                "Host": "user.qzone.qq.com",
                "Connection": "keep-alive"
            },
        )

        if res.status_code != 200:
            raise Exception("访问失败: " + str(res.status_code))

        data = res.text
        if data.startswith('_Callback(') and data.endswith(');'):
            # 1. 去掉res首尾的 _Callback( 和 );
            data = data[len('_Callback('):-2]
        data = data.replace('undefined', 'null')
        try:
            # 2. 解析JSON数据
            data = json5.loads(data)['data']['data']
        except Exception as e:
            logger.error(f"解析错误: {e}")
            # 3. 提取说说内容
        try:
            feeds_list = []
            for feed in data:
                if not feed:  # 跳过None值
                    continue
                # 过滤广告类内容
                appid = str(feed.get('appid', ''))
                if appid != '311':
                    continue
                target_qq = feed.get('uin', '')
                tid = feed.get('key', '')
                if not target_qq or not tid:
                    logger.error(f"无效的说说数据: target_qq={target_qq}, tid={tid}")
                    continue
                #print(feed)

                html_content = feed.get('html', '')
                if not html_content:
                    logger.error(f"说说内容为空: UIN={target_qq}, TID={tid}")
                    continue

                soup = bs4.BeautifulSoup(html_content, 'html.parser')
                is_read = False
                # 根据点赞状态判断是否已读
                like_btn = soup.find('a', class_='qz_like_btn_v3')
                if like_btn:
                    data_islike = like_btn.get('data-islike')
                else:
                    like_btn = soup.find('a', attrs={'data-islike': True})
                    if like_btn:
                        data_islike = like_btn.get('data-islike')
                    else:
                        data_islike = None
                        logger.error("未找到包含data-islike属性的元素")
                if data_islike == '1':
                    is_read = True

                # 只处理未读说说
                if is_read and target_qq != str(self.uin):
                    continue
                # 提取文字内容
                text_div = soup.find('div', class_='f-info')
                text = text_div.get_text(strip=True) if text_div else ""
                # 提取转发内容
                rt_con = ""
                txt_box = soup.select_one('div.txt-box')
                if txt_box:
                    # 获取除昵称外的纯文本内容
                    rt_con = txt_box.get_text(strip=True)
                    # 分割掉昵称部分（从第一个冒号开始取内容）
                    if '：' in rt_con:
                        rt_con = rt_con.split('：', 1)[1].strip()
                # 提取图片URL
                image_urls = []
                # 查找所有图片容器
                img_box = soup.find('div', class_='img-box')
                if img_box:
                    for img in img_box.find_all('img'):
                        src = img.get('src')
                        if src and not src.startswith('http://qzonestyle.gtimg.cn'):  # 过滤表情图标
                            image_urls.append(src)
                # 临时视频处理办法（视频缩略图）
                img_tag = soup.select_one('div.video-img img')
                if img_tag and 'src' in img_tag.attrs:
                    image_urls.append(img_tag['src'])
                # 去重URL
                unique_urls = list(set(image_urls))
                # 获取图片描述
                images = []
                for url in unique_urls:
                    try:
                        image_base64 = await self.get_image_base64_by_url(url)
                        image_manager = get_image_manager()
                        description = await image_manager.get_image_description(image_base64)
                        images.append(description)
                    except Exception as e:
                        logger.info(f'图片识别失败: {url} - {str(e)}')
                # 获取视频
                videos = []
                video_div = soup.select_one('div.img-box.f-video-wrap.play')
                if video_div and 'url3' in video_div.attrs:
                    videos.append(video_div['url3'])
                # 获取评论内容
                comments_list = []
                # 查找所有评论项（包括主评论和回复）
                comment_items = soup.select('li.comments-item.bor3')
                if comment_items:
                    for item in comment_items:
                        # 提取基本信息
                        qq_account = item.get('data-uin', '')
                        comment_tid = item.get('data-tid', '')
                        nickname = item.get('data-nick', '')

                        # 查找评论内容
                        content_div = item.select_one('div.comments-content')
                        if content_div:
                            # 移除操作按钮（回复/删除）
                            for op in content_div.select('div.comments-op'):
                                op.decompose()
                            # 获取纯文本内容
                            content = content_div.get_text(' ', strip=True)
                        else:
                            content = ""

                        # 检查是否是回复
                        parent_tid = None
                        parent_div = item.find_parent('div', class_='mod-comments-sub')
                        if parent_div:
                            parent_li = parent_div.find_parent('li', class_='comments-item')
                            if parent_li:
                                parent_tid = parent_li.get('data-tid')

                        comments_list.append({
                            'qq_account': qq_account,
                            'nickname': nickname,
                            'comment_tid': comment_tid,
                            'content': content,
                            'parent_tid': parent_tid
                        })

                feeds_list.append({
                    'target_qq': target_qq,
                    'tid': tid,
                    'content': text,
                    'images': images,
                    'videos': videos,
                    'rt_con': rt_con,
                    'comments': comments_list,
                })

            logger.info(f"成功解析 {len(feeds_list)} 条说说")
            return feeds_list
        except Exception as e:
            logger.error(f'解析说说错误：{str(e)}', exc_info=True)
            return []


def get_qzone_api(qq_account: str) -> QzoneAPI | None:
    """获取QzoneAPI实例"""
    cookie_file = get_cookie_file_path(qq_account)
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
        except Exception as e:
            logger.error(f"读取 cookie 文件失败: {cookie_file}，错误: {e}")
            cookies = None
    else:
        logger.error(f"cookie 文件不存在: {cookie_file}")
        cookies = None

    if cookies:
        qzone = QzoneAPI(cookies)
        return qzone
    else:
        return None


async def send_feed(message: str, image_directory: str, qq_account: str, enable_image: bool):
    """发送说说及图片"""
    qzone = get_qzone_api(qq_account)

    images = []
    if os.path.exists(image_directory) and enable_image:
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


async def read_feed(qq_account: str, target_qq: str, num: int):
    """阅读指定qq号的说说，返回说说列表"""
    qzone = get_qzone_api(qq_account)

    try:
        feeds_list = await qzone.get_list(target_qq, num)
        #print(feeds_list)
        return feeds_list
    except Exception as e:
        logger.error("获取list失败")
        logger.error(traceback.format_exc())
        return []


async def monitor_read_feed(qq_account: str, num: int):
    """自动阅读说说，返回说说列表"""
    qzone = get_qzone_api(qq_account)

    try:
        feeds_list = await qzone.monitor_get_list(num)
        #print(feeds_list)
        return feeds_list
    except Exception as e:
        logger.error("获取list失败")
        logger.error(traceback.format_exc())
        return []


async def like_feed(qq_account: str, target_qq: str, fid: str):
    """点赞说说"""
    qzone = get_qzone_api(qq_account)

    success = await qzone.like(fid, target_qq)
    if not success:
        logger.error("点赞失败")
        logger.error(traceback.format_exc())
        return success
    return True


async def comment_feed(qq_account: str, target_qq: str, fid: str, content: str):
    """评论说说"""
    qzone = get_qzone_api(qq_account)

    success = await qzone.comment(fid, target_qq, content)
    if not success:
        logger.error("评论失败")
        logger.error(traceback.format_exc())
        return False
    return True


async def reply_feed(fid: str, qq_account: str, target_qq: str, target_nickname: str, content: str, comment_tid: str):
    """评论说说"""
    qzone = get_qzone_api(qq_account)

    success = await qzone.reply(fid, target_qq, target_nickname, content, comment_tid)
    if not success:
        logger.error("评论失败")
        logger.error(traceback.format_exc())
        return False
    return True


async def generate_image_by_sf(api_key: str, story: str, image_dir: str, batch_size: int = 1) -> bool:
    """用SiliconFlow生成图片并保存"""
    logger.info(f"正在生成图片,保存路径{image_dir}")
    models = llm_api.get_available_models()
    prompt_model = "replyer_1"
    model_config = models[prompt_model]
    bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
    bot_details = config_api.get_global_config("personality.identity", "未知")
    if not model_config:
        logger.error('配置模型失败')
        return False
    success, prompt, reasoning, model_name = await llm_api.generate_with_model(
        prompt=f"""
        请根据以下QQ空间说说内容配图，并构建生成配图的风格和prompt。
        说说主人信息：'{bot_personality},{str(bot_details)}'。
        说说内容:'{story}'。 
        请注意：仅回复用于生成图片的prompt，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
        """,
        model_config=model_config,
        request_type="story.generate",
        temperature=0.3,
        max_tokens=1000
    )
    if success:
        logger.info(f'即将生成说说配图：{prompt}')
    else:
        logger.error('生成说说配图prompt失败')
    try:
        # SiliconFlow API
        sf_url = "https://api.siliconflow.cn/v1/images/generations"
        sf_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        sf_data = {
            "model": "Kwai-Kolors/Kolors",
            "prompt": prompt,
            "negative_prompt": "lowres, bad anatomy, bad hands, text, error, cropped, worst quality, low quality, "
                               "normal quality, jpeg artifacts, signature, watermark, username, blurry",
            "image_size": "1024x1024",
            "batch_size": batch_size,
            "seed": random.randint(1, 9999999999),
            "num_inference_steps": 20,
            "guidance_scale": 7.5,
        }
        res = requests.request(method="post", url=sf_url, headers=sf_headers, json=sf_data)
        if res.status_code != 200:
            logger.error(f'生成图片出错，错误码[{res.status_code}]')
            #print(res.text)
            return False
        json_data = res.json()
        image_urls = [img["url"] for img in json_data["images"]]
        # 确保目录存在
        Path(image_dir).mkdir(parents=True, exist_ok=True)
        for i, img_url in enumerate(image_urls):
            try:
                # 下载图片
                img_response = requests.get(img_url)
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
        return False


async def get_send_history(qq_account: str) -> str:
    qzone = get_qzone_api(qq_account)
    feeds_list = await qzone.get_list(target_qq=qq_account, num=5)
    history = "==================="
    for feed in feeds_list:
        if not feed["rt_con"]:
            history += f"""
            时间：'{feed["created_time"]}'。
            说说内容：'{feed["content"]}'
            图片：'{feed["images"]}'
            ===================
            """
        else:
            history += f"""
            时间: '{feed["created_time"]}'
            转发了一条说说，内容为: '{feed["rt_con"]}'
            图片: '{feed["images"]}'
            对该说说的评论为: '{feed["content"]}'
            ===================
            """
    return history


# ===== 插件Command组件 =====
class SendFeedCommand(BaseCommand):
    """发说说Command - 响应/send_feed命令"""

    command_name = "send_feed"
    command_description = "发一条说说"

    command_pattern = r"^/send_feed(?:\s+(?P<topic>\w+))?$"
    command_help = "发一条主题为<topic>或随机的说说"
    command_examples = ["/send_feed", "/send_feed topic"]
    intercept_message = True

    def check_permission(self, qq_account: str) -> bool:
        """检查qq号为qq_account的用户是否拥有权限"""
        permission_list = self.get_config("send.permission")
        permission_type = self.get_config("send.permission_type")
        logger.info(f'[{self.command_name}]{permission_type}:{str(permission_list)}')
        if permission_type == 'whitelist':
            return qq_account in permission_list
        elif permission_type == 'blacklist':
            return qq_account not in permission_list
        else:
            logger.error('permission_type错误，可能为拼写错误')
            return False

    async def execute(self) -> tuple[bool, Optional[str], bool]:
        #权限检查
        user_id = self.message.message_info.user_info.user_id
        if not self.check_permission(user_id):
            logger.info(f"{user_id}无{self.command_name}权限")
            await self.send_text(f"{user_id}权限不足，无权使用此命令")
            return False, f"{user_id}权限不足，无权使用此命令", True
        else:
            logger.info(f"{user_id}拥有{self.command_name}权限")

        topic = self.matched_groups.get("topic")
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]
        if not model_config:
            return False, "未配置LLM模型", True

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")

        qq_account = config_api.get_global_config("bot.qq_account", "")
        port = self.get_config("plugin.http_port", "9999")
        image_dir = self.get_config("send.image_directory", "./images")
        image_num = self.get_config("send.ai_image_number", 1)
        enable_ai_image = self.get_config("send.enable_ai_image", False)
        apikey = self.get_config("models.siliconflow_apikey", "")

        # 更新cookies
        try:
            await renew_cookies(port)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败", True

        if topic:
            prompt = f"""
            你是'{bot_personality}'，你想写一条主题是'{topic}'的说说发表在qq空间上，
            {bot_expression}
            不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，
            只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
            """
        else:
            prompt = f"""
            你是'{bot_personality}'，你想写一条说说发表在qq空间上，主题不限
            {bot_expression}
            不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，
            只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
            """

        prompt += "\n以下是你以前发过的说说，写新说说时注意不要在相隔不长的时间发送相同主题的说说"
        prompt += await get_send_history(qq_account)
        prompt += "\n不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"

        success, story, reasoning, model_name = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="story.generate",
            temperature=0.3,
            max_tokens=1000
        )

        if not success:
            return False, "生成说说内容失败", True

        logger.info(f"成功生成说说内容：'{story}'")

        if enable_ai_image and apikey:
            await generate_image_by_sf(api_key=apikey, story=story, image_dir=image_dir, batch_size=image_num)
        elif enable_ai_image and not apikey:
            logger.error('请填写apikey')

        # 发送说说
        enable_image = self.get_config("send.enable_image", "true")
        success = await send_feed(story, image_dir, qq_account, enable_image)
        if not success:
            return False, "发送说说失败", True
        await self.send_text(f"已发送说说：\n{story}")
        return True, 'success', True


# ===== 插件Action组件 =====
class SendFeedAction(BaseAction):
    """发说说Action - 只在用户要求发说说时激活"""

    action_name = "send_feed"
    action_description = "发一条相应主题的说说"

    focus_activation_type = ActionActivationType.KEYWORD
    normal_activation_type = ActionActivationType.KEYWORD

    activation_keywords = ["说说", "空间", "动态"]
    keyword_case_sensitive = False

    action_parameters = {
        "topic": "要发送的说说主题",
        "user_name": "要求你发说说的好友的qq名称",
    }
    action_require = [
        "用户要求发说说时使用",
        "当有人希望你更新qq空间时使用",
        "当你认为适合发说说时使用",
    ]
    associated_types = ["text", "emoji"]

    def check_permission(self, qq_account: str) -> bool:
        """检查qq号为qq_account的用户是否拥有权限"""
        permission_list = self.get_config("send.permission")
        permission_type = self.get_config("send.permission_type")
        logger.info(f'[{self.action_name}]{permission_type}:{str(permission_list)}')
        if permission_type == 'whitelist':
            return qq_account in permission_list
        elif permission_type == 'blacklist':
            return qq_account not in permission_list
        else:
            logger.error('permission_type错误，可能为拼写错误')
            return False

    async def execute(self) -> Tuple[bool, str]:
        #检查权限
        user_name = self.action_data.get("user_name", "")
        person_id = person_api.get_person_id_by_name(user_name)
        if not person_id:
            logger.error(f"未找到用户 {user_name} 的person_id")
            success, reply_set, prompt_ = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                action_data={"extra_info_block": f'你不认识{user_name}，无法阅读他的说说，请用符合你人格特点的方式拒绝请求'}
            )
            if success and reply_set:
                reply_type, reply_content = reply_set[0]
                await self.send_text(reply_content)
            return False, "未找到用户的person_id"
        user_id = await person_api.get_person_value(person_id, "user_id")
        if not self.check_permission(user_id):  # 若权限不足
            logger.info(f"{user_id}无{self.action_name}权限")
            success, reply_set, prompt_ = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                action_data={"extra_info_block": f'{user_name}无权命令你发送说说，请用符合你人格特点的方式拒绝请求'}
            )
            if success and reply_set:
                reply_type, reply_content = reply_set[0]
                await self.send_text(reply_content)
            return False, ""
        else:
            logger.info(f"{user_id}拥有{self.action_name}权限")
        topic = self.action_data.get("topic", "")
        logger.info(f"说说主题:{topic}")
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]

        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")
        qq_account = config_api.get_global_config("bot.qq_account", "")

        port = self.get_config("plugin.http_port", "9999")
        image_dir = self.get_config("send.image_directory", "./images")
        image_num = self.get_config("send.ai_image_number", 1)
        enable_ai_image = self.get_config("send.enable_ai_image", False)
        apikey = self.get_config("models.siliconflow_apikey", "")
        try:
            await renew_cookies(port)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败"

        prompt = f"""
        你是{bot_personality}，你想写一条主题是{topic}的说说发表在qq空间上，
        {bot_expression}
        不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，
        只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
        """
        prompt += "\n以下是你以前发过的说说，写新说说时注意不要在相隔不长的时间发送相同主题的说说"
        prompt += await get_send_history(qq_account)
        prompt += "\n只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"
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
        if enable_ai_image and apikey:
            await generate_image_by_sf(api_key=apikey, story=story, image_dir=image_dir, batch_size=image_num)
        elif enable_ai_image and not apikey:
            logger.error('请填写apikey')

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
        await self.send_text('我发了一条说说啦~')
        # 生成回复
        success, reply_set, prompt_ = await generator_api.generate_reply(
            chat_stream=self.chat_stream,
            action_data={"extra_info_block": f'你刚刚发了一条说说，内容为{story}'}
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

    activation_keywords = ["说说", "空间", "动态"]
    keyword_case_sensitive = False

    action_parameters = {
        "target_name": "需要阅读动态的好友的qq名称",
        "user_name": "要求你阅读动态的好友的qq名称"
    }

    action_require = [
        "需要阅读某人动态、说说、QQ空间时使用",
        "当有人希望你评价某人的动态、说说、QQ空间",
        "当你认为适合阅读说说、动态、QQ空间时使用",
    ]
    associated_types = ["text"]

    def check_permission(self, qq_account: str) -> bool:
        """检查qq号为qq_account的用户是否拥有权限"""
        permission_list = self.get_config("read.permission")
        permission_type = self.get_config("read.permission_type")
        logger.info(f'[{self.action_name}]{permission_type}:{str(permission_list)}')
        if permission_type == 'whitelist':
            return qq_account in permission_list
        elif permission_type == 'blacklist':
            return qq_account not in permission_list
        else:
            logger.error('permission_type错误，可能为拼写错误')
            return False

    async def execute(self) -> Tuple[bool, str]:
        #检查权限
        user_name = self.action_data.get("user_name", "")
        person_id = person_api.get_person_id_by_name(user_name)
        if not person_id:
            logger.error(f"未找到用户 {user_name} 的person_id")
            success, reply_set, prompt_ = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                action_data={"extra_info_block": f'你不认识{user_name}，无法阅读他的说说，请用符合你人格特点的方式拒绝请求' }
            )
            if success and reply_set:
                reply_type, reply_content = reply_set[0]
                await self.send_text(reply_content)
            return False, "未找到用户的person_id"
        user_id = await person_api.get_person_value(person_id, "user_id")
        if not self.check_permission(user_id):  # 若权限不足
            logger.info(f"{user_id}无{self.action_name}权限")
            success, reply_set, prompt_ = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                action_data={"extra_info_block": f'{user_name}无权命令你阅读说说，请用符合人格的方式进行拒绝的回复'}
            )
            if success and reply_set:
                reply_type, reply_content = reply_set[0]
                await self.send_text(reply_content)
            return False, ""
        else:
            logger.info(f"{user_id}拥有{self.action_name}权限")

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
        feeds_list = await read_feed(qq_account, target_qq, num)
        if 'error' not in feeds_list[0]:
            logger.info(f"成功读取到{len(feeds_list)}条说说")
        #模型配置
        models = llm_api.get_available_models()
        text_model = self.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]
        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")
        #错误处理，如对方设置了访问权限
        if 'error' in feeds_list[0]:
            success, reply_set, prompt_ = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                action_data={"extra_info_block": f'你在读取说说的时候出现了错误，错误原因：{feeds_list[0].get("error")}'}
            )

            if success and reply_set:
                reply_type, reply_content = reply_set[0]
                await self.send_text(reply_content)
                return True, 'success'

            return False, '生成回复失败'
        #逐条点赞回复
        for feed in feeds_list:
            await asyncio.sleep(3 + random.random())
            content = feed["content"]
            if feed["images"]:
                for image in feed["images"]:
                    content = content + image
            fid = feed["tid"]
            rt_con = feed["rt_con"]
            if random.random() <= comment_possibility:
                #评论说说
                if not rt_con:
                    prompt = f"""
                    你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，
                    你看到了你的好友'{target_name}'qq空间上内容是'{content}'的说说，你想要发表你的一条评论，
                    {bot_expression}，回复的平淡一些，简短一些，说中文，
                    不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                    """
                else:
                    prompt = f"""
                    你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，
                    你看到了你的好友'{target_name}'在qq空间上转发了一条内容为'{rt_con}'的说说，你的好友的评论为'{content}'
                    你想要发表你的一条评论，{bot_expression}，回复的平淡一些，简短一些，说中文，
                    不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                    """
                logger.info(f"正在评论'{target_name}'的说说：{content[:20]}...")
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

                success = await comment_feed(qq_account, target_qq, fid, comment)
                if not success:
                    logger.error(f"评论说说'{content}'失败")
                    return False, "评论说说失败"
                logger.info(f"发送评论'{comment}'成功")
            # 点赞说说
            if random.random() <= like_possibility:
                success = await like_feed(qq_account, target_qq, fid)
                if not success:
                    logger.error(f"点赞说说'{content}'失败")
                    return False, "点赞说说失败"
                logger.info(f"点赞说说'{content[:10]}..'成功")

        # 生成回复
        success, reply_set, prompt_ = await generator_api.generate_reply(
            chat_stream=self.chat_stream,
            action_data={"extra_info_block": f'你刚刚成功读了以下说说：{feeds_list}，请告知你已经读了说说，生成回复'}
        )

        if success and reply_set:
            reply_type, reply_content = reply_set[0]
            await self.send_text(reply_content)
            return True, 'success'

        return False, '生成回复失败'


# ===== 定时任务功能 =====
class FeedMonitor:
    """定时监控好友说说的类"""

    def __init__(self, plugin):
        self.plugin = plugin
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
        while self.is_running:
            try:
                # 获取配置
                interval = self.plugin.get_config("monitor.interval_minutes", 5)
                read_num = 3

                # 等待指定时间
                await asyncio.sleep(interval * 60)

                # 执行监控任务
                await self.check_feeds(read_num)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"监控任务出错: {str(e)}")
                traceback.print_exc()
                # 出错后等待一段时间再重试
                await asyncio.sleep(300)

    async def check_feeds(self, read_num: int):
        """检查好友说说"""

        qq_account = config_api.get_global_config("bot.qq_account", "")
        port = self.plugin.get_config("plugin.http_port", "9999")
        #模型配置
        models = llm_api.get_available_models()
        text_model = self.plugin.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]
        if not model_config:
            return False, "未配置LLM模型"

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")

        # 更新cookies
        try:
            await renew_cookies(port)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return False, "更新cookies失败"

        try:
            logger.info(f"监控任务: 正在获取说说列表")
            feeds_list = await monitor_read_feed(qq_account, read_num)
        except Exception as e:
            logger.error(f"获取说说列表失败: {str(e)}")
            return False, "获取说说列表失败"
            # 逐条点赞回复
        try:
            if len(feeds_list) == 0:
                logger.info('未读取到新说说')
                return True, "success"
            for feed in feeds_list:
                await asyncio.sleep(3 + random.random())
                content = feed["content"]
                if feed["images"]:
                    for image in feed["images"]:
                        content = content + image
                fid = feed["tid"]
                target_qq = feed["target_qq"]
                rt_con = feed["rt_con"]
                comments_list = feed["comments"]
                # 检查自己的说说评论并回复
                if target_qq == qq_account:
                    enable_auto_reply = self.plugin.get_config("monitor.enable_auto_reply", False)
                    if not enable_auto_reply:
                        continue
                    # 获取未回复的评论
                    ignored_tids = []  # 已回复的评论tid
                    list_to_reply = []  # 待回复的评论
                    if comments_list:
                        #print(comments_list)
                        for comment in comments_list:
                            if comment['parent_tid'] and comment['qq_account'] == qq_account:
                                ignored_tids.append(comment['parent_tid'])
                        list_to_reply = [
                            comment for comment in comments_list
                            if comment['parent_tid'] is None  # 只考虑主评论
                               and comment['comment_tid'] not in ignored_tids  # 没有被bot回复过
                        ]

                    if not list_to_reply:
                        continue
                    for comment in list_to_reply:
                        # 逐条回复评论
                        prompt = f"""
                        你是'{bot_personality}'，你的好友'{comment['nickname']}'评论了你QQ空间上的一条内容为“{content}”说说，
                        你的好友对该说说的评论为:“{comment["content"]}”，你想要对此评论进行回复
                        {bot_expression}，回复的平淡一些，简短一些，说中文，
                        不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                        """
                        logger.info(f"正在回复{comment['nickname']}的评论：{comment['content']}...")
                        success, reply, reasoning, model_name = await llm_api.generate_with_model(
                            prompt=prompt,
                            model_config=model_config,
                            request_type="story.generate",
                            temperature=0.3,
                            max_tokens=1000
                        )

                        if not success:
                            return False, "生成回复内容失败"

                        logger.info(f"成功生成回复内容：'{reply}'，即将发送")

                        await renew_cookies(port)
                        success = await reply_feed(fid, qq_account, target_qq, comment['nickname'], reply,
                                                   comment['comment_tid'])
                        if not success:
                            logger.error(f"回复评论{comment['content']}失败")
                            return False, "回复评论失败"
                        logger.info(f"发送回复'{reply}'成功")
                        await asyncio.sleep(10 + random.random() * 10)
                    continue
                # 评论他人说说
                if not rt_con:
                    prompt = f"""
                    你是'{bot_personality}'，你正在浏览你好友'{target_qq}'的QQ空间，
                    你看到了你的好友'{target_qq}'qq空间上内容是'{content}'的说说，你想要发表你的一条评论，
                    {bot_expression}，回复的平淡一些，简短一些，说中文，
                    不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                    """
                else:
                    prompt = f"""
                    你是'{bot_personality}'，你正在浏览你好友'{target_qq}'的QQ空间，
                    你看到了你的好友'{target_qq}'在qq空间上转发了一条内容为'{rt_con}'的说说，你的好友的评论为'{content}'
                    你想要发表你的一条评论，{bot_expression}，回复的平淡一些，简短一些，说中文，
                    不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容
                    """
                logger.info(f"正在评论'{target_qq}'的说说：{content[:20]}...")
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

                success = await comment_feed(qq_account, target_qq, fid, comment)
                if not success:
                    logger.error(f"评论说说{content}失败")
                    return False, "评论说说失败"
                logger.info(f"发送评论'{comment}'成功")
                # 点赞说说
                success = await like_feed(qq_account, target_qq, fid)
                if not success:
                    logger.error(f"点赞说说{content}失败")
                    return False, "点赞说说失败"
                logger.info(f'点赞说说{content[:10]}..成功')
                return True, 'success'
        except Exception as e:
            logger.error(f"点赞评论失败: {str(e)}")
            return False, "点赞评论失败"


class ScheduleSender:
    """定时发送说说的类"""

    def __init__(self, plugin):
        self.plugin = plugin
        self.is_running = False
        self.task = None
        self.last_send_time = 0

    async def start(self):
        """启动定时发送任务"""
        if self.is_running:
            return
        self.is_running = True
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

    async def _schedule_loop(self):
        """定时发送循环"""
        while self.is_running:
            try:
                # 获取配置
                schedule_times = self.plugin.get_config("schedule.schedule_times", ["08:00", "20:00"])
                current_time = datetime.datetime.now().strftime("%H:%M")

                # 检查是否到达发送时间
                if current_time in schedule_times:
                    # 避免同一分钟内重复发送
                    if time.time() - self.last_send_time > 60:
                        logger.info("定时任务：正在发送说说")
                        self.last_send_time = time.time()
                        await self.send_scheduled_feed()

                # 每分钟检查一次
                await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时发送任务出错: {str(e)}")

    async def send_scheduled_feed(self):
        """发送定时说说"""
        models = llm_api.get_available_models()
        text_model = self.plugin.get_config("models.text_model", "replyer_1")
        model_config = models[text_model]
        if not model_config:
            logger.error("未配置LLM模型")
            return

        # 获取主题设置
        random_topic = self.plugin.get_config("schedule.random_topic", True)
        fixed_topics = self.plugin.get_config("schedule.fixed_topics", ["日常生活", "心情分享", "有趣见闻"])

        bot_personality = config_api.get_global_config("personality.personality_core", "一个机器人")
        bot_expression = config_api.get_global_config("expression.expression_style", "内容积极向上")

        qq_account = config_api.get_global_config("bot.qq_account", "")
        port = self.plugin.get_config("plugin.http_port", "9999")
        image_dir = self.plugin.get_config("send.image_directory", "./images")
        enable_image = self.plugin.get_config("send.enable_image", True)
        image_num = self.plugin.get_config("send.ai_image_number", 1)
        enable_ai_image = self.plugin.get_config("send.enable_ai_image", False)
        apikey = self.plugin.get_config("models.siliconflow_apikey", "")
        # 更新cookies
        try:
            await renew_cookies(port)
        except Exception as e:
            logger.error(f"更新cookies失败: {str(e)}")
            return

        # 生成说说内容
        if random_topic:
            prompt = f"""
            你是'{bot_personality}'，你想写一条说说发表在qq空间上，主题不限
            {bot_expression}
            不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，
            只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
            """
        else:
            fixed_topic = random.choice(fixed_topics)
            prompt = f"""
            你是'{bot_personality}'，你想写一条主题是'{fixed_topic}'的说说发表在qq空间上，
            {bot_expression}
            不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，
            只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )
            """

        prompt += "\n以下是你最近发过的说说，写新说说时注意不要在相隔不长的时间发送相似内容的说说\n"
        prompt += await get_send_history(qq_account)
        prompt += "\n只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"

        success, story, reasoning, model_name = await llm_api.generate_with_model(
            prompt=prompt,
            model_config=model_config,
            request_type="story.generate",
            temperature=0.3,
            max_tokens=1000
        )

        if not success:
            logger.error("生成说说内容失败")
            return

        logger.info(f"定时任务生成说说内容：'{story}'")

        if enable_ai_image and apikey:
            await generate_image_by_sf(api_key=apikey, story=story, image_dir=image_dir, batch_size=image_num)
        elif enable_ai_image and not apikey:
            logger.error('请填写apikey')

        # 发送说说
        success = await send_feed(story, image_dir, qq_account, enable_image)
        if success:
            logger.info(f"定时任务成功发送说说: {story}")
        else:
            logger.error("定时任务发送说说失败")


# ===== 插件注册 =====
@register_plugin
class MaizonePlugin(BasePlugin):
    """Maizone插件 - 让麦麦发QQ空间"""

    plugin_name = "Maizone"
    plugin_description = "让麦麦实现QQ空间点赞、评论、发说说"
    plugin_version = "1.2.2"
    plugin_author = "internetsb"
    enable_plugin = False
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ['httpx', 'requests', 'bs4', 'json5']
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
            "http_port": ConfigField(type=str, default='9999', description="Napcat设定http服务器端口号"),
        },
        "models": {
            "text_model": ConfigField(type=str, default="replyer_1", description="生成文本的模型（从全局变量读取）"),
            "siliconflow_apikey": ConfigField(type=str, default="", description="用于硅基流动ai生图的apikey"),
        },
        "send": {
            "permission": ConfigField(type=list, default=['114514', '1919810', ],
                                      description="权限QQ号列表（请以相同格式添加）"),
            "permission_type": ConfigField(type=str, default='whitelist',
                                           description="whitelist:在列表中的QQ号有权限，blacklist:在列表中的QQ号无权限"),
            "enable_image": ConfigField(type=bool, default=False,
                                        description="是否启用带图片的说说（禁用Ai生图将从已注册表情包中获取）"),
            "enable_ai_image": ConfigField(type=bool, default=False, description="是否启用Ai生成带图片的说说"),
            "ai_image_number": ConfigField(type=int, default=1, description="一次生成几张图片(范围1至4)"),
            "image_directory": ConfigField(type=str, default="./plugins/Maizone/images", description="图片存储目录")
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
            "interval_minutes": ConfigField(type=int, default=5, description="阅读间隔(分钟)"),
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

        # 根据配置启用插件
        if self.get_config("plugin.enable", True):
            self.enable_plugin = True

            # 根据配置初始化监控
            if self.get_config("monitor.enable_auto_monitor", False):
                self.monitor = FeedMonitor(self)
                asyncio.create_task(self._start_monitor_after_delay())

            # 根据配置初始化日程
            if self.get_config("schedule.enable_schedule", False):
                self.scheduler = ScheduleSender(self)
                asyncio.create_task(self._start_scheduler_after_delay())

    async def _start_monitor_after_delay(self):
        """延迟启动监控任务"""
        # 等待一段时间让插件完全初始化
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
