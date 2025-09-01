# Maizone（麦麦空间） 插件

<u>制作者水平稀烂，任何bug或建议请联系qq：1523640161</u>

## 概述
Maizone（麦麦空间）插件v2.1.0，让你的麦麦发说说，读QQ空间，点赞评论！

## 功能
- **发说说**: 当用户说"说说"、"qq空间"、"动态"时麦麦会决定是否发说说和说说的主题

- **说说配图**：可以从已注册表情包中选择，或用ai生成配图，或随机选择

- **读说说**：当用户要求麦麦读说说、qq空间，并指定目标的qq昵称时，麦麦会获取该目标账号最近的动态并点赞评论

- **权限管理**：在config.toml中指定谁可以让麦麦读说说或发说说

- **自动阅读**：开启此功能让麦麦秒赞秒评新说说，回复评论（谨慎开启回复评论）

- **定时发送**：开启此功能让麦麦定时发说说

## 使用方法
### 安装插件

1. 下载或克隆本仓库（麦麦0.8版本可在release中下载旧版）

   ```
   git clone https://github.com/internetsb/Maizone.git
   ```

2. 将`Maizone\`文件夹放入`MaiBot\plugins`文件夹下

3. 安装相应依赖，示例：

   ```bash
   #在MaiBot文件夹下
   .\venv\Scripts\activate
   cd .\plugins\Maizone\
   pip install -i https://mirrors.aliyun.com/pypi/simple -r .\requirements.txt --upgrade
   #uv安装,在plugins\Maizone文件夹下
   uv pip install -r .\requirements.txt -i https://mirrors.aliyun.com/pypi/simple --upgrade
   ```

   <u>一键包用户可在启动时选择交互式安装pip模块，逐行安装MaiBot\plugins\Maizone\requirements.txt</u>中的依赖

4. 启动一次麦麦自动生成`config.toml`配置文件，成功生成配置文件即说明读取插件成功

### 设置Napcat http服务器端口以获取cookie

![](images/done_napcat1.png)

![](images/done_napcat2.png)

启用后在配置文件config.toml（若无则先启动一次）中填写上在napcat中设置的host（默认127.0.0.1）和端口号（默认9999）用于获取cookie

> [!IMPORTANT]
>
> Docker用户可将Napcat的HTTP Server的Host栏改为core（或0.0.0.0），插件的config.toml中的http_host栏改为napcat。经测试亦可正常使用

**备用方案操作步骤：用浏览器登录QQ空间并挂在后台**

插件*内置了两个方法用于自动获取cookie，如果还是不行但还是想用的话自己定时爬出cookie把目录自带的json填上吧（*

### 修改配置文件
请设置：
1. 是否启用插件及各种功能
2. 是否启用说说配图和ai生成配图（及相应的api_key）
3. 权限名单及类型

更多配置请看config.toml中的注释

### 快速开始
在config.toml中分别填写上send和read模块中的权限名单和类型

**发说说**：向麦麦发送命令：`/send_feed` 或说 “发条说说吧”/“发一条你今天心情的说说” 正常情况下，等待几秒后麦麦将发送一条相应主题的说说至QQ空间

**读说说**：对麦麦说：“读一下我的QQ空间”/“评价一下@xxx的空间”，麦麦将会对其近几条评论进行点赞评论

**自动看说说**：在config.toml中monitor开启，麦麦会自动阅读新说说并点赞、评论（不读自己的）

**定时发说说**：在config.toml中schedule开启，麦麦会定时发送说说

## 鸣谢

部分代码来自仓库：https://github.com/gfhdhytghd/qzone-toolkit

感谢[xc94188](https://github.com/xc94188)、[myxxr](https://github.com/myxxr)、[UnCLAS-Prommer](https://github.com/UnCLAS-Prommer)提供的功能改进

Maibot-Plus团队的魔改插件，神秘群号：169850076

