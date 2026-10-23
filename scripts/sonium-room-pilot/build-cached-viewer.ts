// Development-only source harness: reuse complete cached SDK artifacts, never rebuild Rerun.
import { readdir, readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
const root=resolve(import.meta.dir,"../.."),target=resolve(root,"target/lean-rerun/debug"),output=process.argv[2];
if(!output)throw Error("usage: bun build-cached-viewer.ts NEW_BINARY_PATH");
const log=await readFile(resolve(root,"target/qa/visionplan-sonium-trial-20261023/core-build.jsonl"),"utf8");
const artifacts=log.split("\n").flatMap(line=>{try{return [JSON.parse(line)]}catch{return []}});
const args=["--edition=2024","--cfg",'feature="viewer"',"-C","opt-level=1","-C","debuginfo=0","-L",`dependency=${target}/deps`];
for(const name of ["serde","serde_json","sha2","sonium_solvers_sparse"]){const a=artifacts.findLast(a=>a.reason==="compiler-artifact"&&a.target.name===name&&a.filenames.some((f:string)=>f.endsWith(".rlib")));const file=a?.filenames.find((f:string)=>f.endsWith(".rlib"));if(!file)throw Error(`Missing cached direct dependency ${name}`);args.push("--extern",`${name}=${file}`);}
const candidates=[];
const version=Bun.TOML.parse(await Bun.file(resolve(import.meta.dir,"Cargo.toml")).text()).dependencies.rerun.version as string;
for(const file of await readdir(`${target}/deps`)){if(/^librerun-.*\.rlib$/.test(file)){const hash=file.slice("librerun-".length,-".rlib".length);try{const metadata=JSON.parse(await readFile(`${target}/.fingerprint/rerun-${hash}/lib-rerun.json`,"utf8"));if(metadata.features.includes('"web_viewer"'))candidates.push({file:`${target}/deps/${file}`,mtime:(await stat(`${target}/deps/${file}`)).mtimeMs});}catch{}}}
for(let i=candidates.length-1;i>=0;i--){const hash=candidates[i].file.split("librerun-")[1].slice(0,-5);const source=await Bun.file(`${target}/deps/rerun-${hash}.d`).text().catch(()=>"");const cached=source.match(/\/rerun-(\d+\.\d+\.\d+)\//)?.[1];if(!cached||!Bun.semver.satisfies(cached,`^${version}`))candidates.splice(i,1);}
candidates.sort((a,b)=>b.mtime-a.mtime);if(!candidates[0])throw Error(`No cached Rerun ${version} SDK/web viewer library; use normal Cargo viewer feature when storage permits`);
args.push("--extern",`rerun=${candidates[0].file}`,"-o",resolve(output),resolve(import.meta.dir,"src/main.rs"));
console.log(`Reusing ${candidates[0].file.split("/").at(-1)}; no dependency compilation`);
const child=Bun.spawn(["rustc",...args],{cwd:root,stdout:"inherit",stderr:"inherit"});process.exit(await child.exited);
