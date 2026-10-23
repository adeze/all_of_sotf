// Development source harness: use existing compiler-compatible libraries, never compile dependencies.
import { readdir, stat, access } from "node:fs/promises";
import { resolve } from "node:path";
const root=resolve(import.meta.dir,"../.."), deps=resolve(root,"target/lean-rerun/debug/deps");
const output=process.argv[2];
if(!output)throw Error("usage: bun build-cached.ts NEW_BINARY [--test]");
try{await access(output);throw Error("Refusing to overwrite existing binary");}catch(e){if(e.code!=="ENOENT")throw e;}
const args=["--edition=2024","-C","opt-level=1","-C","debuginfo=0","-L",`dependency=${deps}`];
let optimizerFingerprint="";
for(const name of ["autoeq_optim","rerun","serde_json","sha2","ndarray","anyhow","tempfile"]){
  const files=[];
  for(const file of await readdir(deps))if(new RegExp(`^lib${name}-[a-f0-9]+\\.rlib$`).test(file))files.push({file,modified:(await stat(`${deps}/${file}`)).mtimeMs});
  files.sort((a,b)=>b.modified-a.modified);
  if(name==="rerun"){
    const version=Bun.TOML.parse(await Bun.file(resolve(import.meta.dir,"Cargo.toml")).text()).dependencies.rerun.version as string;
    for(let i=files.length-1;i>=0;i--){
      const hash=files[i].file.slice("librerun-".length,-5);
      const source=await Bun.file(resolve(deps,`rerun-${hash}.d`)).text().catch(()=>"");
      const cached=source.match(/\/rerun-(\d+\.\d+\.\d+)\//)?.[1];
      if(!cached||!Bun.semver.satisfies(cached,`^${version}`))files.splice(i,1);
    }
  }
  if(name==="serde_json"||name==="ndarray"){
    const expected=optimizerFingerprint.match(new RegExp(`\\[\\d+,"${name}",false,(\\d+)\\]`))?.[1];
    if(!expected)throw Error(`Cached optimizer has no ${name} fingerprint`);
    for(let i=files.length-1;i>=0;i--){
      const hash=files[i].file.slice(`lib${name}-`.length,-5);
      const text=await Bun.file(resolve(deps,`../.fingerprint/${name}-${hash}/lib-${name}`)).text().catch(()=>"");
      if(!text||BigInt("0x"+text.match(/../g)!.reverse().join("")).toString()!==expected)files.splice(i,1);
    }
  }
  if(!files[0])throw Error(`Missing cached ${name}; no dependency build attempted`);
  if(name==="autoeq_optim"){
    const hash=files[0].file.slice("libautoeq_optim-".length,-5);
    optimizerFingerprint=await Bun.file(resolve(deps,`../.fingerprint/autoeq-optim-${hash}/lib-autoeq_optim.json`)).text();
  }
  args.push("--extern",`${name}=${deps}/${files[0].file}`);
  console.log(`Using ${files[0].file}`);
}
if(process.argv.includes("--test"))args.push("--test");
args.push(resolve(import.meta.dir,"src/main.rs"),"-o",resolve(output));
const child=Bun.spawn(["rustc",...args],{cwd:root,stdout:"inherit",stderr:"inherit"});
process.exit(await child.exited);
