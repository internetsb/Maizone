# Maizone（麦麦空间） 插件

> [!WARNING]
>
> 该插件仍在开发阶段，开发者水平稀烂，可能出现大量bug，任何问题或建议请联系qq：1523640161



## 概述
Maizone（麦麦空间）插件v0.6，让你的麦麦发说说，读QQ空间，点赞评论！

## 功能
- **发说说**: 当用户说"说说"、"qq空间"、"动态"时麦麦会决定是否发说说

- **发说说指令**: 发送 `/send_feed [topic]` 命令让麦麦发主题为topic或随机的说说

  如发送`/send_feed 今天的天气` 让麦麦以今天的天气为主题发一条说说

  发送`/send_feed` 让麦麦以随机主题发一条说说

- **发说说AI配图**：（未做，麦麦会从已注册的表情包中选择配图（得有表情包））

- **读说说**：当用户要求麦麦读说说、qq空间，并指定目标的qq昵称时，麦麦会获取该目标账号最近的动态并点赞评论

- **权限管理**：在config.toml中指定谁可以让麦麦读说说或发说说

## 使用方法
### 安装插件

1. 下载或克隆本仓库

2. 将`Maizone/`文件夹放入`MaiBot/plugins`文件夹下

3. 安装相应依赖

   ```bash
   pip install -i https://mirrors.aliyun.com/pypi/simple -r .\requirements.txt --upgrade
   ```

   

4. 启动一次以生成`config.toml`配置文件

### 设置Napcat http服务器端口

![](napcat1.png)

![](napcat2.png)

启用后在配置文件config.toml（若无则先启动一次）中填写上在napcat中设置的端口号（默认9999）

### 快速开始
在config.toml中分别填写上send和read模块中的权限名单和类型

向麦麦发送命令：`/send_feed` 或说 “发条说说吧” 正常情况下，等待几秒后麦麦将发送一条说说至QQ空间
对麦麦说：“读一下我的QQ空间”，麦麦将会对近几条评论进行点赞评论

## 配置文件
插件会自动生成 `config.toml` 配置文件，用户可以修改：
- 黑白名单
- 每次读取的说说数量
- 发送说说和读取说说的相关配置

## 参考

部分代码来自仓库：https://github.com/gfhdhytghd/qzone-toolkit
