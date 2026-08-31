import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const hanaHome = process.env.HANA_HOME || path.join(os.homedir(), ".hanako");
const coreConfig = JSON.parse(
  fs.readFileSync(path.join(hanaHome, "plugin-data", "shiyuan-hook", "config.json"), "utf8"),
);
const assistantName = process.env.SHIYUAN_EXPECTED_ASSISTANT || coreConfig.assistantName || "我的助手";
const pluginRoot = process.env.SHIYUAN_HANA_PLUGIN_ROOT || path.join(hanaHome, "plugins", "shiyuan-hook");
const pluginPath = path.join(pluginRoot, "extensions", "shiyuan-context.js");
const smokeSession = `shiyuan-smoke-hana-${Date.now()}`;

let beforeAgentStart = null;
let messageEnd = null;
const api = {
  on(name, handler) {
    if (name === "before_agent_start") beforeAgentStart = handler;
    if (name === "message_end") messageEnd = handler;
  },
};

const extension = await import(pathToFileURL(pluginPath).href);
extension.default(api);
if (!beforeAgentStart) throw new Error("before_agent_start handler was not registered");
if (!messageEnd) throw new Error("message_end handler was not registered");

const result = await beforeAgentStart({
  prompt: "个人助手内部测试：请实现检查点测试",
  systemPrompt: "BASE_SYSTEM_PROMPT",
  sessionId: smokeSession,
});

if (result?.systemPrompt !== undefined) {
  throw new Error("Hana Hook must not mutate the cache-bound system prompt");
}
if (result?.message?.customType !== "shiyuan-core-context" || result.message.display !== false) {
  throw new Error("Hana Hook did not return hidden turn context");
}
if (!result.message.content.includes("<shiyuan_core_context>")) {
  throw new Error("Hana Hook did not inject personal-assistant context");
}
if (!result.message.content.includes(`你当前是${assistantName}使用的 Hana 身体`)) {
  throw new Error("Hana body identity is missing");
}
if (!result.message.content.includes("用户画像（包含已确认事实与明确标注的待验证判断）")) {
  throw new Error("Hana Hook did not inject the Core user profile");
}
if (!result.message.content.includes(`${assistantName}开发状态`)) {
  throw new Error("Hana Hook did not inject the Core development status");
}
if (!result.message.content.includes(`${assistantName}知情范围与新鲜度`)) {
  throw new Error("Hana Hook did not receive the Core coverage ledger");
}
if (!result.message.content.includes("未接入来源和未授权公司原文仍未知")) {
  throw new Error("Hana Hook is missing the coverage boundary");
}
if (!result.message.content.includes("Core v0.3.8")) {
  throw new Error("Hana Hook received an empty or outdated development status");
}
if (!result.message.content.includes("模糊指代候选")) {
  throw new Error("Hana Hook did not receive vague-reference resolution context");
}
if (!result.message.content.includes("最近工作与跨身体活动")) {
  throw new Error("Hana Hook did not receive cross-body work context");
}
if (!result.message.content.includes(`🐳 ${assistantName}在线`)) {
  throw new Error("Hana Hook did not receive the Core response-style canary");
}
const intermediate = await messageEnd({
  message: {
    role: "assistant",
    content: [
      { type: "text", text: "正在核对测试证据。" },
      { type: "toolCall", name: "test", arguments: {} },
    ],
    stopReason: "toolUse",
  },
});
if (intermediate !== undefined) throw new Error("Hana Hook marked an intermediate tool-use message");
const finalized = await messageEnd({
  message: {
    role: "assistant",
    content: [{ type: "text", text: "<pulse>这是 UI 隐藏思考，不能进入历史。</mpulse>\n\n已完成检查点测试。" }],
    stopReason: "stop",
  },
});
if (!finalized?.message?.content?.[0]?.text?.endsWith(`🐳 ${assistantName}在线`)) {
  throw new Error("Hana Hook did not deterministically append the online marker");
}

const historyId = crypto.createHash("sha256").update(`hana\0${smokeSession}`).digest("hex");
const historyResponse = await fetch(
  `${coreConfig.coreUrl.replace(/\/$/, "")}/v1/history/sessions/${historyId}?include_messages=true`,
  { headers: { Authorization: `Bearer ${coreConfig.token}` } },
);
if (!historyResponse.ok) throw new Error(`Hana live history was not stored: ${historyResponse.status}`);
const history = await historyResponse.json();
if (history.messages.length < 2) throw new Error("Hana live history was incomplete");
const lastAssistant = history.messages.findLast((item) => item.role === "assistant");
if (lastAssistant?.content !== "已完成检查点测试。") throw new Error("Hana hidden <pulse> content leaked into history");
const workResponse = await fetch(
  `${coreConfig.coreUrl.replace(/\/$/, "")}/v1/work/recent?limit=12`,
  { headers: { Authorization: `Bearer ${coreConfig.token}` } },
);
const work = (await workResponse.json()).items.find((item) => item.owner_session_id === smokeSession);
if (!work?.latest_checkpoint?.summary?.includes("正在核对测试证据")) {
  throw new Error("Hana tool-use checkpoint was not stored");
}
if (work.effective_status !== "completed") throw new Error("Hana final receipt did not close the checkpointed work");
console.log(`Hana Hook OK: ${assistantName} context, marker, session identity and sanitized live history verified`);
