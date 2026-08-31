import { coreRequest } from "../shared/client.js";

export const name = "propose_shiyuan_memory";
export const description = "向个人助手 Core 提交一条候选记忆；候选不会自动成为事实，需用户确认";
export const parameters = {
  type: "object",
  properties: {
    kind: { type: "string", description: "fact/preference/decision/episode/procedure" },
    content: { type: "string", description: "简洁、可独立理解的记忆内容" },
    scope: { type: "string", description: "personal/project/team/system" },
    source: { type: "string", description: "来源会话、文件或用户明确陈述" },
    confidence: { type: "number", description: "0 到 1" },
    sensitivity: { type: "string", description: "normal/private/restricted" },
    evidence: { type: "string", description: "证据或为什么值得保存" },
  },
  required: ["content", "source"],
};

export async function execute(input, toolCtx) {
  const result = await coreRequest(
    "/v1/memory/proposals",
    {
      method: "POST",
      body: {
        kind: input.kind || "fact",
        content: input.content,
        scope: input.scope || "personal",
        source: `hana:${input.source}`,
        confidence: input.confidence ?? 0.7,
        sensitivity: input.sensitivity || "normal",
        evidence: input.evidence || null,
      },
    },
    toolCtx.dataDir,
  );
  return `已提交个人助手候选记忆 ${result.id}，当前状态：${result.status}（尚未自动确认）`;
}
