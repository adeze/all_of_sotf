import { useEffect, useId, useRef, useState } from "react";

export type PlotDomain = {minHz:number;maxHz:number;minDb:number;maxDb:number};
export type Trace = {label:string;color:string;frequency:number[];db:number[];
  band?:{min:number[];max:number[];p10:number[];p90:number[]}};

/** The key excludes response values so solver callbacks never reset the viewport. */
export function responsePlotFrame(traces:Trace[],unity=false,domain?:PlotDomain) {
  if(domain && (!Object.values(domain).every(Number.isFinite) || domain.minHz<=0 ||
    domain.maxHz<=domain.minHz || domain.maxDb<=domain.minDb)) throw new RangeError("Invalid response chart domain");
  let minHz=Infinity,maxHz=-Infinity,minDb=unity?-12:Infinity,maxDb=unity?6:-Infinity;
  for(const trace of traces) {
    for(const f of trace.frequency)if(Number.isFinite(f)&&f>0){minHz=Math.min(minHz,f);maxHz=Math.max(maxHz,f);}
    for(const db of [...trace.db,...(trace.band?.min??[]),...(trace.band?.max??[])])if(Number.isFinite(db)){minDb=Math.min(minDb,db);maxDb=Math.max(maxDb,db);}
  }
  const low=Math.floor(minDb/6)*6,high=Math.max(low+6,Math.ceil(maxDb/6)*6);
  const initial=domain??{minHz:minHz===maxHz?minHz/1.05:minHz,maxHz:minHz===maxHz?maxHz*1.05:maxHz,minDb:low,maxDb:high};
  if(!Object.values(initial).every(Number.isFinite))return null;
  return {key:JSON.stringify([unity,domain??null,traces.map(t=>[t.label,t.frequency])]),domain:initial};
}

/** Zoom changes numerical domains; the plot rectangle and axes never move. */
export function zoomPlotDomain(current:PlotDomain,base:PlotDomain,factor:number,x=.5,y=.5):PlotDomain {
  if(!Number.isFinite(factor)||factor<=0)return current;
  const range=(low:number,high:number,baseLow:number,baseHigh:number,anchor:number,minSpan:number)=>{
    const a=Math.max(0,Math.min(1,anchor));
    const span=Math.min(baseHigh-baseLow,Math.max(Math.min(minSpan,baseHigh-baseLow),(high-low)*factor));
    const start=Math.max(baseLow,Math.min(baseHigh-span,low+(high-low)*a-span*a));
    return [start,start+span];
  };
  const [minLog,maxLog]=range(Math.log(current.minHz),Math.log(current.maxHz),Math.log(base.minHz),Math.log(base.maxHz),x,.02);
  const [minDb,maxDb]=range(current.minDb,current.maxDb,base.minDb,base.maxDb,y,.5);
  return {minHz:Math.exp(minLog),maxHz:Math.exp(maxLog),minDb,maxDb};
}

/** Display only: supplied response samples and their original dB reference are unchanged. */
export function ResponsePlot({traces,label,unity=false,domain}:{traces:Trace[];label:string;unity?:boolean;domain?:PlotDomain}) {
  const frame=responsePlotFrame(traces,unity,domain);
  return frame?<ResponsePlotFrame key={frame.key} traces={traces} label={label} initial={frame.domain}/>:null;
}

function ResponsePlotFrame({traces,label,initial}:{traces:Trace[];label:string;initial:PlotDomain}) {
  const [base]=useState(initial);
  const [domain,setDomain]=useState(base);
  const svg=useRef<SVGSVGElement>(null);
  const clipId=useId();
  useEffect(()=>{
    const element=svg.current;
    if(!element)return;
    const wheel=(event:WheelEvent)=>{
      const matrix=element.getScreenCTM();
      if(!matrix)return;
      const point=element.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
      const local=point.matrixTransform(matrix.inverse());
      if(local.x<50||local.x>740||local.y<40||local.y>260)return;
      event.preventDefault();
      setDomain(d=>zoomPlotDomain(d,base,Math.exp(Math.max(-100,Math.min(100,event.deltaY))*.002),(local.x-50)/690,(260-local.y)/220));
    };
    element.addEventListener("wheel",wheel,{passive:false});
    return ()=>element.removeEventListener("wheel",wheel);
  },[base]);
  const x=(f:number)=>50+690*(Math.log(f)-Math.log(domain.minHz))/(Math.log(domain.maxHz)-Math.log(domain.minHz));
  const y=(db:number)=>260-(db-domain.minDb)/(domain.maxDb-domain.minDb)*220;
  const ticks=[...new Set([domain.minDb,(domain.minDb+domain.maxDb)/2,domain.maxDb,...(domain.minDb<0&&domain.maxDb>0?[0]:[])])];
  const curve=(trace:Trace)=>{
    let connected=false;
    return trace.frequency.map((f,i)=>{
      if(!(f>0)||!Number.isFinite(f)||!Number.isFinite(trace.db[i])){connected=false;return "";}
      const part=`${connected?'L':'M'}${x(f)},${y(trace.db[i])}`;connected=true;return part;
    }).join(" ");
  };
  const band=(trace:Trace,low:number[],high:number[])=>{
    const parts:string[]=[];let start=0;
    while(start<trace.frequency.length){
      const valid=(i:number)=>trace.frequency[i]>0&&Number.isFinite(trace.frequency[i])&&Number.isFinite(low[i])&&Number.isFinite(high[i])&&low[i]<=high[i];
      if(!valid(start)){start++;continue;}
      let end=start+1;while(end<trace.frequency.length&&valid(end))end++;
      if(end-start>1){
        const top=trace.frequency.slice(start,end).map((f,i)=>`${i?'L':'M'}${x(f)},${y(high[start+i])}`);
        const bottom=trace.frequency.slice(start,end).map((f,i)=>`L${x(f)},${y(low[start+i])}`).reverse();
        parts.push([...top,...bottom,"Z"].join(" "));
      }
      start=end;
    }
    return parts.join(" ");
  };
  return <><svg ref={svg} viewBox="0 0 780 310" role="img" aria-label={label} style={{display:"block",width:"100%",overflow:"hidden"}}>
    <defs><clipPath id={clipId}><rect x="50" y="40" width="690" height="220"/></clipPath></defs>
    {ticks.map(db=><g key={db}><line x1="50" y1={y(db)} x2="740" y2={y(db)} stroke={db===0?"#adb7c8":"#e2e5ec"}/><text x="40" y={y(db)+4} textAnchor="end">{Number(db.toFixed(1))}</text></g>)}
    {[20,50,100,200,500,1000,2000,5000,10000,20000].filter(f=>f>=domain.minHz&&f<=domain.maxHz).map(f=><g key={f}><line x1={x(f)} y1="40" x2={x(f)} y2="260" stroke="#eef0f4"/><text x={x(f)} y="282" textAnchor="middle">{f>=1000?`${f/1000}k`:f}</text></g>)}
    <g clipPath={`url(#${clipId})`}>
      {traces.map((t,index)=><g key={`${t.label}-${index}`}>
        {t.band&&<><path fill={t.color} fillOpacity="0.12" d={band(t,t.band.min,t.band.max)}/><path fill={t.color} fillOpacity="0.24" d={band(t,t.band.p10,t.band.p90)}/></>}
        <path fill="none" stroke={t.color} strokeWidth="2" d={curve(t)}/>
      </g>)}
    </g>
    <text x="395" y="306" textAnchor="middle">Frequency (Hz · log)</text><text x="14" y="22">dB</text>
  </svg><div className="legend">{traces.map((t,i)=><span key={`${t.label}-${i}`}><i style={{background:t.color}}/>{t.label}</span>)}</div>
    <div role="group" aria-label={`${label} zoom`} style={{display:"flex",gap:8,marginTop:12}}>
      <button type="button" className="secondary" aria-label="Zoom in chart" onClick={()=>setDomain(d=>zoomPlotDomain(d,base,.7))}>Zoom in</button>
      <button type="button" className="secondary" aria-label="Zoom out chart" onClick={()=>setDomain(d=>zoomPlotDomain(d,base,1/.7))}>Zoom out</button>
      <button type="button" className="secondary" onClick={()=>setDomain(base)}>Reset zoom</button>
    </div></>;
}
