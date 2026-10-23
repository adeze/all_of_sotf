import React,{useEffect,useState} from "react";
import {createRoot} from "react-dom/client";
import {ResponsePlot,type Trace} from "./response-plot";
import {chartSchema,parseCallback,viewerURL,type EnvelopeChart,type EnvelopeCallback} from "./envelope-contracts";
import "./style.css";

async function readJSON(path:string):Promise<any>{
  const response=await fetch(path,{cache:"no-store"});const value=await response.json();
  if(!response.ok)throw new Error(value.error??"Study request failed");return value;
}
function App(){
  const [chart,setChart]=useState<EnvelopeChart|null>(null),[viewer,setViewer]=useState("");
  const [records,setRecords]=useState<EnvelopeCallback[]>([]),[error,setError]=useState("");
  const [channels,setChannels]=useState<string[]>([]),[mode,setMode]=useState(""),[generation,setGeneration]=useState(0);
  const [playing,setPlaying]=useState(true),[tab,setTab]=useState<"response"|"rerun">("response");
  useEffect(()=>{
    let cancelled=false,timer:ReturnType<typeof setTimeout>|undefined;
    async function start(){
      try{
        const metadata=await readJSON("/api/study");if(cancelled)return;
        const config=chartSchema.parse(metadata.chart);setChart(config);setChannels(config.channels);
        setMode(config.smoothing_modes[0]);setViewer(viewerURL(metadata.viewer_url));let after=0;
        async function poll(){
          try{
            const batch=await readJSON(`/api/callbacks?after=${after}`);if(cancelled)return;
            if(!Array.isArray(batch.callbacks)||!Number.isInteger(batch.next)||batch.next<after||batch.next>10000||batch.next-after!==batch.callbacks.length)throw new Error("Invalid study callback cursor");
            const incoming=batch.callbacks.map((value:unknown)=>parseCallback(value,config));
            after=batch.next;setRecords(previous=>[...previous,...incoming]);setError("");
          }catch(problem){if(!cancelled)setError(problem instanceof Error?problem.message:"Study polling failed");}
          if(!cancelled)timer=setTimeout(poll,1000);
        }
        await poll();
      }catch(problem){if(!cancelled)setError(problem instanceof Error?problem.message:"Study loading failed");}
    }
    void start();return()=>{cancelled=true;if(timer)clearTimeout(timer);};
  },[]);
  const maxGeneration=records.reduce((max,item)=>Math.max(max,item.generation),0);
  useEffect(()=>{if(!playing||!maxGeneration)return;if(generation>=maxGeneration){setPlaying(false);return;}
    const timer=setInterval(()=>setGeneration(current=>Math.min(current+1,maxGeneration)),160);return()=>clearInterval(timer);},[playing,maxGeneration,generation]);
  const selected=channels.flatMap(channel=>{
    const matching=records.filter(row=>row.channel===channel&&row.generation<=generation);
    const latest=matching.reduce<EnvelopeCallback|undefined>((best,row)=>!best||row.generation>best.generation||row.generation===best.generation&&row.callback_index>=best.callback_index?row:best,undefined);
    return latest?[latest]:[];
  });
  const colors=["#3478ce","#c46b22","#8559ad","#178279","#bd4872","#687930","#7163c0","#7c6758"];
  const traces:Trace[]=selected.map(row=>({label:row.channel,color:colors[chart!.channels.indexOf(row.channel)%colors.length],
    frequency:row.frequency_hz,db:row.display_best_db[mode],band:row.envelopes[mode]}));
  if(chart&&selected.length)traces.push({label:"Common target",color:"#967600",frequency:selected[0].frequency_hz,db:selected[0].frequency_hz.map(()=>chart.target_db)});
  return <main><header><p className="eyebrow">SPATIAL STUDIO · OFFLINE STUDY</p><h1>Solver response</h1><p className="muted">Real solver callbacks · common frequency and level axes · read-only</p></header>
    <nav aria-label="Study views" style={{display:"flex",gap:8,marginBottom:20}}>{(["response","rerun"] as const).map(value=><button key={value} className={tab===value?undefined:"secondary"} aria-pressed={tab===value} onClick={()=>setTab(value)}>{value==="response"?"Response":"Rerun inspector"}</button>)}</nav>
    {error&&<p role="alert" className="error">{error}</p>}
    {!chart?<p>Loading study…</p>:tab==="response"?<section className="card"><div className="heading"><h2>Best response and population spread</h2><label>Smoothing<select value={mode} onChange={event=>setMode(event.target.value)}>{chart.smoothing_modes.map(value=><option key={value}>{value}</option>)}</select></label></div>
      <fieldset style={{border:0,padding:0,marginTop:18}}><legend>Compare speakers</legend><div style={{display:"flex",gap:20,flexWrap:"wrap"}}>{chart.channels.map(channel=><label key={channel} style={{display:"flex",alignItems:"center",gap:8}}><input style={{width:"auto",margin:0}} type="checkbox" checked={channels.includes(channel)} onChange={event=>setChannels(previous=>event.target.checked?[...previous,channel]:previous.filter(item=>item!==channel))}/>{channel}</label>)}</div></fieldset>
      {traces.length?<ResponsePlot traces={traces} domain={chart.domain} label="Solver response, logarithmic frequency in Hz and relative level in dB"/>:<p>{channels.length?"Waiting for selected speakers at this generation…":"Select speakers to compare."}</p>}
      <div style={{display:"flex",alignItems:"center",gap:18,marginTop:24}}><button onClick={()=>{if(!playing&&generation>=maxGeneration)setGeneration(0);setPlaying(value=>!value);}}>{playing?"Pause":"Play"}</button><label style={{flex:1}}>Generation {generation} / {maxGeneration}<input aria-label="Solver generation" type="range" min={0} max={maxGeneration} value={generation} disabled={!maxGeneration} onChange={event=>{setPlaying(false);setGeneration(Number(event.target.value));}}/></label></div>
      <p className="muted">Shading: population min–max and 10th–90th percentiles. Smoothing computed in Rust. Zoom changes chart scales; axes stay in place.</p>
      <table><caption>Selected solver checkpoints</caption><thead><tr><th>Speaker</th><th>Generation</th><th>Objective</th><th>Population</th></tr></thead><tbody>{selected.map(row=><tr key={row.channel}><th>{row.channel}</th><td>{row.generation}</td><td>{row.loss.toFixed(5)}</td><td>{row.envelopes[mode].count}</td></tr>)}</tbody></table>
    </section>:<section className="card" style={{padding:8}}><iframe title="Rerun workflow and solver history" src={viewer} style={{width:"100%",height:"75vh",border:0,borderRadius:12}}/></section>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<App/>);
