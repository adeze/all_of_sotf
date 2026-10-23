import { z } from "zod";

const samples = z.array(z.number().finite()).min(2).max(4096);
export const chartSchema = z.object({
  domain: z.object({minHz:z.number().positive().finite(),maxHz:z.number().positive().finite(),minDb:z.number().finite(),maxDb:z.number().finite()})
    .refine(v=>v.minHz<v.maxHz&&v.minDb<v.maxDb),
  channels:z.array(z.string().regex(/^[A-Za-z0-9_-]{1,64}$/)).min(1).max(8).refine(v=>new Set(v).size===v.length),
  target_db:z.number().finite(),smoothing_modes:z.array(z.string().min(1).max(64)).min(1).max(8),
  rerun_version:z.literal("0.40.0"),transfer_prohibited:z.literal(true),
});
export type EnvelopeChart = z.infer<typeof chartSchema>;
const bandSchema=z.object({min:samples,max:samples,p10:samples,p90:samples,count:z.number().int().min(1).max(256)});
const callbackSchema=z.object({channel:z.string(),generation:z.number().int().nonnegative(),callback_index:z.number().int().nonnegative(),
  loss:z.number().finite(),frequency_hz:samples,best_response_db:samples,
  display_best_db:z.record(z.string(),samples),envelopes:z.record(z.string(),bandSchema),
  terminal:z.object({success:z.boolean(),message:z.string(),finalized:z.boolean()}).nullable(),
});
export type EnvelopeCallback=z.infer<typeof callbackSchema>;
export function parseCallback(input:unknown,chart:EnvelopeChart):EnvelopeCallback {
  const value=callbackSchema.parse(input), n=value.frequency_hz.length;
  if(!chart.channels.includes(value.channel)||value.best_response_db.length!==n||
    !value.frequency_hz.every((f,i)=>f>0&&(!i||f>value.frequency_hz[i-1])))throw new Error("Invalid callback channel or frequency grid");
  for(const mode of chart.smoothing_modes){
    const best=value.display_best_db[mode],band=value.envelopes[mode];
    if(!best||!band||[best,band.min,band.max,band.p10,band.p90].some(a=>a.length!==n)||
      !band.min.every((v,i)=>v<=band.p10[i]&&band.p10[i]<=band.p90[i]&&band.p90[i]<=band.max[i]))throw new Error("Invalid callback response envelope");
  }
  return value;
}
export function parseCursor(value:string|null):number {
  if(value===null)return 0;
  if(!/^(0|[1-9]\d{0,4})$/.test(value)||Number(value)>10000)throw new Error("Invalid callback cursor");
  return Number(value);
}
export function viewerURL(value:unknown):string {
  if(typeof value!=="string")throw new Error("Missing viewer URL");
  const url=new URL(value);
  if(url.protocol!=="http:"||url.hostname!=="127.0.0.1"||url.username||url.password)throw new Error("Viewer must use loopback HTTP");
  const source=url.searchParams.get("url");
  if(source){const target=new URL(source);if(target.protocol!=="rerun+http:"||target.hostname!=="127.0.0.1"||target.username||target.password)throw new Error("Viewer source must use loopback Rerun HTTP");}
  return url.href;
}
