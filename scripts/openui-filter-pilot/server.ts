import page from "./index.html";
import { comparisonSchema, commandSchema, loopbackWorkbenchURL, sameOrigin, snapshotSchema } from "./src/contracts";

const file = process.env.OPENUI_COMPARISON;
if (!file) throw new Error("Set OPENUI_COMPARISON to an explicit Rust-exported comparison JSON file");
if (Bun.file(file).size > 8 * 1024 * 1024) throw new Error("Comparison file exceeds 8 MiB");
const comparison = comparisonSchema.parse(await Bun.file(file).json());
const endpoint = process.env.WORKBENCH_URL ? loopbackWorkbenchURL(process.env.WORKBENCH_URL) : null;
const runID = process.env.SOTF_WORKBENCH_RUN_ID;
if (endpoint && (!runID || !/^[A-Za-z0-9_-]{16,128}$/.test(runID))) throw new Error("Set a valid server-held SOTF_WORKBENCH_RUN_ID");
const port = Number(process.env.PORT ?? 8094);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid PORT");
const origin = `http://127.0.0.1:${port}`;
const json = (value: unknown, status = 200) => Response.json(value, { status,
  headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
async function designer(request: Request): Promise<Response> {
  if (!sameOrigin(request, origin)) return json({ error: "Origin rejected" }, 403);
  if (!endpoint) return json({ error: "Designer disconnected: start the Rust workbench and configure WORKBENCH_URL" }, 503);
  try {
    if (request.method === "POST" && !request.headers.get("content-type")?.startsWith("application/json"))
      return json({ error: "JSON required" }, 415);
    const text = request.method === "GET" ? '{"operation":"inspect"}' : await request.text();
    if (text.length > 8192) return json({ error: "Command too large" }, 413);
    const command = commandSchema.parse(JSON.parse(text));
    const response = await fetch(endpoint, { method: "POST", redirect: "error",
      headers: { "Content-Type": "application/json", "x-sotf-dev-run-id": runID! },
      body: JSON.stringify(command), signal: AbortSignal.timeout(6000) });
    const envelope = await response.json() as { ok: boolean; error?: string; value?: unknown };
    if (!response.ok || !envelope.ok) return json({ error: envelope.error ?? "Designer command rejected" }, 409);
    return json(snapshotSchema.parse(envelope.value));
  } catch (error) { return json({ error: error instanceof Error ? error.message : "Designer request failed" }, 400); }
}
Bun.serve({ hostname: "127.0.0.1", port, maxRequestBodySize: 8192, development: { hmr: true, console: true },
  routes: { "/": page, "/api/comparison": request => sameOrigin(request, origin)
    ? json(comparison) : json({ error: "Origin rejected" }, 403),
    "/api/designer": { GET: designer, POST: designer } },
  fetch() { return new Response("Not found", { status: 404 }); },
});
console.log(`OpenUI comparison pilot: ${origin}; chat disabled; no model requests`);
