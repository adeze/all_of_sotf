import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ResponsePlot, responsePlotFrame, zoomPlotDomain, type Trace } from "./response-plot";

const domain={minHz:20,maxHz:20000,minDb:-80,maxDb:10};
const trace:Trace={label:"L",color:"#2468ac",frequency:[20,200,2000,20000],db:[-20,-30,-40,-50],
  band:{min:[-30,-40,-50,-60],max:[-10,-20,-30,-40],p10:[-25,-35,-45,-55],p90:[-15,-25,-35,-45]}};

test("zoom changes logarithmic domains within fixed bounds; callback values keep viewport identity",()=>{
  const zoomed=zoomPlotDomain(domain,domain,.5);
  expect(Math.log(zoomed.maxHz/zoomed.minHz)).toBeCloseTo(Math.log(1000)/2,12);
  expect(zoomed.maxDb-zoomed.minDb).toBe(45);
  const restored=zoomPlotDomain(zoomed,domain,100);
  expect(restored.minHz).toBeCloseTo(domain.minHz,10);
  expect(restored.maxHz).toBeCloseTo(domain.maxHz,8);
  expect(restored.minDb).toBe(domain.minDb);
  expect(restored.maxDb).toBe(domain.maxDb);
  const next={...trace,db:trace.db.map(v=>v+100)};
  expect(responsePlotFrame([trace])!.key).toBe(responsePlotFrame([next])!.key);
  expect(responsePlotFrame([trace])!.key).not.toBe(responsePlotFrame([{...trace,label:"R"}])!.key);
  expect(responsePlotFrame([trace],false,domain)!.key).not.toBe(responsePlotFrame([trace],false,{...domain,minDb:-90})!.key);
  expect(()=>responsePlotFrame([trace],false,{...domain,minHz:0})).toThrow("Invalid response chart domain");
});

test("fixed SVG clip contains curves and both filled bands; invalid samples cannot produce NaN paths",()=>{
  const markup=renderToStaticMarkup(<ResponsePlot label="Measured responses" domain={domain} traces={[trace,{...trace,label:"R",color:"#f80",db:[-90,NaN,20,-40]}]}/>);
  expect(markup).toContain('viewBox="0 0 780 310"');
  expect(markup).toContain('<rect x="50" y="40" width="690" height="220"');
  expect(markup).toContain('clip-path="url(');
  expect(markup.match(/fill-opacity="0.12"/g)).toHaveLength(2);
  expect(markup.match(/fill-opacity="0.24"/g)).toHaveLength(2);
  expect(markup).not.toContain("NaN");
  expect(markup).toContain("Reset zoom");
});
