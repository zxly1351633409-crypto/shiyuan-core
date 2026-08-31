import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  appendVisibleMessage,
  catchUpLocalWork,
  compactLocalAssistantMessage,
  localMemoryStatus,
  proposeFromText,
  recordLocalWorkCheckpoint,
  recordLocalWorkReceipt,
  resolveLocalContext,
  startLocalWork,
} from "../shared/local-memory.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const policyPath = path.resolve(here, "..", "company-policy.md");
const memoryPath = path.resolve(here, "..", "confirmed-memory.md");

function contextMessage(content) {
  return {
    message: {
      customType: "shiyuan-company-safe-context",
      content,
      display: false,
      details: { source: "shiyuan-company-safe" },
    },
  };
}

export default function registerCompanyContext(pi) {
  let currentSession = null;
  pi.on("before_agent_start", async (event) => {
    const policy = fs.readFileSync(policyPath, "utf8");
    const memory = fs.readFileSync(memoryPath, "utf8");
    const prompt = String(event?.prompt || "");
    currentSession = event?.sessionId || event?.session_id || null;
    if (prompt) appendVisibleMessage("hana", currentSession, "user", prompt);
    if (prompt) proposeFromText(prompt, "hana");
    if (prompt) startLocalWork("hana", currentSession, prompt);
    const resolved = resolveLocalContext(prompt, "hana", 8);
    const confirmed = resolved.memories;
    const dynamicMemories = confirmed.length
      ? confirmed.map((item) => `- [${item.kind}] ${item.content}（公司本地已确认）`).join("\n")
      : "- 暂无公司本地已确认记忆";
    const pending = localMemoryStatus().candidate;
    const localContext = [
      "## 公司本地增量记忆",
      dynamicMemories,
      `- 待审核候选：${pending} 条。候选不能当作事实；只在用户明确要求后调用审核工具。`,
      "- 本地提取器只保存短候选结论，不保存本轮完整提示词。",
      "## 公司本地可见对话召回",
      ...(resolved.history.length
        ? resolved.history.slice(0, 4).map((item) => `- [${item.body}:${item.role} ${item.created_at}] ${String(item.content || "").slice(0, 600)}`)
        : ["- 暂无匹配的公司本地可见对话"]),
      "- 只归档用户与助手可见文本；不归档私有推理、隐藏系统提示或工具原始输出。",
      "## 公司本地最近工作",
      ...(resolved.recent_work.length
        ? resolved.recent_work.slice(0, 6).map((item) => {
            const checkpoint = item.latest_checkpoint?.summary ? `\n  最近检查点：${item.latest_checkpoint.summary}` : "";
            const receipt = item.latest_receipt?.result_summary ? `\n  最近结果：${item.latest_receipt.result_summary}` : "";
            return `- ${item.id} | ${item.effective_status || item.status} | 当前身体：${item.owner_body || "无"} | ${item.title}${checkpoint}${receipt}`;
          })
        : ["- 暂无公司本地结构化工作记录"]),
      "## 模糊指代候选",
      ...(resolved.candidate_interpretations.length
        ? resolved.candidate_interpretations.slice(0, 6).map((item) => `- [${item.type}] ${item.title}: ${String(item.summary || "").slice(0, 500)}`)
        : ["- 暂无候选"]),
      `- 是否模糊指代：${resolved.vague_reference}；先自行推断最可能对象，只有候选确实并列才追问。`,
      "## 自上次读取后的其他身体活动",
      ...(() => {
        const unread = catchUpLocalWork("hana", 20);
        return unread.length
          ? unread.map((item) => `- #${item.sequence} ${item.body} ${item.kind}：${item.summary}`)
          : ["- 暂无其他身体的新活动"];
      })(),
      "- 用户说“继续刚才的”时，优先恢复最近工作；活跃租约属于另一身体时先只读检查，明确接手后再修改。",
    ].join("\n");
    return contextMessage(`${policy.trimEnd()}\n\n${memory.trimEnd()}\n\n${localContext}\n`);
  });
  pi.on("message_end", async (event) => {
    const content = typeof event?.message?.content === "string"
      ? event.message.content
      : Array.isArray(event?.message?.content)
        ? event.message.content.filter((part) => part?.type === "text").map((part) => part.text || "").join("\n")
        : "";
    if (content) appendVisibleMessage("hana", currentSession, "assistant", content);
    if (event?.message?.stopReason === "toolUse") {
      if (content) recordLocalWorkCheckpoint("hana", currentSession, "tool-use", content);
      return undefined;
    }
    if (content) recordLocalWorkReceipt("hana", currentSession, compactLocalAssistantMessage(content));
    return undefined;
  });
}
