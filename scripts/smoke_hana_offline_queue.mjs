import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "shiyuan-hana-offline-"));
process.env.HANA_HOME = temporary;
const data = path.join(temporary, "plugin-data", "shiyuan-hook");
fs.mkdirSync(data, { recursive: true });
const configPath = path.join(data, "config.json");
fs.writeFileSync(configPath, JSON.stringify({
  coreUrl: "http://127.0.0.1:9", token: "test-token", body: "hana", timeoutMs: 80,
}), "utf8");

const client = await import(pathToFileURL(path.join(root, "connectors", "hana-hook", "shared", "client.js")).href);
let failed = false;
try {
  await client.durableCoreRequest("/v1/events", {
    method: "POST", body: { summary: "offline-first", idempotency_key: "one" },
  });
} catch { failed = true; }
if (!failed || client.offlineOutboxStatus().pending !== 1) throw new Error("Hana offline event was not queued");

const received = [];
const server = http.createServer((request, response) => {
  let body = "";
  request.on("data", (chunk) => { body += chunk; });
  request.on("end", () => {
    received.push({ url: request.url, body: JSON.parse(body || "{}") });
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end('{"ok":true}');
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
fs.writeFileSync(configPath, JSON.stringify({
  coreUrl: `http://127.0.0.1:${address.port}`, token: "test-token", body: "hana", timeoutMs: 500,
}), "utf8");
await new Promise((resolve) => setTimeout(resolve, 5100));
const replay = await client.flushOfflineOutbox();
server.close();
if (replay.replayed !== 1 || replay.remaining !== 0) throw new Error("Hana offline queue did not replay");
if (received[0]?.url !== "/v1/events" || received[0]?.body?.summary !== "offline-first") {
  throw new Error("Hana replay payload changed");
}
const resolved = path.resolve(temporary);
const tempBase = path.resolve(os.tmpdir()) + path.sep;
if (!resolved.startsWith(tempBase)) throw new Error("Unexpected temporary path");
fs.rmSync(resolved, { recursive: true, force: true });
console.log("Hana offline queue OK: durable local enqueue, reconnect replay, ordered deletion");
