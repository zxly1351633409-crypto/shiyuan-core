import crypto from "node:crypto";

import {
  baseContext,
  compactBootstrap,
  coreRequest,
  durableCoreRequest,
  loadContextCache,
  loadConfig,
  offlineOutboxStatus,
  saveContextCache,
} from "../shared/client.js";
import { compactAssistantMessage, messageText } from "../shared/work-receipt.js";

function sessionId(event, extensionContext) {
  return event?.sessionId
    || event?.session_id
    || extensionContext?.sessionManager?.getSessionId?.()
    || process.env.HANA_SESSION_ID
    || null;
}

function contextMessage(content) {
  return {
    message: {
      customType: "shiyuan-core-context",
      content,
      display: false,
      details: { source: "shiyuan-hook" },
    },
  };
}

export function appendResponseMarker(message, marker) {
  if (message?.role !== "assistant" || !marker) return null;
  // Tool-use messages are intermediate messages, not the final user-visible answer.
  if (message.stopReason === "toolUse") return null;

  if (typeof message.content === "string") {
    if (message.content.trimEnd().endsWith(marker)) return null;
    return { ...message, content: `${message.content.trimEnd()}\n\n${marker}` };
  }
  if (!Array.isArray(message.content)) return null;

  const lastTextIndex = message.content.findLastIndex((part) => part?.type === "text");
  const visibleText = message.content
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("");
  if (visibleText.trimEnd().endsWith(marker)) return null;

  const content = message.content.map((part) => ({ ...part }));
  if (lastTextIndex >= 0) {
    const current = content[lastTextIndex].text || "";
    content[lastTextIndex].text = `${current.trimEnd()}\n\n${marker}`;
  } else {
    content.push({ type: "text", text: marker });
  }
  return { ...message, content };
}

export default function registerShiyuanContext(pi) {
  // Each AgentSession owns its extension instance, so this state follows the
  // current Hana session without leaking between concurrently open sessions.
  let turnStyle = { coreOnline: false, mode: "off", marker: "" };
  let currentTurn = { session: null, turnId: null, workstreamId: null };

  pi.on("before_agent_start", async (event, extensionContext) => {
    turnStyle = { coreOnline: false, mode: "off", marker: "" };
    const config = loadConfig();
    const session = sessionId(event, extensionContext);
    currentTurn = { session, turnId: event?.turnId || event?.turn_id || null, workstreamId: null };
    const base = baseContext(config, session);
    let workState = null;
    try {
      workState = await durableCoreRequest("/v1/work/turn-start", {
        method: "POST",
        timeoutMs: 5000,
        body: {
          ...base,
          prompt: String(event.prompt || "").slice(0, 12000),
          turn_id: currentTurn.turnId,
        },
      });
    } catch {}
    currentTurn.workstreamId = workState?.workstream?.id || null;
    if (config.captureMessages && event.prompt) {
      await durableCoreRequest("/v1/events", {
        method: "POST",
        timeoutMs: 5000,
        body: {
          event_type: "user_prompt",
          ...base,
          summary: String(event.prompt).slice(0, 12000),
          payload: { source: "hana-server-hook" },
          idempotency_key: crypto
            .createHash("sha256")
            .update(`${session || "none"}|${event.prompt}`)
            .digest("hex"),
        },
      }).catch(() => {});
      if (session) {
        const historyKey = crypto
          .createHash("sha256")
          .update(`hana|${session}|${currentTurn.turnId || "none"}|user|${event.prompt}`)
          .digest("hex");
        await durableCoreRequest("/v1/history/messages", {
          method: "POST",
          timeoutMs: 5000,
          body: {
            source: "hana",
            source_session_id: session,
            source_locator: "live-hook/hana",
            idempotency_key: historyKey,
            message: {
              role: "user",
              content: String(event.prompt).slice(0, 2_000_000),
              timestamp: new Date().toISOString(),
            },
          },
        }).catch(() => {});
      }
    }
    if (!workState) {
      const cached = loadContextCache();
      if (cached) {
        return contextMessage(compactBootstrap(
          cached.bootstrap,
          cached.recall || { items: [] },
          cached.historyRecall || {},
          offlineOutboxStatus(),
          { coreOnline: false, cachedAt: cached.savedAt },
        ));
      }
      return contextMessage(
        `[十元 Core 当前离线或不可达；本轮继续正常工作，不要声称已读取或写入长期记忆。允许保存的可见事件已进入本机补传箱，当前 ${offlineOutboxStatus().pending} 条。]`,
      );
    }
    try {
      const [bootstrap, recall, historyRecall] = await Promise.all([
        coreRequest("/v1/bootstrap", { method: "POST", body: base }),
        coreRequest("/v1/recall", {
          method: "POST",
          body: { ...base, query: event.prompt || "", limit: 8 },
        }),
        coreRequest("/v1/context/resolve", {
          method: "POST",
          body: { ...base, query: event.prompt || "", limit: 8 },
        }).catch(() => ({ items: [] })),
      ]);
      turnStyle = {
        coreOnline: true,
        mode: bootstrap?.response_style?.mode || "off",
        marker: bootstrap?.response_style?.marker || "",
      };
      try {
        saveContextCache(bootstrap, recall, historyRecall || {});
      } catch {}
      // Hana freezes a cache-prefix contract before before_agent_start runs.
      // Changing systemPrompt here invalidates that contract and aborts the turn.
      // A hidden custom message is still sent to the model as turn context without
      // mutating the frozen system prompt.
      return contextMessage(compactBootstrap(bootstrap, recall, historyRecall, offlineOutboxStatus()));
    } catch (error) {
      // Fail open: Hana remains usable when the NAS/Core is offline.
      const cached = loadContextCache();
      if (cached) {
        return contextMessage(compactBootstrap(
          cached.bootstrap,
          cached.recall || { items: [] },
          cached.historyRecall || {},
          offlineOutboxStatus(),
          { coreOnline: false, cachedAt: cached.savedAt },
        ));
      }
      return contextMessage(
        "[十元 Core 当前离线或不可达；本轮继续正常工作，不要声称已读取或写入长期记忆。]",
      );
    }
  });

  // The model still receives Core style guidance, but the connectivity marker
  // is a transport guarantee: append it to the finalized assistant message.
  // Hana applies this replacement before UI listeners and session persistence.
  pi.on("message_end", async (event) => {
    const config = loadConfig();
    const text = messageText(event.message);
    if (text && config.captureMessages !== false) {
      if (event.message?.stopReason === "toolUse") {
        const digest = crypto
          .createHash("sha256")
          .update(`${currentTurn.session || "none"}|${currentTurn.turnId || "none"}|checkpoint|${text}`)
          .digest("hex");
        await durableCoreRequest("/v1/work/checkpoints", {
          method: "POST",
          body: {
            ...baseContext(config, currentTurn.session),
            workstream_id: currentTurn.workstreamId,
            turn_id: currentTurn.turnId,
            phase: "implementing",
            summary: text.slice(0, 2000),
            idempotency_key: digest,
          },
        }).catch(() => {});
        return undefined;
      }
      const receipt = compactAssistantMessage(text);
      const digest = crypto
        .createHash("sha256")
        .update(`${currentTurn.session || "none"}|${currentTurn.turnId || "none"}|${receipt.result_summary}`)
        .digest("hex");
      await durableCoreRequest("/v1/work/receipts", {
        method: "POST",
        body: {
          ...baseContext(config, currentTurn.session),
          turn_id: currentTurn.turnId,
          ...receipt,
          idempotency_key: digest,
        },
      }).catch(() => {});
      if (currentTurn.session) {
        const historyKey = crypto
          .createHash("sha256")
          .update(`hana|${currentTurn.session}|${currentTurn.turnId || "none"}|assistant|${text}`)
          .digest("hex");
        await durableCoreRequest("/v1/history/messages", {
          method: "POST",
          body: {
            source: "hana",
            source_session_id: currentTurn.session,
            source_locator: "live-hook/hana",
            idempotency_key: historyKey,
            message: {
              role: "assistant",
              content: text.slice(0, 2_000_000),
              timestamp: new Date().toISOString(),
            },
          },
        }).catch(() => {});
      }
    }
    if (!turnStyle.coreOnline || turnStyle.mode === "off") return undefined;
    const message = appendResponseMarker(event.message, turnStyle.marker);
    return message ? { message } : undefined;
  });
}
