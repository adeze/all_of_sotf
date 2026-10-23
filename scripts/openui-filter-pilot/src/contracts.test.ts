import { describe, expect, test } from "bun:test";
import { acceptStudyPoll, commandSchema, comparisonSchema, liveResponseSchema, loopbackWorkbenchURL, sameOrigin, snapshotSchema } from "./contracts";

describe("OpenUI boundary", () => {
  test("only explicit loopback endpoint and same-origin browser calls", () => {
    expect(loopbackWorkbenchURL("http://127.0.0.1:8795/workbench").port).toBe("8795");
    for (const url of ["https://example.com/workbench","http://127.0.0.1:8795/transfer",
      "http://secret@127.0.0.1:8795/workbench","http://127.0.0.1:8795/workbench?token=x"]) {
      expect(()=>loopbackWorkbenchURL(url)).toThrow();
    }
    const origin="http://127.0.0.1:8094";
    expect(sameOrigin(new Request(`${origin}/api/designer`),origin)).toBe(true);
    expect(sameOrigin(new Request(`${origin}/api/designer`,{headers:{Origin:"https://evil.example"}}),origin)).toBe(false);
    expect(sameOrigin(new Request("http://evil.example/api/designer"),origin)).toBe(false);
  });
  test("commands remain bounded, revision-bound and offline", () => {
    const command={operation:"update_designer",expected_revision:"revision-1",patch:{min_freq:20,max_freq:300,max_cut_db:12,max_boost_db:0}};
    expect(commandSchema.safeParse(command).success).toBe(true);
    expect(commandSchema.safeParse({operation:"run_study",expected_revision:"revision-1"}).success).toBe(true);
    expect(commandSchema.safeParse({operation:"cancel_study",expected_revision:"revision-1"}).success).toBe(true);
    for (const invalid of [{...command,expected_revision:""},{...command,patch:{...command.patch,min_freq:400}},
      {...command,patch:{...command.patch,max_boost_db:-1}},{...command,patch:{...command.patch,gain:3}},
      {operation:"run_study"},{operation:"transfer"}]) {
      expect(commandSchema.safeParse(invalid).success).toBe(false);
    }
  });
  test("response grids cannot silently mismatch or exceed native Nyquist", () => {
    const channel={name:"FL",sample_rate_hz:48000,frequency_hz:[20,20000],response_db:[0,-3]};
    const candidate={id:"test-only",name:"Synthetic contract fixture",kind:"serialized_electrical_fir",source_digest:"fixture",channels:[channel]};
    expect(comparisonSchema.safeParse({candidates:[candidate,{...candidate,id:"test-2"}]}).success).toBe(true);
    for (const patch of [{response_db:[0]},{frequency_hz:[20000,20]},{sample_rate_hz:6000}]) {
      expect(comparisonSchema.safeParse({candidates:[candidate,{...candidate,channels:[{...channel,...patch}]}]}).success).toBe(false);
    }
  });
  test("live callback responses preserve their shared grid and provenance fields", () => {
    const response={channel:"L",frequency_hz:[20,100],prepared_measurement_db:[-2,3],target_db:[0,0],filter_db:[0,-1],predicted_db:[-2,2]};
    expect(liveResponseSchema.parse(response)).toEqual(response);
    for(const change of [{predicted_db:[0]},{frequency_hz:[100,20]},{filter_db:[NaN,0]}]) {
      expect(liveResponseSchema.safeParse({...response,...change}).success).toBe(false);
    }
    const current=snapshotSchema.parse({revision:"r1",designer:{min_freq:20,max_freq:300,max_boost_db:0,max_cut_db:12},chat_enabled:false,offline_only:true,
      study:{id:"study-2",status:"completed",results_current:true,callback_count:10}});
    expect(acceptStudyPoll(current,{...current,study:{...current.study!,status:"running"}})).toBe(current);
    expect(acceptStudyPoll(current,{...current,study:{...current.study!,id:"study-1"}})).toBe(current);
    expect(acceptStudyPoll(current,{...current,study:{...current.study!,callback_count:9}})).toBe(current);
  });
});
