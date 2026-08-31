import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const name = "export_shiyuan_company_handoff";
export const description = "仅在用户明确要求时，把已脱敏的公司工作摘要保存为等待人工审核的本地交接卡；不会自动发送";
export const parameters = {
  type: "object",
  properties: {
    title: { type: "string", description: "不含未公开项目名的概括标题" },
    summary: { type: "string", description: "允许带离公司的抽象摘要，不得粘贴原文" },
    decisions: { type: "array", items: { type: "string" } },
    next_actions: { type: "array", items: { type: "string" } },
    sensitivity: { type: "string", enum: ["safe_summary", "review_required", "do_not_export"] },
    contains_company_confidential: { type: "boolean" },
    source_body: { type: "string" },
  },
  required: ["title", "summary", "sensitivity", "contains_company_confidential"],
};

const secretPattern = /(api[_ -]?key|access[_ -]?token|password|passwd|secret|bearer\s+[a-z0-9._-]+|begin [a-z ]*private key)/i;

function strings(value) {
  return Array.isArray(value)
    ? value.slice(0, 20).map((item) => String(item).trim().slice(0, 2000)).filter(Boolean)
    : [];
}

function safeName(value) {
  return String(value).replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/[ ._]+$/g, "").slice(0, 80) || "未命名交接";
}

export async function execute(input) {
  const title = String(input.title || "").trim().slice(0, 300);
  const summary = String(input.summary || "").trim().slice(0, 12000);
  if (!title || !summary) throw new Error("title 和 summary 必填");
  const decisions = strings(input.decisions);
  const nextActions = strings(input.next_actions);
  const allowed = new Set(["safe_summary", "review_required", "do_not_export"]);
  const sensitivity = allowed.has(input.sensitivity) ? input.sensitivity : "review_required";
  const containsConfidential = Boolean(input.contains_company_confidential);
  const secretLike = secretPattern.test([title, summary, ...decisions, ...nextActions].join("\n"));
  const exportStatus = sensitivity === "do_not_export" || containsConfidential || secretLike
    ? "local_only"
    : "awaiting_human_review";
  const id = crypto.randomUUID();
  const card = {
    schema: "shiyuan.company-handoff.v1",
    id,
    created_at: new Date().toISOString(),
    source_body: String(input.source_body || "hana-company").slice(0, 64),
    title,
    summary,
    decisions,
    next_actions: nextActions,
    sensitivity,
    contains_company_confidential: containsConfidential,
    secret_like_pattern_detected: secretLike,
    export_status: exportStatus,
    notice: "本地生成不等于获准外传；发送前必须由用户按公司制度人工审核。",
  };
  const root = process.env.SHIYUAN_COMPANY_OUTBOX || path.join(os.homedir(), "Documents", "十元交接箱");
  const target = path.join(root, exportStatus === "local_only" ? "仅限公司本地" : "待人工审核");
  fs.mkdirSync(target, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  const stem = `${stamp}-${safeName(title)}-${id.slice(0, 8)}`;
  const jsonPath = path.join(target, `${stem}.json`);
  const mdPath = path.join(target, `${stem}.md`);
  const txtPath = path.join(target, `${stem}.txt`);
  fs.writeFileSync(jsonPath, `${JSON.stringify(card, null, 2)}\n`, "utf8");
  const bullets = (values) => values.length ? values.map((item) => `- ${item}`).join("\n") : "- 无";
  fs.writeFileSync(
    mdPath,
    `# ${title}\n\n> 状态：${exportStatus}  \n> 敏感级别：${sensitivity}  \n> ID：${id}\n\n## 摘要\n\n${summary}\n\n## 已做决策\n\n${bullets(decisions)}\n\n## 下一步\n\n${bullets(nextActions)}\n\n## 人工审核提醒\n\n本地生成不等于获准外传。请删除原文、附件、内部路径、未公开名称、人员信息和其他受限内容后再发送。\n`,
    "utf8",
  );
  fs.writeFileSync(
    txtPath,
    `[十元公司交接 v1]\nID：${id}\n标题：${title}\n摘要：${summary}\n决策：${decisions.join("；") || "无"}\n下一步：${nextActions.join("；") || "无"}\n状态：${exportStatus}\n[/十元公司交接]\n`,
    "utf8",
  );
  return `交接卡已保存到公司本地，状态：${exportStatus}。\n${jsonPath}\n${mdPath}\n${txtPath}\n本地生成不等于获准外传；请先人工审核。`;
}
