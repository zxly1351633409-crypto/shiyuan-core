import { baseContext, coreRequest, loadConfig } from "../shared/client.js";

export const name = "recall_shiyuan_memory";
export const description = "按主题从十元 Core 检索已确认的跨身体长期记忆；可选择同时检索旧可见对话";
export const parameters = {
  type: "object",
  properties: {
    query: { type: "string", description: "要回忆的主题或问题" },
    limit: { type: "integer", description: "返回数量，默认 8，最多 30" },
    includeHistory: { type: "boolean", description: "是否同时返回旧历史片段，默认 true" },
  },
  required: ["query"],
};

export async function execute(input, toolCtx) {
  const config = loadConfig(toolCtx.dataDir);
  const result = await coreRequest(
    "/v1/recall",
    {
      method: "POST",
      body: { ...baseContext(config), query: input.query, limit: input.limit || 8 },
    },
    toolCtx.dataDir,
  );
  if (input.includeHistory === false) return JSON.stringify(result.items, null, 2);
  const history = await coreRequest(
    "/v1/history/recall",
    {
      method: "POST",
      body: { ...baseContext(config), query: input.query, limit: Math.min(input.limit || 6, 20) },
    },
    toolCtx.dataDir,
  );
  return JSON.stringify({ confirmed_memory: result.items, history: history.items }, null, 2);
}
