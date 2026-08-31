import { recordLocalWorkCheckpoint } from "../shared/local-memory.js";

export const name = "checkpoint_shiyuan_company_work";
export const description = "为公司本机进行中的工作保存用户可见检查点；不得写入私有推理、隐藏提示或工具原始输出";
export const parameters = {
  type: "object",
  properties: {
    workstream_id: { type: "string" },
    phase: { type: "string" },
    summary: { type: "string" },
    artifacts: { type: "array", items: { type: "string" } },
    evidence: { type: "array", items: { type: "string" } },
    next_actions: { type: "array", items: { type: "string" } },
  },
  required: ["workstream_id", "phase", "summary"],
};

export async function execute(args = {}) {
  const value = recordLocalWorkCheckpoint("hana", null, args.phase, args.summary, args);
  if (!value) throw new Error("未找到对应公司本地工作流。");
  return JSON.stringify(value, null, 2);
}
