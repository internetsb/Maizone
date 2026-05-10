import base64
import datetime
import json
import sys
import os
import time
from typing import Any
from pathlib import Path

from maibot_sdk import API
import httpx
# 额外依赖库
import json5
import bs4

cookie_path = str(Path(__file__).parent.resolve() / "cookies.json")
# ===== logger =====
# logger在插件加载时注入ctx.logger实例
class NoLogger:
    def info(self, msg):
        pass
    def error(self, msg):
        pass
    def warning(self, msg):
        pass
    def debug(self, msg):
        pass
    def critical(self, msg):
        pass
logger = NoLogger()

def set_qzone_logger(log_instance):
    """设置logger实例"""
    global logger
    logger = log_instance

# ===== Image Manager =====
class NoImageManager:
    async def get_image_description(self, image_base64: str) -> str:
        return "图片"
image_manager = NoImageManager()
def set_image_manager(image_manager_instance):
    """设置image_manager实例"""
    global image_manager
    image_manager = image_manager_instance
# ===== 辅助函数 =====
def generate_gtk(skey: str) -> str:
    """特定协议算法，生成QQ空间的gtk值"""
    hash_val = 5381
    for i in range(len(skey)):
        hash_val += (hash_val << 5) + ord(skey[i])
    return str(hash_val & 2147483647)


def get_picbo_and_richval(upload_result) -> tuple[str | None, str | None]:
    """从上传结果中提取图片的picbo和richval值用于发表图片说说"""
    if not isinstance(upload_result, dict) or 'ret' not in upload_result:
        logger.error("获取图片picbo和richval失败: 返回数据不合法")
        return None, None
    if upload_result.get('ret') != 0:
        logger.error(f"上传图片失败: {upload_result}")
        return None, None
    
    try:
        picbo = upload_result['data']['url'].split('&bo=')[1]
        richval = ",{},{},{},{},{},{},,{},{}".format(
            upload_result['data']['albumid'], upload_result['data']['lloc'],
            upload_result['data']['sloc'], upload_result['data']['type'],
            upload_result['data']['height'], upload_result['data']['width'],
            upload_result['data']['height'], upload_result['data']['width']
        )
        return picbo, richval
    except (KeyError, IndexError) as e:
        logger.error(f"提取picbo和richval失败: {e}")
        return None, None


def extract_code_html(html_content: str) -> Any | None:
    """从QQ空间响应的HTML内容中提取响应码code的值"""
    try:
        soup = bs4.BeautifulSoup(html_content, 'html.parser')
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'frameElement.callback' in script.string:
                script_content = script.string
                start_index = script_content.find('frameElement.callback(') + len('frameElement.callback(')
                end_index = script_content.rfind(');')
                if 0 < start_index < end_index:
                    json_str = script_content[start_index:end_index].strip()
                    if json_str.endswith(';'):
                        json_str = json_str[:-1]
                    data = json5.loads(json_str)
                    if isinstance(data, dict) and 'code' in data:
                        return data.get("code")
                    else:
                        continue
        return None
    except:
        return None


def extract_code_json(json_response) -> Any | None:
    """
    从QQ空间响应的json内容中提取code值，如果不存在则返回None
    """
    try:
        if isinstance(json_response, str):
            data = json.loads(json_response)
        else:
            data = json_response

        return data.get('code', None)
    except (json.JSONDecodeError, KeyError, AttributeError):
        return None


def image_to_base64(image: bytes) -> str:
    """将图片转换为base64字符串"""
    pic_base64 = base64.b64encode(image)
    return str(pic_base64)[2:-1]


class QzoneAPI:
    # QQ空间url常量
    UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION_PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    DOLIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    REPLY_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    ZONE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"

    def __init__(self, cookies_dict: dict = {}):
        self.cookies = cookies_dict
        self.uin = self.cookies.get("uin", "").lstrip("o0")   # uin 从cookies中提取，去除前导o和0
        if self.uin == "":
            logger.error("未找到uin，请检查cookies是否正确")
            return
        self.qq_nickname = ""
        self.gtk2 = ''

        if 'p_skey' in self.cookies:
            self.gtk2 = generate_gtk(self.cookies['p_skey'])

    async def get_image_base64_by_url(self, url: str) -> str | None:
        """
            从指定的URL获取图片并将其转换为Base64编码格式，用于解析配图。

            Args:
                url (str): 图片的URL地址。

            Returns:
                str: 图片的Base64编码字符串，如果获取失败则返回None。
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://qzone.qq.com/"
        }
        async with httpx.AsyncClient(follow_redirects=True) as client:
            request = httpx.Request("GET", url, headers=headers)
            response = await client.send(request)

        if response.status_code != 200:
            logger.error(f"请求失败: {response.url} 状态码: {response.status_code}")
            logger.error(f"原始URL: {url}")
            return None

        return base64.b64encode(response.content).decode('utf-8')

    async def upload_image(self, image: bytes) -> str | None:
        """
            上传图片到QQ空间。

            Args:
                image (bytes): 图片的二进制数据。

            Returns:
                str: 上传成功后返回的响应数据，如果上传失败则返回None。
        """
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            res = await client.request(
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
                cookies=self.cookies
            )
        if res.status_code == 200:
            logger.debug(f"上传图片响应: {res.text}")
            try:
                return eval(res.text[res.text.find('{'):res.text.rfind('}') + 1])
            except Exception as e:
                logger.error(f"解析上传响应失败: {e}")
                return None
        else:
            logger.error(f"上传图片失败: 状态码 {res.status_code}")
            return None

    async def publish_emotion(self, content: str, images: list[bytes] = []) -> str | None:
        """
        将说说内容和图片上传到QQ空间。图片会先上传并生成对应的pic_bo和richval值，然后与文本内容一起提交。

        Args:
            content (str): 说说的文本内容。
            images (list[bytes], 可选): 图片的二进制数据列表，默认为空列表。

        Returns:
            str: 成功发送后返回的说说ID（tid），如果发送失败则返回None。
        """
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
                upload_result = await self.upload_image(img)
                if upload_result:
                    picbo, richval = get_picbo_and_richval(upload_result)
                    if picbo and richval:
                        pic_bos.append(picbo)
                        richvals.append(richval)
            
            if pic_bos:
                post_data['pic_bo'] = ','.join(pic_bos)
                post_data['richtype'] = '1'
                post_data['richval'] = '\t'.join(richvals)

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.request(
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
                },
                cookies=self.cookies
            )
        if res.status_code == 200:
            if extract_code_json(res.text) != 0:
                logger.error(f"发表说说失败，响应内容: {res.text}")
                return None
            try:
                return res.json().get('tid')
            except Exception as e:
                logger.error(f"解析发表结果失败: {e}")
                return None
        else:
            logger.error(f"发表说说失败: 状态码 {res.status_code} 内容: {res.text}")
            return None

    async def like(self, fid: str, target_qq: str) -> bool:
        """
        点赞指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_qq (str): 目标QQ号。

        Returns:
            bool: 如果点赞成功返回True，否则返回False。
        """
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
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.request(
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
                cookies=self.cookies
            )
        if res.status_code == 200:
            if extract_code_json(res.text) != 0:
                logger.error("点赞失败" + res.text)
                return False
            return True
        else:
            logger.error("点赞失败: " + res.text)
            return False

    async def comment(self, fid: str, target_qq: str, content: str) -> bool:
        """
        评论指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_qq (str): 目标QQ号。
            content (str): 评论的文本内容。

        Returns:
            bool: 如果评论成功返回True，否则返回False。
        """
        uin = self.uin
        post_data = {
            "topicId": f'{target_qq}_{fid}__1',  # 说说ID
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
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.request(
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
                cookies=self.cookies
            )
        if res.status_code == 200:
            if extract_code_html(res.text) != 0:
                logger.error("评论失败" + res.text)
                return False
            return True
        else:
            logger.error("评论失败: " + res.text)
            return False

    async def reply(self, fid: str, target_qq: str, target_nickname: str, content: str, comment_tid: str) -> bool:
        """
        回复指定评论。
        TODO 采用子评论回复的方法不可用，暂时通过在评论内容中@目标昵称来实现。
        Args:
            fid (str): 说说的动态ID。
            target_qq (str): 目标QQ号。
            target_nickname (str): 目标QQ昵称。
            content (str): 回复的文本内容。
            comment_tid (str): 评论的唯一标识ID。

        Returns:
            bool: 如果回复成功返回True，否则返回False。
        """
        uin = self.uin
        post_data = {
            "topicId": f"{uin}_{fid}__1",  # 使用标准评论格式，而不是针对特定评论
            "uin": uin,
            "hostUin": uin,
            "content": f"回复@ {target_nickname} ：{content}",  # 内容中明确标示回复对象
            "format": "fs",
            "plat": "qzone",
            "source": "ic",
            "platformid": 52,
            "ref": "feeds",
            "richtype": "",
            "richval": "",
            "paramstr": f"@{target_nickname}",  # 确保触发@提醒机制
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.request(
                method="POST",
                url=self.REPLY_URL,
                params={
                    "g_tk": self.gtk2,
                },
                data=post_data,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
                },
                cookies=self.cookies
            )
        if res.status_code == 200:
            if extract_code_html(res.text) != 0:
                logger.error("回复失败" + res.text)
                return False
            return True
        else:
            logger.error(f"回复失败，错误码: {res.status_code}")
            return False

    async def get_list(self, target_qq: str, num: int, filter: bool = True) -> list[dict[str, Any]]:
        """
        获取指定QQ号的好友说说列表

        Args:
            target_qq (str): 目标QQ号。
            num (int): 要获取的说说数量。
            filter (bool, optional): 是否过滤掉已评论说说。默认为True。

        Returns:
            list[dict[str, Any]]: 包含说说信息的字典列表，每条字典包含说说的ID（tid）、发布时间（created_time）、内容（content）、图片描述（images）、视频url（videos）及转发内容（rt_con）。
            若发生错误，则返回包含错误信息的字典列表。如[{'error': '错误信息'}]。
        """
        logger.info(f'即将获取 {target_qq} 的说说列表...num={num} filter={filter}')
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.request(
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
                cookies=self.cookies
            )

        if res.status_code != 200:
            logger.error("访问失败: " + str(res.status_code))
            return []

        data = res.text
        if data.startswith('_preloadCallback(') and data.endswith(');'):
            # 1. 去掉res首尾的 _preloadCallback( 和 );
            json_str = data[len('_preloadCallback('):-2]
        else:
            json_str = data

        try:
            # 2. 解析JSON数据
            json_data = json.loads(json_str)
            logger.debug(f"原始说说数据: {json_data}")
            uin_nickname = json_data.get('logininfo').get('name')
            self.qq_nickname = uin_nickname

            if json_data.get('code') != 0:
                return [{"error": json_data.get('message')}]
            # 3. 提取说说内容
            feeds_list = []
            msglist = json_data.get("msglist") or []
            if not msglist:
                logger.warning("msglist为空或None，返回空的说说列表")
            for msg in msglist:
                # 已评论过的说说不再阅读
                is_comment = False
                if 'commentlist' in msg:
                    commentlist = msg.get("commentlist")
                    if isinstance(commentlist, list):  # 确保一定是可迭代的列表
                        for comment in commentlist:
                            qq_nickname = comment.get("name")
                            if uin_nickname == qq_nickname and target_qq != str(self.uin) and filter:  # 已评论且不是自己的说说且过滤已评论说说
                                logger.info('已评论过此说说，即将跳过')
                                is_comment = True
                                break

                if not is_comment or not filter:
                    # 存储结果
                    timestamp = msg.get("created_time", "")
                    if timestamp:
                        time_tuple = time.localtime(timestamp)
                        # 格式化为字符串（年-月-日 时:分:秒）
                        created_time = time.strftime('%Y-%m-%d %H:%M:%S', time_tuple)
                    else:
                        created_time = msg.get("createTime", "unknown")
                    tid = msg.get("tid", "")
                    content = msg.get("content", "")
                    logger.info(f"正在阅读说说内容: {content[:20]}...")
                    # 提取图片信息
                    images = []
                    # TODO 图片可读化
                    async def append_image_description(url: str):
                        if not url:
                            return
                        try:
                            image_base64 = await self.get_image_base64_by_url(url)
                            if not image_base64:
                                logger.warning(f"获取图片失败: {url}")
                                return
                            image_description = await image_manager.get_image_description(image_base64)
                            images.append(image_description)
                        except Exception as img_err:
                            logger.warning(f"获取图片描述失败: {img_err}")

                    for pic in (msg.get("pic") or []):
                        url = pic.get("url1") or pic.get("pic_id") or pic.get("smallurl")
                        await append_image_description(url)

                    # 读取视频封面（按图片处理）
                    for video in (msg.get("video") or []):
                        video_image_url = video.get("url1") or video.get("pic_url")
                        await append_image_description(video_image_url)

                    # 提取视频播放地址
                    videos = []
                    for video in (msg.get("video") or []):
                        url = video.get("url3")
                        if url:
                            videos.append(url)

                    # 提取转发内容
                    rt_con = ""
                    rt_data = msg.get("rt_con") or {}
                    if isinstance(rt_data, dict):
                        rt_con = rt_data.get("content", "")

                    # 提取评论
                    def _safe_int(value):
                        try:
                            return int(value)
                        except (TypeError, ValueError):
                            return None

                    comments = []
                    for comment in (msg.get("commentlist") or []):
                        comment_nickname = comment.get("name", "")
                        comment_content = comment.get("content", "")
                        comment_uin = comment.get("uin", "")
                        comment_tid_value = _safe_int(comment.get("tid"))
                        comment_time = comment.get("createTime", "") or comment.get("createTime2", "")

                        for sub_comment in (comment.get("list_3") or []):
                            sub_content = sub_comment.get("content", "")
                            sub_nickname = sub_comment.get("name", "")
                            sub_uin = sub_comment.get("uin", "")
                            sub_tid_value = _safe_int(sub_comment.get("tid"))
                            sub_time = sub_comment.get("createTime", "") or comment.get("createTime2", "")
                            sub_parent = comment_tid_value
                            comments.append(
                                {
                                    "content": sub_content,
                                    "qq_account": str(sub_uin),
                                    "nickname": sub_nickname,
                                    "comment_tid": sub_tid_value,
                                    "created_time": sub_time,
                                    "parent_tid": sub_parent,
                                }
                            )

                        comments.append(
                            {
                                "content": comment_content,
                                "qq_account": str(comment_uin),
                                "nickname": comment_nickname,
                                "comment_tid": comment_tid_value,
                                "created_time": comment_time,
                                "parent_tid": None,
                            }
                        )
                    # 存储信息
                    feeds_list.append({"target_qq": str(target_qq),
                                       "tid": str(tid),
                                       "created_time": created_time,
                                       "content": content,
                                       "images": images,
                                       "videos": videos,
                                       "rt_con": rt_con,
                                       "comments": comments})
            if len(feeds_list) == 0:
                return [{"error": '你已经看过最近的所有说说了，没有必要再看一遍'}]
            return feeds_list

        except Exception as e:
            logger.error(str(json_data))
            return [{"error": f'{e},你没有看到任何东西'}]

    async def get_qzone_list(self) -> list[dict[str, Any]]:
        """
        获取自己的QQ空间下，好友最新的几条说说，过滤自己的说说，不过滤已读说说
        Returns:
            list[dict[str, Any]]: 包含说说信息的字典列表，每条字典包含目标QQ号（target_qq）、说说ID(tid)、内容(content)、图片描述(images)、视频url(videos)、转发内容(rt_con)及评论内容(comments)。
        """
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.request(
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
                cookies=self.cookies
            )

        if res.status_code != 200:
            logger.error("访问失败: " + str(res.status_code))
            return []

        data = res.text
        #logger.debug(f"原始说说数据:{data}")
        if data.startswith('_Callback(') and data.endswith(');'):
            # 1. 去掉res首尾的 _Callback( 和 );
            data = data[len('_Callback('):-2]
        data = data.replace('undefined', 'null')
        try:
            # 2. 解析JSON数据
            data_dict = json5.loads(data)
            if isinstance(data_dict, dict):
                data_json = data_dict.get('data', {}).get('data', [])
            else:
                logger.error("无效的JSON数据")
            #logger.debug(f"初解析原始说说数据: {data}")
        except Exception as e:
            logger.error(f"解析错误: {e}")
            # 3. 提取说说内容
        try:
            feeds_list = []
            for feed in data_json:
                if not feed:  # 跳过None值
                    continue
                # 过滤广告类内容（appid=311）
                appid = str(feed.get('appid', ''))
                if appid != '311':
                    continue
                target_qq = feed.get('uin', '')
                tid = feed.get('key', '')
                if not target_qq or not tid:
                    logger.error(f"无效的说说数据: target_qq={target_qq}, tid={tid}")
                    continue
                # print(feed)

                html_content = feed.get('html', '')
                if not html_content:
                    logger.error(f"说说内容为空: UIN={target_qq}, TID={tid}")
                    continue

                soup = bs4.BeautifulSoup(html_content, 'html.parser')

                # 解析说说时间 - 相对时间，如'昨天17:50'
                created_time = feed.get('feedstime', '').strip()

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
                        if src and isinstance(src, str) and not src.startswith('http://qzonestyle.gtimg.cn'):  # 过滤表情图标
                            image_urls.append(src)
                # TODO 临时视频处理办法（视频缩略图）
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
                        if not image_base64:
                            logger.warning(f"获取图片失败: {url}")
                            continue
                        description = await image_manager.get_image_description(image_base64)
                        images.append(description)
                    except Exception as e:
                        logger.info(f'图片识别失败: {url} - {str(e)}')
                # 获取视频url
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

                        # 提取评论时间（直接使用相对时间字符串）
                        comment_time_span = item.select_one('span.state')
                        comment_time = comment_time_span.get_text(strip=True) if comment_time_span else ""

                        # 检查是否是回复
                        parent_tid = None
                        parent_div = item.find_parent('div', class_='mod-comments-sub')
                        if parent_div:
                            parent_li = parent_div.find_parent('li', class_='comments-item')
                            if parent_li:
                                parent_tid = parent_li.get('data-tid')

                        comments_list.append({
                            'qq_account': str(qq_account),
                            'nickname': nickname,
                            'comment_tid': int(comment_tid) if isinstance(comment_tid, str) and comment_tid.isdigit() else 0,
                            'content': content,
                            "created_time": comment_time,  # 直接使用相对时间字符串
                            'parent_tid': int(parent_tid) if isinstance(parent_tid, str) and parent_tid.isdigit() else None
                        })

                feeds_list.append({
                    'target_qq': str(target_qq),
                    'tid': str(tid),
                    "created_time": created_time,  # 相对时间字符串
                    'content': text,
                    'images': images,
                    'videos': videos,
                    'rt_con': rt_con,
                    'comments': comments_list,
                })

            logger.info(f"成功解析 {len(feeds_list)} 条最新说说")
            # 获取自己说说下的完整评论内容
            feeds_list = [item for item in feeds_list if item.get('target_qq') != str(self.uin)]  # 去除其中自己的说说
            return feeds_list
        except Exception as e:
            logger.error(f'解析说说错误：{str(e)}')
            return []

    async def get_send_history(self, num: int) -> str:
        """
        构建说说发送历史prompt。
        Args:
            num (int): 要获取的说说数量。
        Returns:
            str: 最近发过的说说内容。
        Raises:
            Exception: 如果请求失败或响应状态码不是200，将抛出异常。
        """
        feeds_list = await self.get_list(target_qq=str(self.uin), num=num)
        history = "==================="
        for feed in feeds_list:
            if not feed.get("rt_con", ""):
                history += f"""
时间：'{feed.get("created_time", "")}'。
说说内容：'{feed.get("content", "")}'
图片：'{feed.get("images", [])}'
===================
"""
            else:
                history += f"""
时间: '{feed.get("created_time", "")}'。
转发了一条说说，内容为: '{feed.get("rt_con", "")}'
图片: '{feed.get("images", [])}'
对该说说的评论为: '{feed.get("content", "")}'
===================
"""
        return history


def create_qzone_api() -> QzoneAPI | None:
    """
    使用存在的cookie文件创建QzoneAPI实例并返回。

    Returns:
        QzoneAPI | None: 如果成功加载cookie并创建QzoneAPI实例，则返回实例；否则返回None。

    说明:
        该函数尝试从指定路径加载与QQ账号关联的cookie文件。如果文件存在且加载成功，
        则使用加载的cookie创建并返回一个QzoneAPI实例；如果文件不存在或加载失败，
        则记录错误日志并返回None。
    """
    cookie_file = cookie_path
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
