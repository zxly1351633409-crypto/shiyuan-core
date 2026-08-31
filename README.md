# Shiyuan Core（十元 Core）

Shiyuan Core 是一个本地优先、与模型解耦的个人 AI 状态层。Codex、
HanaAgent 或其他 Agent 可以作为不同“身体”，共同读取经过审核的记忆、
任务状态和跨会话工作回执。

它不是模型，也不会训练模型。它负责让可替换的 Agent 共享连续状态，同时把
候选记忆、已确认事实、历史资料和正在进行的工作分开管理。

## 主要能力

- 候选记忆 → 人工确认 → 长期记忆，不把一次对话自动固化为事实。
- SQLite/FTS5 历史检索，可选本地语义检索侧车。
- Codex 生命周期 Hook 与 MCP Server。
- HanaAgent 服务端 Hook，桌面、PWA 和 Bridge 共用。
- 跨身体任务、检查点、精简结果回执和离线补传。
- Obsidian 友好的 Markdown Vault。
- 独立的公司本地安全模式，不默认连接家庭或公网服务。
- 只读记忆管理页面。

## 隐私说明

本公开版本由一份全新的无历史副本生成，默认画像和已确认记忆均为空。
仓库不包含真实聊天、私人文件、数据库、设备路径、内网地址、账号或密钥。
详见 [PRIVACY.md](PRIVACY.md) 与 [SECURITY.md](SECURITY.md)。

## 5 分钟启动

需要 Docker Desktop 或 Docker Engine。

```powershell
git clone https://github.com/YOUR_ACCOUNT/shiyuan-core.git
cd shiyuan-core
Copy-Item core.env.example core.env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

把最后一条命令生成的随机值写入 `core.env` 的
`SHIYUAN_CORE_TOKEN`，然后启动：

```powershell
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:8710/health
```

数据保存在 Docker 命名卷 `shiyuan-data`，不会写入 Git 仓库。

## 首次个性化

首次启动会在 Vault 中创建三个空白/模板文件：

- `00 Identity/十元.md`：助手身份与安全边界；
- `00 Identity/用户画像.md`：只写经过确认的事实；
- `90 System/开发状态.md`：当前项目状态与接续说明。

不要一次性导入整台电脑。建议先写一小份确认画像，再逐步接入经过选择的
会话或资料源。

## 安装身体连接器

### Codex

`connectors/codex-hook` 提供生命周期 Hook 与 MCP Server。客户端配置默认放在
`%USERPROFILE%\.shiyuan\client.json`。完整字段见该目录 README。

### HanaAgent

把 `connectors/hana-hook` 安装为 HanaAgent 服务端插件。配置默认放在
`.hanako/plugin-data/shiyuan-hook/config.json`。若手机连接同一个 Hana Server，
手机无需单独安装插件。

可以先生成两个客户端配置：

```powershell
$env:SHIYUAN_INSTALL_URL = "http://127.0.0.1:8710"
$env:SHIYUAN_INSTALL_TOKEN = "与-core.env-相同的随机值"
python scripts/install_client_configs.py
```

脚本只写本机配置，不会从远程主机读取 Token。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:SHIYUAN_CORE_TOKEN = "a-local-test-token-with-at-least-32-characters"
.\.venv\Scripts\python.exe -m pytest -q
```

## 可选语义检索

默认使用 SQLite FTS5。语义侧车需要本地 ONNX 模型目录；模型权重不包含在
仓库中。准备好模型后，可使用：

```powershell
docker compose --profile semantic up -d --build
```

在验证语义结果不会让关键词已通过项回退前，建议先使用
`SHIYUAN_HISTORY_RETRIEVAL_MODE=hybrid-shadow`。

## 重要限制

- Core 不会自行升级、替用户确认记忆或自动获得电脑文件权限。
- 连接器只能看到其明确接入的数据源。
- 精简工作回执不是完整历史；两者用途不同。
- 把服务暴露到公网前必须自行配置 TLS、身份认证和网络边界。

## License

MIT
