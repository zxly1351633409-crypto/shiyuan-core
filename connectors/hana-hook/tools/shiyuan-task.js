import { coreRequest } from "../shared/client.js";

export const name = "create_shiyuan_task";
export const description = "建立十元跨身体任务交接卡，可指定 Codex/Hana 或暂不指定身体";
export const parameters = {
  type: "object",
  properties: {
    title: { type: "string", description: "任务标题" },
    objective: { type: "string", description: "要达到的具体目标" },
    assigned_body: { type: "string", description: "codex/hana，留空表示任意身体" },
    project: { type: "string", description: "项目名称" },
    context: { type: "object", description: "交接上下文" },
    acceptance: { type: "array", items: { type: "string" }, description: "验收条件" },
  },
  required: ["title", "objective"],
};

export async function execute(input, toolCtx) {
  const result = await coreRequest(
    "/v1/tasks",
    {
      method: "POST",
      body: {
        ...input,
        assigned_body: input.assigned_body || null,
        project: input.project || null,
        context: input.context || {},
        acceptance: input.acceptance || [],
        source_body: "hana",
      },
    },
    toolCtx.dataDir,
  );
  return `十元任务卡已建立：${result.id}\n${result.title}\n状态：${result.status}`;
}
