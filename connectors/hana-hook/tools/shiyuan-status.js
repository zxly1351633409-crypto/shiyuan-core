import { coreRequest } from "../shared/client.js";

export const name = "get_shiyuan_status";
export const description = "查看个人助手 Core 是否在线，以及记忆、事件和任务数量";
export const parameters = { type: "object", properties: {} };

export async function execute(_input, toolCtx) {
  const result = await coreRequest("/v1/status", {}, toolCtx.dataDir);
  return JSON.stringify(result, null, 2);
}
