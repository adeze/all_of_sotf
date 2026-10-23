import React, { createContext, useContext, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createLibrary, defineComponent, Renderer } from "@openuidev/react-lang";
import { z } from "zod/v4";
import { acceptStudyPoll, comparisonSchema, snapshotSchema, type Comparison, type Snapshot } from "./contracts";
import { ResponsePlot } from "./response-plot";
import "./style.css";

type Context = { data: Comparison; channel: string; setChannel: (value: string) => void;
  snapshot: Snapshot | null; error: string; busy: boolean;
  refresh: () => Promise<void>; apply: (limits: Snapshot["designer"]) => Promise<void>;
  studyCommand:(operation:"run_study"|"cancel_study")=>Promise<void> };
const State = createContext<Context | null>(null);
const state = () => { const value = useContext(State); if (!value) throw new Error("Comparison context missing"); return value; };
const colors = ["#3458b5", "#bd5c27", "#7041a8", "#15756d"];
const candidateName = (name: string, index: number) => name.startsWith("Retained ") ? `Saved candidate ${String.fromCharCode(65+index)}` : name;
const dbLabel = (value: number) => `${Number(value.toFixed(1))} dB`;
const Chart = defineComponent({ name: "ResponseChart", description: "Actual serialized electrical FIR responses; never a room prediction",
  props: z.object({ title: z.string() }), component: ({ props }) => {
    const s = state();
    const channels = s.data.candidates.map(c => c.channels.find(ch => ch.name === s.channel));
    return <section className="card response"><div className="heading"><h2>{props.title}</h2><label>Channel <select value={s.channel} onChange={e => s.setChannel(e.target.value)}>
      {[...new Set(s.data.candidates.flatMap(c => c.channels.map(ch => ch.name)))].map(c => <option key={c}>{c}</option>)}</select></label></div>
      <ResponsePlot unity label={`Electrical FIR response comparison for ${s.channel}, logarithmic Hz versus dB`}
        traces={channels.flatMap((ch,i)=>ch?[{label:candidateName(s.data.candidates[i].name,i),color:colors[i%colors.length],frequency:ch.frequency_hz,db:ch.response_db}]:[])}/>
      <p className="muted">From serialized FIR coefficients, referenced to unity gain. No measured-room prediction or smoothing.</p>
    </section>;
  } });
const Tradeoffs = defineComponent({ name: "Tradeoffs", description: "Source-bound comparison scope and unavailable perceptual objectives",
  props: z.object({ title: z.string() }), component: ({ props }) => {
    const s = state();
    return <section className="card"><h2>{props.title}</h2><p>Compare attenuation and tonal balance across saved filters.</p>
      <table><caption>Sampled response range · {s.channel}</caption><thead><tr><th>Candidate</th><th>Lowest</th><th>Highest</th></tr></thead><tbody>{s.data.candidates.map((c,i) => {
        const ch=c.channels.find(v=>v.name===s.channel);
        return <tr key={c.id}><th>{candidateName(c.name,i)}</th><td>{ch?dbLabel(Math.min(...ch.response_db)):"Unavailable"}</td><td>{ch?dbLabel(Math.max(...ch.response_db)):"Unavailable"}</td></tr>;
      })}</tbody></table>
      <dl><dt>Not scored</dt><dd>Acoustic headroom, soundstage, seat consistency and post-calibration decay.</dd></dl>
      <details><summary>Candidate identities</summary>{s.data.candidates.map(c => <p className="identity" key={c.id}>{c.name}<br/><code>{c.source_digest}</code></p>)}</details>
    </section>;
  } });
const LiveStudy = defineComponent({name:"LiveStudy",description:"Actual offline solver callbacks: prepared measurement, target and candidate prediction",
  props:z.object({title:z.string()}),component:({props})=>{
    const s=state(),study=s.snapshot?.study,response=study?.response;
    const finalChannel=study?.status==="completed"?(response?.channel&&study.final_responses[response.channel]?response.channel:Object.keys(study.final_responses)[0]):undefined;
    const final=finalChannel?study?.final_responses[finalChannel]:undefined;
    const stale=Boolean(study?.revision&&study.revision!==s.snapshot?.revision);
    return <section className="card response"><div className="heading"><h2>{props.title}{finalChannel?` · ${finalChannel}`:response?` · ${response.channel}`:""}</h2>
      <span role="status">{stale?"Previous study · draft changed":study?.status??"Not connected"}</span></div>
      {s.error&&<p role="alert" className="error">{s.error}</p>}
      {study?.status==="running"&&<progress aria-label="Offline study progress" max="1" value={study.progress}/>}
      {final?<ResponsePlot label={`Canonical final study response for ${finalChannel}, logarithmic Hz versus dB`} traces={[
        {label:"Study measurement",color:"#6b7585",frequency:final.frequency_hz,db:final.measurement_db},
        {label:"Final study prediction",color:"#3458b5",frequency:final.frequency_hz,db:final.predicted_db},
      ]}/>:response?<ResponsePlot label={`Offline predicted response for ${response.channel}, logarithmic Hz versus dB`} traces={[
        {label:"Prepared measurement",color:"#6b7585",frequency:response.frequency_hz,db:response.prepared_measurement_db},
        {label:"Target",color:"#15756d",frequency:response.frequency_hz,db:response.target_db},
        {label:study?.status==="running"?"Current candidate prediction":"Last optimizer candidate (not final output)",color:"#3458b5",frequency:response.frequency_hz,db:response.predicted_db},
      ]}/>:<p className="muted">Run an offline study to see the solver's response snapshots.</p>}
      {study&&study.status!=="idle"&&<p className="muted">Iteration {study.iteration} · {study.callback_count} callbacks recorded{study.loss!==null?` · ${study.status==="running"?"optimizer objective":"last optimizer objective"} ${study.loss.toFixed(4)}`:""}</p>}
      {final&&<p className="muted">Final objective: {final.pre_score===null?"unavailable":final.pre_score.toFixed(4)} → {final.post_score===null?"unavailable":final.post_score.toFixed(4)}. Final pipeline decisions can retain the original response.</p>}
      {study?.error&&<p role="alert" className="error">{study.error}</p>}
      <p className="muted">Predictions use this study's measurement reference. Saved electrical filters below remain separate. Display refresh may skip frames; direct callback recording preserves the solver history.</p>
    </section>;
  }});
const Controls = defineComponent({ name: "DesignerControls", description: "Four supported revision-bound offline Designer limits; saved candidates are immutable",
  props: z.object({ title: z.string() }), component: ({ props }) => {
    const s = state(); const [draft,setDraft] = useState<Snapshot["designer"] | null>(null);
    useEffect(() => setDraft(s.snapshot?.designer ?? null), [s.snapshot?.revision]);
    const labels: Record<keyof Snapshot["designer"],string> = {min_freq:"Correction from (Hz)",max_freq:"Correction to (Hz)",max_boost_db:"Maximum boost (dB)",max_cut_db:"Maximum cut (dB)"};
    const running=s.snapshot?.study?.status==="running";
    const dirty=Boolean(draft&&s.snapshot&&(Object.keys(labels) as (keyof typeof labels)[]).some(k=>draft[k]!==s.snapshot!.designer[k]));
    const canStudy=s.snapshot?.capabilities?.commands.includes("run_study");
    return <section className="card"><h2>{props.title}</h2><p>Apply limits, then run an offline study. Retained Swift candidates remain separate.</p>
      {draft && <form onSubmit={e => { e.preventDefault(); void s.apply(draft); }}><div className="fields">{(Object.keys(labels) as (keyof typeof labels)[]).map(key => <label key={key}>{labels[key]}<input type="number" required disabled={running||s.busy} min={key.includes("freq")?20:0} max={key.includes("freq")?20000:25} step={key.includes("freq")?1:0.5} value={draft[key]} onChange={e => setDraft({...draft,[key]:e.target.valueAsNumber})}/></label>)}</div>
        <button disabled={s.busy||running||!dirty} type="submit">Apply draft limits</button></form>}
      {canStudy&&<div className="study-actions">{running?<button disabled={s.busy} onClick={()=>void s.studyCommand("cancel_study")}>Cancel study</button>:<button disabled={s.busy||dirty} onClick={()=>void s.studyCommand("run_study")}>Run offline study</button>}
        {dirty&&<p className="muted">Apply pending limits before starting the study.</p>}</div>}
      <button className="secondary" disabled={s.busy} onClick={() => void s.refresh()}>Refresh Designer</button>
      {s.snapshot && <p className="muted" role="status">{running?"Study running; draft edits are locked.":s.snapshot.study?.results_current?"Study results match the current draft.":"No completed study for the current draft."}</p>}
    </section>;
  } });
const Panel = defineComponent({ name:"Comparison", description:"Filter comparison using the current project data and supported controls",
  props:z.object({title:z.string(),children:z.array(z.union([Chart.ref,Tradeoffs.ref,Controls.ref,LiveStudy.ref]))}),
  component:({props,renderNode}) => <main><header><p className="eyebrow">CALIBRATION WORKBENCH / OPENUI PILOT</p><h1>{props.title}</h1><p>Compare saved filters. Adjust the next draft.</p></header><div className="grid">{renderNode(props.children)}</div></main> });
const library = createLibrary({root:"Comparison",components:[Panel,Chart,Tradeoffs,Controls,LiveStudy]});
const program = 'root = Comparison("Filter designer", [live, controls, tradeoffs, chart])\nlive = LiveStudy("Offline study response")\nchart = ResponseChart("Saved electrical responses")\ntradeoffs = Tradeoffs("Saved filter comparison")\ncontrols = DesignerControls("Designer limits")';

async function request(url: string, options?: RequestInit) { const response=await fetch(url,options); const data=await response.json(); if(!response.ok) throw new Error(data.error ?? "Request failed"); return data; }
function App() {
  const [data,setData]=useState<Comparison|null>(null),[snapshot,setSnapshot]=useState<Snapshot|null>(null);
  const [channel,setChannel]=useState("FL"),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const refresh=async()=>{setBusy(true);try{setSnapshot(snapshotSchema.parse(await request("/api/designer")));setError("");}catch(e){setError(String(e));}finally{setBusy(false);}};
  const apply=async(patch:Snapshot["designer"])=>{if(!snapshot)return;setBusy(true);try{setSnapshot(snapshotSchema.parse(await request("/api/designer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({operation:"update_designer",expected_revision:snapshot.revision,patch})})));setError("");}catch(e){setError(String(e));}finally{setBusy(false);}};
  const studyCommand=async(operation:"run_study"|"cancel_study")=>{if(!snapshot)return;setBusy(true);try{setSnapshot(snapshotSchema.parse(await request("/api/designer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({operation,expected_revision:snapshot.revision})})));setError("");}catch(e){setError(String(e));}finally{setBusy(false);}};
  useEffect(()=>{void request("/api/comparison").then(v=>{const parsed=comparisonSchema.parse(v);setData(parsed);setChannel(parsed.candidates[0].channels[0].name);}).catch(e=>setError(String(e)));void refresh();},[]);
  useEffect(()=>{
    if(snapshot?.study?.status!=="running")return;
    let active=true,inFlight=false;
    const timer=setInterval(async()=>{
      if(inFlight)return;inFlight=true;
      try{const next=snapshotSchema.parse(await request("/api/designer"));if(active){setSnapshot(current=>current?acceptStudyPoll(current,next):current);setError("");}}
      catch(e){if(active)setError(String(e));}finally{inFlight=false;}
    },250);
    return()=>{active=false;clearInterval(timer);};
  },[snapshot?.study?.status,snapshot?.study?.revision,snapshot?.study?.id]);
  if(!data)return <main><h1>Filter comparison</h1><p role="status">{error||"Loading saved candidates…"}</p></main>;
  return <State.Provider value={{data,channel,setChannel,snapshot,error,busy,refresh,apply,studyCommand}}><Renderer response={program} library={library} isStreaming={false}/><footer>Chat disabled · deterministic OpenUI layout · no model or receiver requests</footer></State.Provider>;
}
if (import.meta.hot) {
  const root = (import.meta.hot.data.root ??= createRoot(document.getElementById("root")!));
  root.render(<App/>);
} else {
  createRoot(document.getElementById("root")!).render(<App/>);
}
