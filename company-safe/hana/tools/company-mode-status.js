import os from "node:os";
import path from "node:path";

import { localMemoryStatus } from "../shared/local-memory.js";

export const name = "shiyuan_company_status";
export const description = "查看十元公司安全模式状态；该模式不连接家庭 Core";
export const parameters = { type: "object", properties: {} };

export async function execute() {
  const outbox = process.env.SHIYUAN_COMPANY_OUTBOX || path.join(os.homedir(), "Documents", "十元交接箱");
  return JSON.stringify(
    {
      mode: "company-safe-offline",
      core_connected: false,
      automatic_upload: false,
      memory_snapshot: "confirmed-full-2026-08-29",
      local_memory: localMemoryStatus(),
      visible_conversation_saved_locally: true,
      private_reasoning_saved: false,
      outbox,
    },
    null,
    2,
  );
}
