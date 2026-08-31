const MARKERS = ["🐳 十元在线", "🐳 公司十元本地模式"];
const EVIDENCE = ["通过", "passed", "healthy", "测试", "验证", "sha-256", "commit", "提交", "校验"];
const NEXT = ["下一步", "未完成", "尚未", "还需", "仍需", "待处理", "遗留", "需要重启"];
const DECISION = ["决定", "采用", "选择", "保持", "不再", "改为", "范围"];

function matchedLines(lines, cues, limit = 5) {
  const values = [];
  for (const line of lines) {
    const lower = line.toLowerCase();
    if (cues.some((cue) => lower.includes(cue))) {
      const value = line.replace(/^[\s#>*+\-\d.）)✅⚠️🔍]+/u, "").trim().slice(0, 500);
      if (value && !values.includes(value)) values.push(value);
    }
    if (values.length >= limit) break;
  }
  return values;
}

export function messageText(message) {
  if (typeof message?.content === "string") {
    return message.content.replace(/<pulse\b[^>]*>[\s\S]*?<\/m?pulse>\s*/giu, "").trim();
  }
  if (!Array.isArray(message?.content)) return "";
  return message.content
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n")
    .replace(/<pulse\b[^>]*>[\s\S]*?<\/m?pulse>\s*/giu, "")
    .trim();
}

export function compactAssistantMessage(message) {
  let text = String(message || "").replace(/\r\n/g, "\n");
  for (const marker of MARKERS) text = text.replaceAll(marker, "");
  text = text.trim();
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  const paragraphs = text.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
  const selected = [];
  for (const paragraph of paragraphs) {
    if ((paragraph.startsWith("#") || paragraph.startsWith("```")) && selected.length === 0) continue;
    selected.push(paragraph);
    if (selected.join("\n\n").length >= 900) break;
  }
  const resultSummary = selected.join("\n\n").trim().slice(0, 900)
    || "本轮没有可保存的用户可见结果摘要。";
  const artifacts = [];
  const patterns = [/\[[^\]]+\]\(([^)]+)\)/g, /`([^`\n]*(?:[A-Za-z]:\\|\/)[^`\n]+)`/g, /(?<!\w)([A-Za-z]:\\[^\s<>"|?*]+)/g];
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const value = match[1]?.trim();
      if (value && !value.startsWith("http://") && !value.startsWith("https://") && !artifacts.includes(value)) {
        artifacts.push(value.slice(0, 1000));
      }
      if (artifacts.length >= 12) break;
    }
  }
  const nextActions = matchedLines(lines, NEXT);
  const lower = text.toLowerCase();
  const blocked = ["阻塞", "无法继续", "需要用户", "等待授权", "失败"].some((cue) => lower.includes(cue));
  const completed = ["已完成", "完成了", "全部通过", "已经解决", "已部署"].some((cue) => lower.includes(cue));
  return {
    status: blocked ? "blocked" : completed && nextActions.length === 0 ? "completed" : "waiting",
    result_summary: resultSummary,
    decisions: matchedLines(lines, DECISION),
    artifacts,
    evidence: matchedLines(lines, EVIDENCE),
    next_actions: nextActions,
  };
}
