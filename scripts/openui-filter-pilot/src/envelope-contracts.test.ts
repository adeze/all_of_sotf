import {test,expect} from "bun:test";
import {chartSchema,parseCallback,parseCursor,viewerURL} from "./envelope-contracts";

test("bounded study contracts reject invalid cursors, grids, envelopes and nonlocal viewers",()=>{
  const chart=chartSchema.parse({domain:{minHz:20,maxHz:20000,minDb:-80,maxDb:10},channels:["L"],target_db:-25,smoothing_modes:["raw"],rerun_version:"0.40.0",transfer_prohibited:true});
  const value={channel:"L",generation:0,callback_index:0,loss:1,frequency_hz:[20,40],best_response_db:[-20,-30],display_best_db:{raw:[-20,-30]},envelopes:{raw:{min:[-30,-40],max:[-10,-20],p10:[-28,-38],p90:[-12,-22],count:24}},terminal:null};
  expect(parseCallback(value,chart).generation).toBe(0);expect(parseCursor("10000")).toBe(10000);
  expect(parseCallback({...value,terminal:{success:true,message:"Converged",finalized:true}},chart).terminal?.finalized).toBe(true);
  expect(()=>parseCallback({...value,terminal:false},chart)).toThrow();
  for(const cursor of ["-1","1.5","10001","01","x"])expect(()=>parseCursor(cursor)).toThrow();
  expect(()=>parseCallback({...value,frequency_hz:[40,20]},chart)).toThrow();
  expect(()=>parseCallback({...value,envelopes:{raw:{...value.envelopes.raw,p10:[0,0]}}},chart)).toThrow();
  expect(viewerURL("http://127.0.0.1:9093/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9876%2Fproxy")).toContain("127.0.0.1");
  expect(()=>viewerURL("https://example.com/")).toThrow();
});
