# 十元语义 Shadow 侧车

这是只读的本地派生索引，不是记忆事实源。它读取 Core SQLite 的 `history_chunks`，用 `intfloat/multilingual-e5-small` ONNX 编码，并通过 Docker 内网提供 `/v1/semantic/history`。

- 原始历史仍由 Core SQLite/JSONL 保存；索引损坏时可以重建。
- 服务不开放 NAS 端口，只允许 Compose 内部网络访问。
- `hybrid-shadow` 只记录语义结果和性能，用户可见答案仍使用关键词结果。
- 服务启动时先载入现有索引，后台每 60 秒只编码新增或内容变化的片段，不因一条新会话重建全库。
- 生产切换到 `hybrid` 前必须通过 150 题零回退、延迟/资源门槛，并再次取得用户确认。

模型目录需要包含 `model.onnx`、`tokenizer.json` 及模型配置文件。索引目录只保存 chunk ID、归一化向量、语料指纹和生成时间，不复制聊天正文。
