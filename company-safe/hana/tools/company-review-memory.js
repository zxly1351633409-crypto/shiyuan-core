import { decideMemory } from "../shared/local-memory.js";

export const name = "review_shiyuan_company_memory";
export const description = "仅在用户明确要求后，确认或拒绝一条公司本地候选记忆";
export const parameters = {
  type: "object",
  properties: {
    memory_id: { type: "string" },
    decision: { type: "string", enum: ["confirm", "reject"] },
    note: { type: "string" },
  },
  required: ["memory_id", "decision"],
};

export async function execute(args) {
  return JSON.stringify(decideMemory(args.memory_id, args.decision, args.note || ""), null, 2);
}
