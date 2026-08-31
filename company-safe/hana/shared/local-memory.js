import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const MAX_CANDIDATE_LENGTH = 300;
const MAX_CANDIDATES_PER_EVENT = 3;
const sentenceSplit = /[\r\n。！？!?；;]+/;
const leadingDecoration = /^[\s>*#\-•·\d.、（）()]+/;
const rememberPrefix = /^(?:请)?记住(?:一下)?[：:,，\s]*/;
const questionCue = /(?:吗|么|呢|什么|是否|能否|可否|怎么|如何|为什么|哪(?:个|些|里)?|几(?:个|次|点)?)\s*$/;
const confidentialCue = /(?:保密|机密|未公开|项目代号|客户姓名|供应商名称|内部路径|人员评价)/;
const secretOrRawData = [
  /(?:password|passwd|secret|token|api[_\- ]?key|access[_\- ]?key)\s*[:=]\s*\S+/i,
  /sk-[a-z0-9_\-]{8,}/i,
  /-----BEGIN\s+(?:RSA|OPENSSH|EC|DSA)?\s*PRIVATE\s+KEY-----/i,
  /(?:[a-z]:\\|\\\\|\/home\/|\/users\/)\S+/i,
  /https?:\/\/\S+/i,
  /[\w.+-]+@[\w.-]+\.[a-z]{2,}/i,
  /\b\d{12,}\b/,
];
const triggers = [
  ["preference", /(?:^|[，,\s])我(?:更)?(?:喜欢|偏好|倾向|不喜欢|讨厌|习惯|常用|希望|不希望)/, 0.84],
  ["workflow_preference", /(?:^|[，,\s])(?:以后|后续)(?:请|需要|希望|默认|都要|不要|不再)/, 0.86],
  ["workflow_preference", /(?:^|[，,\s])我的(?:原则|工作方式|开发流程|沟通方式|习惯|偏好)(?:是|为|：|:)/, 0.88],
  ["fact", /(?:^|[，,\s])我(?:目前是|现在是|是|主要使用|通常使用|常用的是)/, 0.80],
];
const vagueReferenceCues = [
  "以前那个", "之前那个", "上次那个", "刚才那个", "原来那个", "那个东西",
  "那件事", "后来呢", "然后呢", "继续刚才", "接着刚才", "续上",
];

export function stateRoot() {
  return path.resolve(
    process.env.SHIYUAN_COMPANY_STATE || path.join(os.homedir(), ".shiyuan-company", "state"),
  );
}

function statusDir(status) {
  if (!["candidate", "confirmed", "rejected"].includes(status)) {
    throw new Error(`unsupported memory status: ${status}`);
  }
  const directory = path.join(stateRoot(), "memory", status);
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

export function normalizeMemoryContent(content) {
  return String(content).normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}_]+/gu, "");
}

export function memoryFingerprint(content) {
  return crypto.createHash("sha256").update(normalizeMemoryContent(content), "utf8").digest("hex");
}

function blocked(sentence) {
  return confidentialCue.test(sentence) || secretOrRawData.some((pattern) => pattern.test(sentence));
}

function candidateFromSentence(value) {
  let sentence = String(value).replace(leadingDecoration, "").replace(/^[\s:：,，。]+|[\s:：,，。]+$/g, "");
  if (sentence.length < 6 || sentence.length > MAX_CANDIDATE_LENGTH) return null;
  if (questionCue.test(sentence) || blocked(sentence)) return null;
  if (rememberPrefix.test(sentence)) {
    sentence = sentence.replace(rememberPrefix, "").replace(/^[\s:：,，。]+|[\s:：,，。]+$/g, "");
    if (sentence.length < 4 || sentence.length > MAX_CANDIDATE_LENGTH || blocked(sentence)) return null;
    return {
      kind: /(?:以后|后续|流程|默认|每次|始终)/.test(sentence) ? "workflow_preference" : "fact",
      content: sentence,
      confidence: 0.96,
      scope: "personal",
      sensitivity: "private",
    };
  }
  for (const [kind, pattern, confidence] of triggers) {
    if (pattern.test(sentence)) {
      return { kind, content: sentence, confidence, scope: "personal", sensitivity: "private" };
    }
  }
  return null;
}

export function extractMemoryCandidates(text) {
  if (typeof text !== "string" || !text.trim()) return [];
  const result = [];
  const seen = new Set();
  for (const sentence of text.slice(0, 12000).split(sentenceSplit)) {
    const candidate = candidateFromSentence(sentence);
    if (!candidate) continue;
    const fingerprint = memoryFingerprint(candidate.content);
    if (!fingerprint || seen.has(fingerprint)) continue;
    seen.add(fingerprint);
    result.push(candidate);
    if (result.length >= MAX_CANDIDATES_PER_EVENT) break;
  }
  return result;
}

function writeAtomic(target, value) {
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, target);
}

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

function isVagueReference(query) {
  const compact = String(query || "").replace(/\s+/g, "");
  return vagueReferenceCues.some((cue) => compact.includes(cue));
}

function historyDirectory() {
  const directory = path.join(stateRoot(), "history");
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

function historyPath(body, sessionId) {
  const identity = `${body}::${sessionId || "unknown"}`;
  const digest = crypto.createHash("sha256").update(identity, "utf8").digest("hex").slice(0, 24);
  return path.join(historyDirectory(), `${body}-${digest}.jsonl`);
}

function redactCredentials(value) {
  return String(value || "")
    .replace(/\b(password|passwd|secret|token|api[_ -]?key|access[_ -]?key)\s*[:=]\s*[^\s,;]+/gi, (_, key) => `${key}=[REDACTED]`)
    .replace(/\bsk-[a-z0-9_-]{8,}\b/gi, "[REDACTED_API_KEY]")
    .replace(/-----BEGIN\s+(?:RSA|OPENSSH|EC|DSA)?\s*PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:RSA|OPENSSH|EC|DSA)?\s*PRIVATE\s+KEY-----/gi, "[REDACTED_PRIVATE_KEY]");
}

export function appendVisibleMessage(body, sessionId, role, content, device = "company") {
  const text = redactCredentials(String(content || "").trim());
  if (!text || !["user", "assistant"].includes(role)) return null;
  const fingerprint = crypto.createHash("sha256")
    .update(`${body}\0${sessionId || ""}\0${role}\0${text}`, "utf8").digest("hex");
  const target = historyPath(body, sessionId);
  if (fs.existsSync(target)) {
    const value = fs.readFileSync(target, "utf8");
    if (value.slice(-20000).includes(`"fingerprint":"${fingerprint}"`)
      || value.slice(-20000).includes(`"fingerprint": "${fingerprint}"`)) return null;
  }
  const record = {
    id: crypto.randomUUID(), fingerprint, body, device, session_id: sessionId || null,
    role, content: text, visible_only: true, local_only: true, created_at: nowIso(),
  };
  fs.appendFileSync(target, `${JSON.stringify(record)}\n`, "utf8");
  return record;
}

function historyRecords(maxFiles = 500, maxRecords = 20000) {
  const paths = fs.readdirSync(historyDirectory(), { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".jsonl"))
    .map((entry) => path.join(historyDirectory(), entry.name))
    .sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs)
    .slice(0, maxFiles);
  const records = [];
  for (const target of paths) {
    try {
      for (const line of fs.readFileSync(target, "utf8").split("\n")) {
        if (!line.trim()) continue;
        const value = JSON.parse(line);
        if (["user", "assistant"].includes(value.role) && value.content) records.push(value);
      }
    } catch {}
  }
  return records.sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || ""))).slice(0, maxRecords);
}

function workStatePath() {
  const directory = path.join(stateRoot(), "work");
  fs.mkdirSync(directory, { recursive: true });
  return path.join(directory, "state.json");
}

function readWorkState() {
  try {
    const value = JSON.parse(fs.readFileSync(workStatePath(), "utf8"));
    if (Array.isArray(value.workstreams) && value.links && typeof value.links === "object") {
      value.version = 2;
      value.activities ||= [];
      value.cursors ||= {};
      value.next_sequence ||= 1;
      return value;
    }
  } catch {}
  return { version: 2, workstreams: [], links: {}, activities: [], cursors: {}, next_sequence: 1 };
}

function saveWorkState(value) {
  value.version = 2;
  value.workstreams = [...value.workstreams]
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")))
    .slice(0, 100);
  value.activities = [...(value.activities || [])]
    .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0))
    .slice(-1000);
  writeAtomic(workStatePath(), value);
}

function recordActivity(state, body, kind, work, summary) {
  const sequence = Number(state.next_sequence || 1);
  const activity = {
    id: crypto.randomUUID(), sequence, body, kind, workstream_id: work.id, title: work.title || "",
    summary: String(summary || "").slice(0, 900), created_at: nowIso(), local_only: true,
  };
  state.next_sequence = sequence + 1;
  state.activities ||= [];
  state.activities.push(activity);
  return activity;
}

function effectiveWork(value) {
  const item = { ...value };
  item.effective_status = item.status === "running" && item.lease_until && item.lease_until <= nowIso()
    ? "stale" : item.status;
  const checkpoints = item.checkpoints || [];
  item.latest_checkpoint = checkpoints.length ? checkpoints.at(-1) : null;
  return item;
}

export function classifyWorkPrompt(prompt) {
  const text = String(prompt || "").trim().replace(/\s+/g, " ").toLowerCase();
  if (["做了什么", "做到哪", "什么进展", "当前进度", "在跑什么", "任务状态"].some((cue) => text.includes(cue))) return "inquiry";
  if (["接手", "转交", "交给", "换到", "由你继续", "让你继续", "你来继续"].some((cue) => text.includes(cue))) return "transfer";
  if (["继续刚才", "继续上次", "接着刚才", "接着做", "继续做", "续上", "恢复任务"].some((cue) => text.includes(cue))) return "continuation";
  if (["实现", "开发", "修复", "解决", "修改", "调整", "搭建", "部署", "安装", "升级", "测试", "验证", "检查", "扫描", "分析", "整理", "生成", "创建", "制作", "构建", "打包", "同步", "迁移", "执行", "查找", "查看", "读取", "编写", "优化", "完善"].some((cue) => text.includes(cue))) return "work";
  return "chat";
}

function workTitle(prompt, limit = 120) {
  const text = String(prompt || "").trim().replace(/\s+/g, " ");
  return (text.split(/[。！？!?\n]/, 1)[0].trim() || "未命名工作").slice(0, limit);
}

export function startLocalWork(body, sessionId, prompt, device = "company") {
  const mode = classifyWorkPrompt(prompt);
  const state = readWorkState();
  if (["chat", "inquiry"].includes(mode)) return { mode, workstream: null, lease_conflict: false, recent_work: state.workstreams.slice(0, 6).map(effectiveWork) };
  const timestamp = nowIso();
  const leaseUntil = new Date(Date.now() + 20 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const linkKey = sessionId ? `${body}::${sessionId}` : null;
  let work = state.workstreams.find((item) => linkKey && item.id === state.links[linkKey]);
  if (work?.status === "completed" && mode === "work") work = null;
  if (!work && ["continuation", "transfer"].includes(mode)) work = state.workstreams[0];
  if (!work) {
    work = {
      id: crypto.randomUUID(), title: workTitle(prompt), objective: workTitle(prompt, 300), status: "running",
      owner_body: body, owner_device: device, owner_session_id: sessionId || null, lease_until: leaseUntil,
      latest_receipt: null, checkpoints: [], local_only: true, created_at: timestamp, updated_at: timestamp,
    };
    state.workstreams.unshift(work);
  }
  const conflict = work.status === "running" && String(work.lease_until || "") > timestamp
    && (work.owner_body !== body || work.owner_session_id !== (sessionId || null));
  if (!conflict || mode === "transfer") {
    Object.assign(work, { status: "running", owner_body: body, owner_device: device, owner_session_id: sessionId || null, lease_until: leaseUntil, updated_at: timestamp });
    if (linkKey) state.links[linkKey] = work.id;
    recordActivity(state, body, work.latest_receipt ? "resumed" : "started", work, prompt);
  }
  saveWorkState(state);
  return { mode, workstream: effectiveWork(work), lease_conflict: conflict, recent_work: state.workstreams.slice(0, 6).map(effectiveWork) };
}

export function compactLocalAssistantMessage(message) {
  const text = String(message || "").replaceAll("🐳 公司十元本地模式", "").replaceAll("🐳 十元在线", "").trim();
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  const paragraphs = text.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
  let summary = "";
  for (const part of paragraphs) {
    if ((part.startsWith("#") || part.startsWith("```")) && !summary) continue;
    summary = `${summary}\n\n${part}`.trim();
    if (summary.length >= 900) break;
  }
  const matched = (cues) => lines.filter((line) => cues.some((cue) => line.toLowerCase().includes(cue))).slice(0, 5).map((line) => line.slice(0, 500));
  const nextActions = matched(["下一步", "未完成", "尚未", "还需", "仍需", "待处理", "遗留"]);
  const lower = text.toLowerCase();
  const blocked = ["阻塞", "无法继续", "需要用户", "等待授权", "失败"].some((cue) => lower.includes(cue));
  const completed = ["已完成", "完成了", "全部通过", "已经解决", "已部署"].some((cue) => lower.includes(cue));
  return {
    status: blocked ? "blocked" : completed && !nextActions.length ? "completed" : "waiting",
    result_summary: summary.slice(0, 900) || "本轮没有可保存的用户可见结果摘要。",
    decisions: matched(["决定", "采用", "选择", "保持", "改为"]), artifacts: [],
    evidence: matched(["通过", "passed", "healthy", "测试", "验证", "校验"]), next_actions: nextActions,
  };
}

export function recordLocalWorkReceipt(body, sessionId, receipt, device = "company") {
  const state = readWorkState();
  const workId = sessionId ? state.links[`${body}::${sessionId}`] : null;
  const work = state.workstreams.find((item) => item.id === workId);
  if (!work) return null;
  const value = {
    id: crypto.randomUUID(), body, device, session_id: sessionId || null, status: receipt.status,
    result_summary: String(receipt.result_summary || "").slice(0, 900), decisions: (receipt.decisions || []).slice(0, 10),
    artifacts: (receipt.artifacts || []).slice(0, 10), evidence: (receipt.evidence || []).slice(0, 10),
    next_actions: (receipt.next_actions || []).slice(0, 10), local_only: true, created_at: nowIso(),
  };
  Object.assign(work, { status: value.status, owner_body: body, owner_device: device, owner_session_id: sessionId || null, lease_until: null, latest_receipt: value, updated_at: value.created_at });
  recordActivity(state, body, "receipt", work, value.result_summary);
  saveWorkState(state);
  return value;
}

export function recordLocalWorkCheckpoint(body, sessionId, phase, summary, options = {}, device = "company") {
  const state = readWorkState();
  const workId = options.workstream_id || (sessionId ? state.links[`${body}::${sessionId}`] : null);
  const work = state.workstreams.find((item) => item.id === workId);
  if (!work) return null;
  const timestamp = nowIso();
  const checkpoint = {
    id: crypto.randomUUID(), phase: String(phase || "progress").slice(0, 80),
    summary: String(summary || "").slice(0, 900), body, device, session_id: sessionId || null,
    artifacts: (options.artifacts || []).slice(0, 10), evidence: (options.evidence || []).slice(0, 10),
    next_actions: (options.next_actions || []).slice(0, 10), visible_only: true, local_only: true,
    created_at: timestamp,
  };
  work.checkpoints ||= [];
  work.checkpoints.push(checkpoint);
  work.checkpoints = work.checkpoints.slice(-30);
  work.updated_at = timestamp;
  work.lease_until = new Date(Date.now() + 20 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "+00:00");
  recordActivity(state, body, "checkpoint", work, checkpoint.summary);
  saveWorkState(state);
  return checkpoint;
}

export function recentLocalWork(limit = 6) {
  return readWorkState().workstreams.slice(0, limit).map(effectiveWork);
}

export function catchUpLocalWork(body, limit = 20) {
  const state = readWorkState();
  const lastSeen = Number(state.cursors?.[body] || 0);
  const activities = (state.activities || [])
    .filter((item) => Number(item.sequence || 0) > lastSeen && item.body !== body)
    .slice(0, limit);
  const maxSequence = Math.max(lastSeen, ...(state.activities || []).map((item) => Number(item.sequence || 0)));
  state.cursors ||= {};
  state.cursors[body] = maxSequence;
  saveWorkState(state);
  return activities;
}

export function listMemories(status = "candidate", limit = 100) {
  return fs
    .readdirSync(statusDir(status), { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => JSON.parse(fs.readFileSync(path.join(statusDir(status), entry.name), "utf8")))
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")))
    .slice(0, Math.max(0, limit));
}

export function proposeFromText(text, sourceBody = "hana") {
  const created = [];
  for (const candidate of extractMemoryCandidates(text)) {
    const fingerprint = memoryFingerprint(candidate.content);
    const exists = ["candidate", "confirmed", "rejected"].some((status) =>
      fs.existsSync(path.join(statusDir(status), `${fingerprint}.json`)),
    );
    if (exists) continue;
    const timestamp = nowIso();
    const record = {
      id: `local-${fingerprint.slice(0, 24)}`,
      ...candidate,
      source: `auto-company-local:${sourceBody}`,
      evidence: "从用户明确陈述中保守提取；完整提示词未保存。",
      fingerprint,
      status: "candidate",
      local_only: true,
      created_at: timestamp,
      updated_at: timestamp,
    };
    writeAtomic(path.join(statusDir("candidate"), `${fingerprint}.json`), record);
    created.push(record);
  }
  return created;
}

function findMemory(memoryId) {
  for (const status of ["candidate", "confirmed", "rejected"]) {
    for (const record of listMemories(status, 10000)) {
      if (record.id === memoryId) {
        return { path: path.join(statusDir(status), `${record.fingerprint}.json`), record };
      }
    }
  }
  throw new Error(`memory not found: ${memoryId}`);
}

export function decideMemory(memoryId, decision, note = "") {
  if (!["confirm", "reject"].includes(decision)) throw new Error("decision must be confirm or reject");
  const found = findMemory(memoryId);
  if (found.record.status !== "candidate") throw new Error("only candidate memories can be reviewed");
  const targetStatus = decision === "confirm" ? "confirmed" : "rejected";
  const record = {
    ...found.record,
    status: targetStatus,
    updated_at: nowIso(),
    ...(note ? { review_note: String(note).slice(0, 2000) } : {}),
  };
  writeAtomic(path.join(statusDir(targetStatus), `${record.fingerprint}.json`), record);
  fs.unlinkSync(found.path);
  return record;
}

function searchUnits(value) {
  const normalized = normalizeMemoryContent(value);
  if (!normalized) return new Set();
  if (normalized.length < 2) return new Set([normalized]);
  return new Set(Array.from({ length: normalized.length - 1 }, (_, index) => normalized.slice(index, index + 2)));
}

export function recallMemories(query = "", limit = 8) {
  const records = listMemories("confirmed", 500);
  const queryUnits = searchUnits(query);
  if (!queryUnits.size) return records.slice(0, limit);
  return records
    .map((record) => {
      const memoryUnits = searchUnits(record.content || "");
      const overlap = [...queryUnits].filter((unit) => memoryUnits.has(unit)).length;
      return { score: overlap / queryUnits.size, record };
    })
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || String(right.record.updated_at).localeCompare(String(left.record.updated_at)))
    .slice(0, limit)
    .map((item) => item.record);
}

export function searchVisibleHistory(query = "", limit = 8) {
  const records = historyRecords();
  const queryUnits = searchUnits(query);
  const vague = isVagueReference(query);
  return records
    .map((record, index) => {
      const contentUnits = searchUnits(record.content || "");
      const overlap = [...queryUnits].filter((unit) => contentUnits.has(unit)).length;
      let score = queryUnits.size ? overlap / queryUnits.size : 0;
      const normalizedQuery = normalizeMemoryContent(query);
      if (normalizedQuery && normalizeMemoryContent(record.content || "").includes(normalizedQuery)) score += 1;
      if (vague) score += Math.max(0, 0.35 - index * 0.002);
      return { score, record };
    })
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || String(right.record.created_at).localeCompare(String(left.record.created_at)))
    .slice(0, limit)
    .map((item) => ({ ...item.record, score: Number(item.score.toFixed(4)), content: String(item.record.content).slice(0, 1600) }));
}

export function recentVisibleHistory(limit = 8) {
  return historyRecords(500, Math.max(1, limit)).slice(0, limit)
    .map((item) => ({ ...item, content: String(item.content).slice(0, 1600) }));
}

export function resolveLocalContext(query = "", body = "hana", limit = 8) {
  const vague = isVagueReference(query);
  const memories = recallMemories(query, limit);
  let history = searchVisibleHistory(query, limit);
  if (vague && !history.length) history = recentVisibleHistory(limit);
  const work = recentLocalWork(limit);
  const candidates = [];
  for (const item of work.slice(0, Math.min(4, limit))) {
    candidates.push({
      type: "work", id: item.id, title: item.title || "",
      summary: item.latest_checkpoint?.summary || item.latest_receipt?.result_summary || item.objective || "",
      created_at: item.updated_at || "",
    });
  }
  for (const item of history.slice(0, Math.max(0, limit - candidates.length))) {
    candidates.push({
      type: "history", id: item.id, title: `${item.body} ${item.role}`,
      summary: item.content || "", created_at: item.created_at || "",
    });
  }
  return {
    query, body, vague_reference: vague, memories, history, recent_work: work,
    candidate_interpretations: candidates.slice(0, limit),
    needs_clarification: vague && candidates.length > 1,
    guidance: "先按时间线和最近工作推断最可能对象；只有候选确实并列才询问用户。",
  };
}

export function localMemoryStatus() {
  const state = readWorkState();
  return {
    state_root: stateRoot(),
    candidate: listMemories("candidate", 10000).length,
    confirmed: listMemories("confirmed", 10000).length,
    rejected: listMemories("rejected", 10000).length,
    workstreams: recentLocalWork(10000).length,
    history_messages: historyRecords().length,
    work_activity: (state.activities || []).length,
    work_cursors: Object.keys(state.cursors || {}).length,
  };
}
