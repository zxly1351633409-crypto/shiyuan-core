# Shiyuan Core：培养你自己的个人 AI 助手

Shiyuan Core 是一个本地优先、与模型解耦的个人 AI 状态层。它是公开框架，
不是名为“十元”的成品人格。Codex、HanaAgent 或其他 Agent 可以作为不同
“身体”，共同读取经过审核的记忆、任务状态和跨会话工作回执。

它不是模型，也不会训练模型。它负责让可替换的 Agent 共享连续状态，同时把
候选记忆、已确认事实、历史资料和正在进行的工作分开管理。

## 主要能力

- 候选记忆经过人工确认后才成为长期事实。
- SQLite/FTS5 历史检索，可选本地语义检索侧车。
- Codex 生命周期 Hook 与 MCP Server。
- HanaAgent 服务端 Hook，桌面、PWA 和 Bridge 共用。
- 跨身体任务、检查点、工作回执和离线补传。
- Obsidian 友好的 Markdown Vault。
- 可选公司安全模式；只有显式选择 Mode Company 才会安装。

## 隐私说明

本公开版本由一份全新的无历史副本生成，默认画像和已确认记忆均为空。
仓库不包含真实聊天、私人文件、数据库、设备路径、内网地址、账号或密钥。
详见 PRIVACY.md 与 SECURITY.md。

## 5 分钟启动：默认个人版

需要 Python 3.10+ 与 Docker Desktop 或 Docker Engine。

    git clone https://github.com/zxly1351633409-crypto/shiyuan-core.git
    cd shiyuan-core
    .\install.ps1 -AssistantName "给你的助手起一个名字"

安装器会：

- 生成本机专用的随机 Core Token，但不在报告中显示；
- 启动个人 Core，而不是公司安全模式；
- 安装 Codex Hook 与 MCP；
- 检测到 Hana 时，同时安装 Hana 服务端插件；
- 写入可恢复的安装报告和 Codex 配置备份。

数据保存在 Docker 命名卷 shiyuan-data，不会写入 Git 仓库。

交给 Codex 自行安装时，让它先阅读仓库根目录 START_HERE_CODEX.md。
不要把 company-safe/START_HERE_CODEX.md 当作普通个人版入口。

## 首次个性化

首次启动会创建：

- 00 Identity/<你的助手名称>.md：助手身份与安全边界；
- 00 Identity/用户画像.md：只写经过确认的事实；
- 90 System/开发状态.md：当前项目状态与接续说明。

不要一次性导入整台电脑。建议先写一小份确认画像，再逐步接入经过选择的
会话或资料源。培养计划见 docs/CULTIVATION_GUIDE.md。

## 身体连接器

根目录 install.ps1 会安装连接器。手工部署时：

- connectors/codex-hook 提供 Codex Hook 与 MCP Server；
- connectors/hana-hook 是 HanaAgent 服务端插件；
- 手机连接同一个 Hana Server 时无需单独安装插件。

## 公司安全模式是可选项

公司安全模式只用于组织制度不允许连接个人 Core、家庭 NAS 或公网服务的电脑。
它不是个人版的降级默认值，也不是当前助手的完整镜像。

    .\install.ps1 -Mode Company

如果旧电脑误装了公司版，请阅读
docs/MIGRATE_FROM_COMPANY_SAFE.md。旧数据会先归档，不会直接删除。

## 本地开发

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    $env:SHIYUAN_CORE_TOKEN = "a-local-test-token-with-at-least-32-characters"
    $env:SHIYUAN_ASSISTANT_NAME = "测试助手"
    .\.venv\Scripts\python.exe -m pytest -q

## 可选语义检索

默认使用 SQLite FTS5。准备本地 ONNX 模型后可启用 semantic profile。
在验证语义结果不会让关键词已通过项回退前，建议先使用 hybrid-shadow。

## 重要限制

- Core 不会自行升级、替用户确认记忆或自动获得电脑文件权限。
- 连接器只能看到其明确接入的数据源。
- 精简工作回执不是完整历史；两者用途不同。
- 把服务暴露到公网前必须自行配置 TLS、身份认证和网络边界。

## License

MIT
