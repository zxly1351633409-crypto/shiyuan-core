# Codex Hook · 十元

- `codex_hook.py` 在会话开始和用户提交消息时读取十元上下文；Core 离线时 fail-open。
- `UserPromptSubmit` 每轮都会返回 Core 连接回执；旧会话需要在安装 Hook 后完整重启一次 Codex，才能让原有会话运行时加载新配置。
- 开启 `capture_messages` 时，用户输入进入家庭 Core 事件流；v0.2.9 会从明确陈述中自动形成去重 candidate，但不会自动确认。
- v0.3.0 在工作型用户请求时自动登记工作流，并在 `Stop` 使用用户可见最终回答生成最多 900 字的结构化回执；不解析 transcript，不保存完整回复或私有推理。
- v0.3.1 会按当前问题从分层旧历史中最多召回 4 个可见对话片段；完整旧历史由独立的 preview-first 导入器建立，不依赖不稳定的实时 Hook transcript。
- `live_activity_bridge.py` 从现有 Codex JSONL 的文件末尾开始增量尾读，只接收 `role=assistant + phase=commentary` 的用户可见阶段更新。`reasoning`、系统/开发消息、工具调用和工具输出会被硬排除；阶段更新通过当前 `session_id` 写成 Core 最新检查点，断网时进入既有离线补传箱。
- 桥接器由 `SessionStart` / `UserPromptSubmit` 自动尝试拉起，使用本机文件锁保持单实例；崩溃后下一次会话或用户提交会重新拉起。状态和错误日志只写 `%USERPROFILE%/.shiyuan/live-activity`，不写 Codex 源会话。
- 新会话会收到最近工作和最近任务报告；“继续刚才的”由 Core 恢复工作状态，任务卡编号不需要用户手工维护。
- `mcp_server.py` 给 Codex 提供长期记忆、旧历史、候选记忆、任务卡建立和回报工具。
- 客户端配置默认位于 `%USERPROFILE%/.shiyuan/client.json`。
