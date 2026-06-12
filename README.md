# Maizone（麦麦空间） 插件

> v1.0.0以下的MaiBot请在release中下载插件

*制作者水平有限，任何漏洞、疑问或建议欢迎提出issue或联系qq：1523640161*

## 概述

Maizone（麦麦空间）插件v3.0.1测试版，让你的麦麦发说说，读QQ空间，点赞评论！

## 功能

- **发说说**: 当用户说"说说"、"qq空间"、"动态"时麦麦会决定是否发说说和说说的主题
- **说说配图**：可以从已注册表情包中选择，或用ai生成配图，或随机选择
- **读说说**：当用户要求麦麦读说说、qq空间，并指定目标的qq昵称时，麦麦会获取该目标账号最近的动态并点赞评论
- **权限管理**：在config.toml中指定谁可以让麦麦读说说或发说说
- **自动阅读**：开启此功能让麦麦秒赞秒评新说说，回复评论
- **定时发送**：开启此功能让麦麦定时发说说
- **插件API**：其它插件可基于本插件提供的API发送文本和图片至QQ空间

## 快速开始

### 一、安装插件

1. 安装并启用[Napcat_Adapter](https://docs.mai-mai.org/manual/adapters/napcat.html)插件，并进行相应配置
2. 从插件商店下载本插件、或克隆本仓库至 `MaiBot\plugins` 文件夹下
3. 命令运行较为耗时，建议修改主程序的插件运行超时阈值

```bash
git clone https://github.com/internetsb/Maizone.git
```

### 二、发送说说

使用命令：`/sendfeed <说说主题>` 或 自然语言 （如："发一条今天天气的说说吧"）

部分配置说明：

- `history_number`：生成新说说时回顾的旧说说数，可用于减少重复，会增加token消耗
- `enable_image`：是否在发说说时附带配图
- `image_mode`：决定配图为表情包、或是AI生成（需进一步配置）、或是二者混合随机

### 三、阅读说说

使用命令：`/readfeed <qq昵称>` 或 自然语言（如："麦麦读下我的qq空间"）

正常情况下，麦麦会获取该目标账号最近的动态并点赞评论

### 四、自动发送

默认关闭

该功能继承发送说说配置的回顾历史数、图片、提示词等配置

部分配置说明：

- `enable_auto_send`：开启此功能，麦麦会定时发送说说
- `daily_probability`：今天要发说说的概率
- `schedule`：发送说说的时间表
- `fluctuation`：在时间表基础上的上下浮动分钟数
- `random_topic`：开启后让LLM自行决定主题，关闭后从固定列表中选择

### 五、自动阅读

默认开启

该功能继承阅读说说配置的点赞评论概率、提示词等配置

部分配置说明：

- `enable_auto_read`：开启此功能，麦麦会定时读取说说并点赞评论
- `interval`：每隔多少分钟读取一次
- `silent_duration`：静默时间，在该时间段内不进行阅读

### 六、自动回复评论

默认开启

该功能继承自动阅读配置的开启、阅读间隔、静默时间等配置

部分配置说明：

- `enable_auto_reply`：开启此功能，麦麦会定时回复自己发的说说下的评论
- `reply_number`：对自己发的最新的多少条说说下的评论进行回复
- `reply_probability`：回复概率

### 七、AI配图

openai格式生图，默认配置为火山引擎seedream

该功能考虑后续使用其余插件的API

部分配置说明：

- `enable_reference`：是否使用参考图，即图生图模式，可以使用Bot人设图或头像等
- `reference`：参考图片URL（http/https开头），或本地图片路径
- `prompt`：提示词，LLM根据待发送说说文本生成配图的生成提示词，再生成图片
- `ref_prompt`：启用参考图时附加在prompt后的提示词

### 八、权限配置

- `blacklist`：黑名单，在此名单中的qq将无法使用此功能
- `whitelist`：白名单，在此名单中的qq将可以使用此功能

### 九、插件API

#### 在 `_manifest.json` 中添加插件依赖

```json
{
  "type": "plugin",
  "id": "internetsb.maizone",
  "version_spec": ">=3.0.0"
}
```

#### 发送说说API：

发送文本和图片到QQ空间

- **api_id**：`internetsb.maizone.send_feed_api`
- **参数**：
  - `message`: str = （可选）文本内容
  - `images`：List[bytes] = （可选）图片列表
- **返回示例**：
  - `result`：bool = 发送是否成功
  - `message`: str = "发送说说失败" 或 "说说发送成功，动态ID：{fid}"
- **调用示例**：

```python
api_id = "internetsb.maizone.send_feed_api"
message = "Hello World 插件已加载！🎉"
image_path = Path(__file__).parent / "reference.jpg"
image_bytes = image_path.read_bytes()
params = {"message": message, "images": [image_bytes]}
result = await self.ctx.api.call(api_id, **params)
self.ctx.logger.info(f"API 调用结果:{result}")
```

#### 阅读说说API：

获取指定用户的最新动态列表

- **api_id**：`internetsb.maizone.get_feeds_list_api`
- **参数**：
  - `target_qq`：str = 目标qq
  - `num`: int = （可选）获取动态数量，默认为5
  - `filter`: Bool = （可选）是否过滤掉已评论过的动态，默认为False
- **返回示例**：
  - `result`：bool = 获取是否成功
  - `message`: str = 错误信息或是"成功获取{len(feeds_list)}条说说"
  - `data`: List[Dict] = 成功获取的动态列表
- **调用示例**：

```python
api_id = "internetsb.maizone.get_feeds_list_api"
target_user_id = "1523640161"
params = {"target_qq": target_user_id, "num": 5, "filter": False}
result = await self.ctx.api.call(api_id, **params)
self.ctx.logger.info(f"API 调用结果:{result}")
```

## 常见问题

- **Q：所有功能都不可用/cookie获取失败/提示"请先登录"**

  **A：请检查插件目录下是否生成cookie，cookie中uin是否正确对应qq号，若错误请尝试使用以下备选方案**

  1. **备选napcat连接**

     在Napcat中添加一个http服务器，

     ```
     Host = "0.0.0.0"
     Port = "9999" # (若日志显示监听9999端口失败，请将9999改为其他端口)
     Token = "自己设置一个密钥"
     ```

     在插件基础配置中填写

     ```
     http_host = "127.0.0.1" # 服务的地址，docker请尝试填写"napcat"
     http_port = "9999" # 刚才填写的Port
     napcat_token = "自己设置的密钥"
     ```
  2. **扫码登录**

     插件目录下会生成qrcode.png，打开图片，使用手机扫码登录QQ空间，有效期约一天
- **Q：No module named 'bs4'/No module named 'json5'**

  **A：请检查MaiBot版本，应该为1.0.0以上，旧版本请查看旧版文档**
- **Q：我发了一条说说，但bot没有回复**

  **A：bot无法阅读相册上传、小程序分享、过早的说说，且某些说说（比如新加的好友）需要多次才能读到，具体读取情况以日志为准**
- **Q：listen EACCES: permission denied 127.0.0.1:9999**

  **A：可能为端口9999被占用，可选择更换为其它端口，并修改相应配置**
- **Q：如何更改使用的模型配置**

  **A：请查看MaiBot/config/model_config.toml，默认使用replyer**

  ```toml
  [model_task_config.replyer] # 首要回复模型，还用于表达器和表达方式学习
  model_list = ["xxxxxx"]
  temperature = xxx
  max_tokens = xxx
  ```

  **可更换为配置的utils、utils_small、tool_use等模型，模型列表配置参看MaiBot文档**
- **其余问题请联系作者修复或解决（部分好友请求可能被过滤导致回复不及时，请见谅）**

## 已知问题

- 无法阅读图片
- 可能出现对同一条说说重复评论，或对同一条评论重复回复的问题，欢迎提供出现问题时的日志
- 当前解析说说附带的视频时仅解析了视频封面
- 自动回复评论依赖 QQ 空间 Web 私有接口，接口变更可能导致回复失败

## 鸣谢

- [MaiBot](https://github.com/MaiM-with-u/MaiBot)
- 部分代码来自仓库：[qzone-toolkit](https://github.com/gfhdhytghd/qzone-toolkit)
- 感谢[xc94188](https://github.com/xc94188)、[myxxr](https://github.com/myxxr)、[UnCLAS-Prommer](https://github.com/UnCLAS-Prommer)、[XXXxx7258](https://github.com/XXXxx7258)、[heitiehu-beep](https://github.com/heitiehu-beep)提供的功能改进
- 魔改版麦麦，集成了魔改版插件[MoFox_Bot](https://github.com/MoFox-Studio/MoFox_Bot)
