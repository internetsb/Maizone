# Maizone（麦麦空间） 插件

<u>任何bug或建议请联系qq：1523640161</u>

## 概述
Maizone（麦麦空间）插件v1.0.0，让你的麦麦发说说，读QQ空间，点赞评论！

## 功能
- **发说说**: 当用户说"说说"、"qq空间"、"动态"时麦麦会决定是否发说说和说说的主题

- **说说配图**：可以从已注册表情包中选择，或用ai生成配图

- **读说说**：当用户要求麦麦读说说、qq空间，并指定目标的qq昵称时，麦麦会获取该目标账号最近的动态并点赞评论

- **权限管理**：在config.toml中指定谁可以让麦麦读说说或发说说

- **自动点赞**：开启监控让麦麦秒赞秒评新说说

## 使用方法
### 安装插件

1. 下载或克隆本仓库

2. 将`Maizone\`文件夹放入`MaiBot\plugins`文件夹下

3. 安装相应依赖，示例：

   ```bash
   #在MaiBot文件夹下
   .\venv\Scripts\activate
   cd .\plugins\Maizone\
   pip install -i https://mirrors.aliyun.com/pypi/simple -r .\requirements.txt --upgrade
   ```

   

4. 启动一次以生成`config.toml`配置文件

### 设置Napcat http服务器端口

![](napcat1.png)

![](napcat2.png)

启用后在配置文件config.toml（若无则先启动一次）中填写上在napcat中设置的端口号（默认9999）

### 修改配置文件
请设置：
1. read和send动作的权限名单和类型
2. 是否启用说说配图和ai生成配图（及相应的apikey）

更多配置请看config.toml中的注释

### 快速开始
在config.toml中分别填写上send和read模块中的权限名单和类型

**发说说**：向麦麦发送命令：`/send_feed` 或说 “发条说说吧”/“发一条你今天心情的说说” 正常情况下，等待几秒后麦麦将发送一条相应主题的说说至QQ空间

**读说说**：对麦麦说：“读一下我的QQ空间”/“评价一下@xxx的空间”，麦麦将会对其近几条评论进行点赞评论

**自动看说说**：在config.toml中开启，麦麦会自动阅读新说说并点赞评论

## 参考

部分代码来自仓库：https://github.com/gfhdhytghd/qzone-toolkit
