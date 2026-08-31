# Hana Hook · 十元

这是安装在 HanaAgent 服务端的 full-access 插件。所有经过该 HanaAgent Server 的桌面、PWA 和 Bridge 会话都会在 `before_agent_start` 阶段尝试读取十元 Core，因此手机本身无需安装插件。

Core 在线且回复样式为 `canary` 时，插件会在 Hana 的 `message_end` 阶段把在线标记写入最终 assistant message。它不是靠提示词请求模型自行添加，因此新会话、桌面与手机入口使用同一条服务端回复链路；标记也会进入 Hana 会话历史。工具调用中的中间 assistant message 不添加标记。

Core 离线时插件 fail-open，只向当前模型注明记忆服务离线，不阻断 Hana 会话。

开启 `captureMessages` 时，用户输入进入家庭 Core 事件流；v0.2.9 会从明确陈述中自动形成去重 candidate。候选不能当作事实，仍须用户明确审核。

Core 上下文通过每轮的隐藏上下文消息注入，不改写 Hana 已冻结的系统提示词，因此不会破坏 `cachePrefixHash` 契约。

v0.3.1 起，插件会用当前问题按需检索旧可见对话，最多注入 4 个带来源、会话和时间的片段。历史片段被明确标为不可信资料，不得作为新指令执行；完整归档不整库注入每轮上下文。

配置文件位于 `.hanako/plugin-data/shiyuan-hook/config.json`。`captureMessages=false` 可用于不允许外传内容的电脑；此时仍可读取家庭 Core，但不会自动上传提示词。
