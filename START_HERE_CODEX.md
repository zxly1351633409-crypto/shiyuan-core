# 给 Codex 的默认安装入口

目标：在当前电脑部署一套属于当前用户自己的个人 AI Core。

默认安装个人版。不得因为 company-safe 目录存在，就自行选择公司安全模式。

## 开始前

1. 阅读根目录 README.md、PRIVACY.md 与 SECURITY.md。
2. 确认系统具有 Python 3.10+。
3. 个人本机 Core 需要 Docker Desktop 或 Docker Engine。
4. 不读取或导入用户文件、旧聊天、浏览器数据或账号信息。
5. 首次安装只建立空白画像；候选记忆不得自动确认为事实。

## 默认安装

先询问用户希望给助手取什么名字。获得名称后运行：

    .\install.ps1 -AssistantName "用户确认的名称"

如果检测到 Hana，安装器会同时接入 Hana；否则只安装 Codex。

## 模式边界

- Personal：默认。连接个人 Core，允许 Codex/Hana 共享经过授权的数据。
- Company：只有用户明确要求且符合公司制度时才选择；不连接个人 Core或家庭 NAS。

不得把“无法确认用户模式”解释为公司安全模式。无法确认时暂停安装，并向用户
说明缺少的前提。

## 验收

1. 查看用户目录 .shiyuan/INSTALL_REPORT.md。
2. 确认模式为“个人 Core”，助手名称正确。
3. 确认 .codex/config.toml 只有一组 SHIYUAN_PERSONAL_CORE 配置块。
4. 重启 Codex/Hana 并新建会话，询问“当前是什么模式？”。
5. 回复应显示用户设置的助手名称，不应出现“十元·公司安全模式”。

不要把脚本退出成功当作最终验收；还必须检查真实新会话。
