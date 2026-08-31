import { coreRequest } from "../shared/client.js";

export const name = "review_shiyuan_memory";
export const description = "列出个人助手候选记忆，或在用户明确要求后确认/拒绝指定候选；不得替用户自行确认";
export const parameters = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["list", "confirm", "reject"], description: "审核动作" },
    memory_id: { type: "string", description: "confirm/reject 时必填" },
    note: { type: "string", description: "用户确认依据或拒绝原因" },
    limit: { type: "integer", description: "list 时的返回数量，默认 30" },
  },
  required: ["action"],
};

export async function execute(input, toolCtx) {
  if (input.action === "list") {
    const limit = Math.max(1, Math.min(input.limit || 30, 100));
    const result = await coreRequest(`/v1/memory/proposals?limit=${limit}`, {}, toolCtx.dataDir);
    return JSON.stringify(result.items, null, 2);
  }
  if (!input.memory_id) return "confirm/reject 必须提供 memory_id";
  const result = await coreRequest(
    `/v1/memory/${encodeURIComponent(input.memory_id)}/${input.action}`,
    { method: "POST", body: { note: input.note || null } },
    toolCtx.dataDir,
  );
  const memory = result.memory || result;
  return `候选记忆 ${memory.id} 已${input.action === "confirm" ? "确认" : "拒绝"}，状态：${memory.status}`;
}
