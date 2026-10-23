import { z } from "zod";

export const channelSchema = z.object({
  name: z.string(),
  sample_rate_hz: z.number().positive(),
  frequency_hz: z.array(z.number().positive().finite()).min(2).max(8192),
  response_db: z.array(z.number().finite()).min(2).max(8192),
}).refine(c => c.frequency_hz.length === c.response_db.length &&
  c.frequency_hz.every((f, i) => f < c.sample_rate_hz / 2 && (!i || f > c.frequency_hz[i - 1])),
  "Response requires matching, ordered sub-Nyquist samples");
export const comparisonSchema = z.object({
  candidates: z.array(z.object({
    id: z.string(), name: z.string(), kind: z.literal("serialized_electrical_fir"),
    source_digest: z.string(), channels: z.array(channelSchema).min(1),
  })).min(2).max(8),
});
export type Comparison = z.infer<typeof comparisonSchema>;
export type Channel = z.infer<typeof channelSchema>;

export const limitsSchema = z.object({
  min_freq: z.number().finite().min(20).max(20000),
  max_freq: z.number().finite().min(20).max(20000),
  max_boost_db: z.number().finite().min(0).max(25),
  max_cut_db: z.number().finite().min(0).max(25),
}).strict().refine(v => v.min_freq < v.max_freq, "Minimum frequency must be below maximum");
export const commandSchema = z.discriminatedUnion("operation", [
  z.object({ operation: z.literal("inspect") }).strict(),
  z.object({ operation: z.literal("update_designer"), expected_revision: z.string().min(1).max(128),
    patch: limitsSchema }).strict(),
  z.object({ operation: z.literal("run_study"), expected_revision: z.string().min(1).max(128) }).strict(),
  z.object({ operation: z.literal("cancel_study"), expected_revision: z.string().min(1).max(128) }).strict(),
]);
const samples = z.array(z.number().finite()).min(2).max(65536);
export const liveResponseSchema = z.object({
  channel:z.string(), frequency_hz:z.array(z.number().positive().finite()).min(2).max(65536),
  prepared_measurement_db:samples, target_db:samples, filter_db:samples, predicted_db:samples,
}).refine(v => [v.prepared_measurement_db,v.target_db,v.filter_db,v.predicted_db].every(a=>a.length===v.frequency_hz.length)
  && v.frequency_hz.every((f,i)=>!i||f>v.frequency_hz[i-1]),"Live response arrays must share an ordered frequency grid");
export const finalResponseSchema=z.object({frequency_hz:z.array(z.number().positive().finite()).min(2).max(65536),
  measurement_db:samples,predicted_db:samples,pre_score:z.number().finite().nullable(),post_score:z.number().finite().nullable(),
}).refine(v=>v.measurement_db.length===v.frequency_hz.length&&v.predicted_db.length===v.frequency_hz.length
  &&v.frequency_hz.every((f,i)=>!i||f>v.frequency_hz[i-1]),"Final response arrays require the canonical shared grid");
export const snapshotSchema = z.object({ revision: z.string(), designer: limitsSchema,
  chat_enabled: z.literal(false), offline_only: z.literal(true),
  capabilities:z.object({commands:z.array(z.string())}).optional(),
  study: z.object({ results_current:z.boolean(),status:z.enum(["idle","running","completed","cancelled","failed"]).default("idle"),
    iteration:z.number().int().nonnegative().default(0),progress:z.number().finite().min(0).max(1).default(0),
    loss:z.number().finite().nullable().default(null),revision:z.string().nullable().default(null),id:z.string().nullable().default(null),
    error:z.string().nullable().default(null),response:liveResponseSchema.nullable().default(null),
    callback_count:z.number().int().nonnegative().default(0),
    final_responses:z.record(z.string(),finalResponseSchema).default({}),
  }).passthrough().optional(),
});
export type Snapshot = z.infer<typeof snapshotSchema>;

export function acceptStudyPoll(current: Snapshot, next: Snapshot): Snapshot {
  if (current.revision !== next.revision || current.study?.id !== next.study?.id ||
      (next.study?.callback_count ?? 0) < (current.study?.callback_count ?? 0) ||
      (current.study?.status !== "running" && next.study?.status === "running")) return current;
  return next;
}

export function loopbackWorkbenchURL(value: string): URL {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || url.pathname !== "/workbench") {
    throw new Error("Designer endpoint must be an explicit loopback HTTP /workbench URL");
  }
  return url;
}

export function sameOrigin(request: Request, origin: string): boolean {
  const ref = request.headers.get("origin");
  return new URL(request.url).origin === origin && (!ref || ref === origin) &&
    !["cross-site", "same-site"].includes(request.headers.get("sec-fetch-site") ?? "");
}
