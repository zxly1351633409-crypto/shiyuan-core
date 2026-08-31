import { listMemories } from "../shared/local-memory.js";

export const name = "list_shiyuan_company_memory_proposals";
export const description = "列出只保存在公司电脑本地的十元候选记忆；候选尚未成为事实";
export const parameters = {
  type: "object",
  properties: { limit: { type: "integer", minimum: 1, maximum: 200 } },
};

export async function execute(args = {}) {
  return JSON.stringify({ items: listMemories("candidate", args.limit || 100) }, null, 2);
}
