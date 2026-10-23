import { resolve } from "node:path";
import page from "./envelope.html";
import {sameOrigin} from "./src/contracts";
import {chartSchema,parseCallback,parseCursor,viewerURL} from "./src/envelope-contracts";

const directory=process.env.ENVELOPE_STUDY;
if(!directory)throw new Error("Set ENVELOPE_STUDY to the explicit Rust-exported study directory");
const port=Number(process.env.PORT??8096);
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error("Invalid PORT");
const root=resolve(directory),origin=`http://127.0.0.1:${port}`;
async function boundedFile(name:string,limit:number):Promise<string>{
  const file=Bun.file(resolve(root,name));
  if(file.size>limit)throw new Error(`${name} exceeds study size limit`);
  const text=await file.text();if(text.length>limit)throw new Error(`${name} exceeds study size limit`);
  return text;
}
const chart=chartSchema.parse(JSON.parse(await boundedFile("chart.json",65536)));
const json=(value:unknown,status=200)=>Response.json(value,{status,headers:{"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}});
async function api(request:Request):Promise<Response>{
  if(!sameOrigin(request,origin))return json({error:"Origin rejected"},403);
  if(request.method!=="GET")return json({error:"Read-only study"},405);
  try{
    const url=new URL(request.url);
    if(url.pathname==="/api/study"){
      if(url.search)return json({error:"Unexpected query"},400);
      const viewer=JSON.parse(await boundedFile("viewer.json",65536));
      return json({chart,viewer_url:viewerURL(viewer.viewer_url)});
    }
    if([...url.searchParams.keys()].some(key=>key!=="after")||url.searchParams.getAll("after").length>1)throw new Error("Unexpected callback query");
    const after=parseCursor(url.searchParams.get("after"));
    const text=await boundedFile("callbacks.jsonl",64*1024*1024),lines=text.split("\n");
    lines.pop(); // The final partial line is not an emitted callback yet.
    if(lines.length>10000||after>lines.length)throw new Error("Callback history exceeds limit or was replaced; reload the page");
    const callbacks=lines.slice(after).map(line=>parseCallback(JSON.parse(line),chart));
    return json({callbacks,next:lines.length});
  }catch(error){return json({error:error instanceof Error?error.message:"Invalid study export"},400);}
}
Bun.serve({hostname:"127.0.0.1",port,maxRequestBodySize:1024,development:{hmr:true,console:true},
  routes:{"/":page,"/api/study":api,"/api/callbacks":api},fetch(){return new Response("Not found",{status:404});}});
console.log(`Solver response study: ${origin}; read-only; chat disabled`);
