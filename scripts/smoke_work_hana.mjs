import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const plugin = process.env.SHIYUAN_HANA_SOURCE;
if (!plugin) throw new Error("SHIYUAN_HANA_SOURCE is required");
let beforeAgentStart = null;
let messageEnd = null;
const api = {
  on(name, handler) {
    if (name === "before_agent_start") beforeAgentStart = handler;
    if (name === "message_end") messageEnd = handler;
  },
};
const extension = await import(pathToFileURL(path.resolve(plugin)).href);
extension.default(api);
if (!beforeAgentStart || !messageEnd) throw new Error("Hana work hooks were not registered");

const viewed = await beforeAgentStart({
  prompt: "Codex 刚才做了什么？",
  sessionId: "hana-e2e-view",
  turnId: "hana-view-turn",
});
if (!viewed?.message?.content?.includes("Codex 接口已经完成")) {
  throw new Error("Hana did not receive Codex work receipt");
}

const continued = await beforeAgentStart({
  prompt: "请接手并继续刚才的",
  sessionId: "hana-e2e-work",
  turnId: "hana-work-turn",
});
if (!continued?.message?.content?.includes("最近工作与跨身体活动")) {
  throw new Error("Hana continuation context is missing");
}
await messageEnd({
  message: {
    role: "assistant",
    content: [{ type: "text", text: "已完成 Hana 反向接续。\n\n验证：Hana Hook 测试通过。" }],
    stopReason: "stop",
  },
});
process.stdout.write(JSON.stringify({ ok: true }));
