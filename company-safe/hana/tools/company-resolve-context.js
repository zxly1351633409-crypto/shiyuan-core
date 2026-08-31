import { resolveLocalContext } from "../shared/local-memory.js";

export const name = "resolve_shiyuan_company_context";
export const description = "在公司本机从已确认记忆、可见对话和工作检查点中恢复模糊上下文；不会联网";
export const parameters = {
  type: "object",
  properties: {
    query: { type: "string" },
    limit: { type: "integer", minimum: 1, maximum: 30 },
  },
  required: ["query"],
};

export async function execute(args = {}) {
  return JSON.stringify(resolveLocalContext(args.query || "", "hana", args.limit || 8), null, 2);
}
