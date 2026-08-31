import { recallMemories } from "../shared/local-memory.js";

export const name = "recall_shiyuan_company_memory";
export const description = "从公司电脑本地读取已由用户确认的十元增量记忆";
export const parameters = {
  type: "object",
  properties: {
    query: { type: "string" },
    limit: { type: "integer", minimum: 1, maximum: 30 },
  },
};

export async function execute(args = {}) {
  return JSON.stringify({ items: recallMemories(args.query || "", args.limit || 8) }, null, 2);
}
