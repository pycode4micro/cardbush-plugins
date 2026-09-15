import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {Server} from '@modelcontextprotocol/sdk/server/index.js';
import {StdioServerTransport} from '@modelcontextprotocol/sdk/server/stdio.js';
import {CallToolRequestSchema,ListToolsRequestSchema} from '@modelcontextprotocol/sdk/types.js';
import {loadConfig,safeError,configPath,ACCESS_NOTICE} from './config.mjs';
import {TOOLS,invoke} from './tools.mjs';
import {runWorker,atomicJson} from './jobs.mjs';

const entrypoint=fileURLToPath(import.meta.url);
function renderResult(data) {
  const content=[{type:'text',text:JSON.stringify(data,null,2)}];
  const audio=data.status==='completed'?data.result?.audio_path:null;
  if(audio&&fs.existsSync(audio)) content.push({type:'resource_link',uri:pathToFileURL(audio).href,name:path.basename(audio),mimeType:data.result.format==='mp3'?'audio/mpeg':data.result.format==='wav'?'audio/wav':'application/octet-stream',description:'Generated audio saved on this computer'});
  return {content,structuredContent:data,isError:false};
}
async function serve() {
  const server=new Server({name:'minimax-music',version:'1.0.0'},{capabilities:{tools:{}}});
  server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:TOOLS}));
  server.setRequestHandler(CallToolRequestSchema,async request=>{
    let cfg;
    try {cfg=loadConfig();return renderResult(await invoke(request.params.name,request.params.arguments||{},cfg,entrypoint));}
    catch(e) {return {content:[{type:'text',text:JSON.stringify({error:safeError(e,cfg),automatic_retry:false})}],isError:true};}
  });
  await server.connect(new StdioServerTransport());
}
async function setup(args) {
  const opts={};for(let i=0;i<args.length;i+=2) {if(!args[i]?.startsWith('--')||args[i+1]===undefined)throw new Error('setup expects --option value pairs');opts[args[i].slice(2)]=args[i+1];}
  for(const k of Object.keys(opts))if(!['config','region','backend','key-file','local-url'].includes(k))throw new Error('Unknown setup option: '+k);
  const file=path.resolve(opts.config||configPath());
  const cfg=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):{};
  if(opts.region)cfg.region=opts.region;if(opts.backend)cfg.backend=opts.backend;
  if(opts['key-file'])cfg.apiKeyFile=path.resolve(opts['key-file']);
  if(opts['local-url'])cfg.localBaseUrl=opts['local-url'];
  // Validate before replacing any existing configuration.
  const tmp=file+'.validate-'+process.pid;atomicJson(tmp,cfg);
  try {loadConfig({...process.env,MINIMAX_MUSIC_CONFIG:tmp});}finally{fs.unlinkSync(tmp);}
  atomicJson(file,cfg);
  return {config_path:file,backend:cfg.backend||'cloud',region:cfg.region||'cn',message:'No API key was printed or stored in this config. Use MINIMAX_API_KEY or the configured private key file.',access_notice:ACCESS_NOTICE};
}
async function main() {
  const [cmd='serve',...args]=process.argv.slice(2);
  if(cmd==='serve')return serve();
  if(cmd==='worker')return runWorker(path.resolve(args[0]));
  if(cmd==='setup')return console.log(JSON.stringify(await setup(args),null,2));
  if(cmd==='call') {
    const [name,requestFile]=args;
    const a=requestFile?JSON.parse(fs.readFileSync(requestFile,'utf8').replace(/^\uFEFF/,'')):{};
    return console.log(JSON.stringify(await invoke(name,a,loadConfig(),entrypoint),null,2));
  }
  if(cmd==='doctor')return console.log(JSON.stringify(await invoke('music_capabilities',{},loadConfig(),entrypoint),null,2));
  if(cmd==='help'||cmd==='--help')return console.log('MiniMax Music\n  serve                     Start stdio MCP server\n  doctor                    Check configuration without an API call\n  call TOOL [request.json]   Run a tool using a UTF-8 JSON file\n  setup --backend cloud --region cn --key-file ABSOLUTE_PATH\n  setup --backend local --local-url http://127.0.0.1:8000\nOptional setup --config ABSOLUTE_PATH; use MINIMAX_MUSIC_CONFIG for a custom path.');
  throw new Error('Unknown command. Run help.');
}
main().catch(e=>{let cfg;try{cfg=loadConfig();}catch{}process.stderr.write(safeError(e,cfg)+'\n');process.exitCode=1;});
