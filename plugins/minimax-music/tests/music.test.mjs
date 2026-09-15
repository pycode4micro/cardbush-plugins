import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {loadConfig,safeError} from '../src/config.mjs';
import {validateArgs,buildRequest} from '../src/requests.mjs';
import {callApi,parseSSE,decodeHex,downloadAudio} from '../src/api.mjs';
import {runWorker,atomicJson,retryDownload,getJob} from '../src/jobs.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const scratch=process.env.MINIMAX_TEST_ROOT || path.join(os.tmpdir(),'minimax-music-tests');
fs.mkdirSync(scratch,{recursive:true});
const dir=fs.mkdtempSync(path.join(scratch,'run-'));
const cfg={backend:'cloud',key:'test-private-key',localKey:'',apiBase:'https://api.minimax.cn',localBase:'http://127.0.0.1:8000',timeoutSeconds:5,dataDir:path.join(dir,'data')};
const base={output_dir:dir};
// A valid, silent 16-bit PCM WAV fixture. No generated music or paid request.
const wav=Buffer.alloc(44+320);
wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(32000,24);wav.writeUInt32LE(64000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(320,40);
const jsonResponse=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json'}});

test('all official generation controls survive mapping',()=>{
  const a={...base,prompt:'暖心的动画主题曲',lyrics:'[Verse]\n风从远方来',model:'music-2.6',stream:true,output_format:'hex',audio_setting:{format:'mp3',sample_rate:24000,bitrate:64000}};
  validateArgs('song',a,cfg);
  const req=buildRequest('song',a,'cloud');
  assert.equal(req.endpoint,'/v1/music_generation');assert.equal(req.body.model,'music-2.6');assert.equal(req.body.lyrics,a.lyrics);assert.equal(req.body.stream,true);assert.deepEqual(req.body.audio_setting,a.audio_setting);assert.equal(req.body.is_instrumental,false);
  const instrumental=buildRequest('instrumental',{...base,prompt:'悬疑',aigc_watermark:true},'cloud');assert.equal(instrumental.body.is_instrumental,true);assert.equal(instrumental.body.aigc_watermark,true);
});
test('reject unsupported or contradictory cloud controls before any network call',()=>{
  for(const extra of [{duration_seconds:90},{seed:1},{model:'music-3.0-free'},{stream:true,output_format:'url'},{stream:true,aigc_watermark:true},{audio_setting:{format:'flac'}},{audio_setting:{sample_rate:48000}},{stream:'yes'},{invented:true}])assert.throws(()=>validateArgs('song',{...base,lyrics:'这是歌词',...extra},cfg));
  assert.throws(()=>validateArgs('song',{output_dir:'relative',lyrics:'歌词'},cfg));
  assert.throws(()=>validateArgs('song',{...base,prompt:'描述'},cfg));
  assert.doesNotThrow(()=>validateArgs('song',{...base,prompt:'描述',lyrics_optimizer:true},cfg));
  assert.throws(()=>validateArgs('instrumental',{...base,prompt:'配乐',lyrics:'人声'},cfg));
});
test('lyrics creation, title preservation and continuation map to official modes',()=>{
  assert.deepEqual(buildRequest('lyrics_write',{...base,title:'星海',prompt:'少年成长'},'cloud').body,{mode:'write_full_song',title:'星海',prompt:'少年成长'});
  assert.deepEqual(buildRequest('lyrics_edit',{...base,lyrics:'原来的歌词',prompt:'续写副歌'},'cloud').body,{mode:'edit',lyrics:'原来的歌词',prompt:'续写副歌'});
  assert.throws(()=>validateArgs('lyrics_edit',{...base,prompt:'续写'},cfg));
});
test('cover references are exclusive and local bytes are encoded',()=>{
  const audio_path=path.join(dir,'reference.wav');fs.writeFileSync(audio_path,wav);
  const a={...base,audio_path,prompt:'改编成温柔的钢琴爵士风格'};validateArgs('cover',a,cfg);
  const req=buildRequest('cover',a,'cloud');assert.equal(req.body.model,'music-cover');assert.deepEqual(Buffer.from(req.body.audio_base64,'base64'),wav);
  assert.throws(()=>validateArgs('cover',{...a,audio_url:'https://example.com/a.wav'},cfg));
  assert.throws(()=>validateArgs('cover',{...base,cover_feature_id:'feature',prompt:a.prompt},cfg));
  assert.doesNotThrow(()=>validateArgs('cover',{...base,cover_feature_id:'feature',prompt:a.prompt,lyrics:'[Verse]\n这是修改后的中文歌词'},cfg));
  assert.throws(()=>validateArgs('cover_preprocess',{...base,cover_feature_id:'feature'},cfg));
  assert.throws(()=>validateArgs('cover',{...base,audio_base64:'!!!?',prompt:a.prompt},cfg));
});
test('self-hosted mapping uses frame budget and never cloud parameters',()=>{
  const a={...base,backend:'local',prompt:'piano ballad',lyrics:'[Verse]\nThe sky is blue',duration_seconds:90,seed:42};
  assert.equal(validateArgs('song',a,cfg),'local');const req=buildRequest('song',a,'local');
  assert.equal(req.body.max_new_tokens,2250);assert.equal(req.body.seed,42);assert.equal(req.body.input,a.lyrics);assert.equal(req.body.stream,false);
  assert.throws(()=>validateArgs('song',{...a,lyrics_optimizer:true},cfg));
  assert.throws(()=>validateArgs('song',{...a,audio_setting:{format:'wav'}},cfg));
  assert.throws(()=>validateArgs('cover',{...base,backend:'local'},cfg));
});
test('cloud key is sent to the official API and provider errors are surfaced without retries',async()=>{
  let calls=0;
  const fetcher=async(url,options)=>{calls++;assert.equal(url,'https://api.minimax.cn/v1/lyrics_generation');assert.equal(options.headers.Authorization,'Bearer test-private-key');assert.equal(options.redirect,'error');return jsonResponse({base_resp:{status_code:1008,status_msg:'insufficient'}});};
  await assert.rejects(callApi(cfg,'cloud',{endpoint:'/v1/lyrics_generation',body:{mode:'write_full_song'}},null,fetcher),/1008/);assert.equal(calls,1);
  assert.equal(safeError(new Error('oops test-private-key Bearer abc'),cfg),'oops [REDACTED] Bearer [REDACTED]');
});
test('interruptions and incomplete responses are unknown rather than success',async()=>{
  const req={endpoint:'/v1/music_generation',body:{output_format:'hex'}};
  await assert.rejects(callApi(cfg,'cloud',req,null,async()=>{throw new Error('disconnect');}),e=>e.uncertain===true);
  await assert.rejects(callApi(cfg,'cloud',req,null,async()=>jsonResponse({data:{status:1,audio:'aabb'}})),e=>e.uncertain===true);
  assert.throws(()=>decodeHex('abc'));assert.throws(()=>decodeHex('zz'));assert.throws(()=>decodeHex(''));
});
test('SSE handles CRLF boundaries, terminal full audio and terminal deltas',async()=>{
  for(const completeAudio of ['ccdd','aabbccdd']) {
    const text=': keepalive\r\n\r\ndata: '+JSON.stringify({data:{audio:'aabb',status:1}})+'\r\n\r\ndata: '+JSON.stringify({data:{audio:completeAudio,status:2},base_resp:{status_code:0}})+'\r\n\r\ndata: [DONE]\r\n\r\n';
    const bytes=new TextEncoder().encode(text);
    const stream=new ReadableStream({start(c){for(let i=0;i<bytes.length;i+=7)c.enqueue(bytes.slice(i,i+7));c.close();}});
    const result=await parseSSE(new Response(stream));assert.equal(result.audio.toString('hex'),'aabbccdd');
  }
  await assert.rejects(parseSSE(new Response('data: {"data":{"audio":"aabb","status":1}}\n\n')),e=>e.uncertain===true);
});
test('download never forwards credentials or accepts HTML',async()=>{
  const audio=await downloadAudio('https://cdn.example/audio.wav',1,async(url,opts)=>{assert.equal(opts.headers,undefined);return new Response(wav,{headers:{'content-type':'audio/wav'}});});assert.deepEqual(audio,wav);
  await assert.rejects(downloadAudio('http://example.com/x',1,async()=>{}),/HTTPS/);
  await assert.rejects(downloadAudio('https://cdn.example/x',1,async()=>new Response('<html>',{headers:{'content-type':'text/html'}})),/text instead/);
});
test('worker saves lyrics and parsed cover structures',async()=>{
  for(const [op,args,raw,expected] of [
    ['lyrics_write',{prompt:'write a theme'},{lyrics:'[Verse]\n星光在远方',song_title:'星光',style_tags:'Pop'},'lyrics_path'],
    ['cover_preprocess',{audio_url:'https://example.com/original.wav'},{cover_feature_id:'abc123',formatted_lyrics:'[Verse]\n原歌词',structure_result:'[{"type":"verse","start":0,"end":12}]',audio_duration:60},'cover_feature_id']
  ]) {
    const id=crypto.randomUUID(),file=path.join(cfg.dataDir,'jobs',id+'.json'),output=path.join(dir,id);fs.mkdirSync(output);
    atomicJson(file,{job_id:id,operation:op,args:{...base,...args},backend:'cloud',status:'queued',output_dir:output});
    await runWorker(file,{cfg,callApi:async()=>({json:raw})});const job=getJob(cfg,id);assert.equal(job.status,'completed');assert.ok(job.result[expected]);
    if(op==='cover_preprocess')assert.equal(job.result.structure[0].end,12);
  }
});
test('download recovery persists provider success and does not generate twice',async()=>{
  const id=crypto.randomUUID(),file=path.join(cfg.dataDir,'jobs',id+'.json'),output=path.join(dir,id);fs.mkdirSync(output);
  atomicJson(file,{job_id:id,operation:'song',args:{...base,lyrics:'这是一段中文歌词',output_format:'url'},backend:'cloud',status:'queued',output_dir:output});
  let calls=0;await runWorker(file,{cfg,callApi:async()=>{calls++;return {json:{data:{status:2,audio:'https://cdn.example/song.wav'}}};},downloadAudio:async()=>{throw new Error('offline');}});
  assert.equal(getJob(cfg,id).status,'download_failed');
  const recovered=await retryDownload(cfg,id,async()=>wav);assert.equal(recovered.status,'completed');assert.equal(calls,1);assert.deepEqual(fs.readFileSync(recovered.result.audio_path),wav);
  await runWorker(file,{cfg,callApi:async()=>{calls++;}});assert.equal(calls,1);
});
test('real MCP handshake, tools, background local generation, reconnect and idempotency',async t=>{
  let generationCalls=0;
  const mock=http.createServer(async(req,res)=>{
    assert.equal(req.url,'/v1/audio/speech');assert.equal(req.headers.authorization,undefined);
    let text='';for await(const chunk of req)text+=chunk;const payload=JSON.parse(text);
    assert.equal(payload.model,'MiniMaxAI/MiniMax-Music3');assert.equal(payload.max_new_tokens,750);generationCalls++;
    res.writeHead(200,{'content-type':'audio/wav'});res.end(wav);
  });
  await new Promise(r=>mock.listen(0,'127.0.0.1',r));t.after(()=>mock.close());
  const env={...process.env,MINIMAX_MUSIC_DATA_DIR:path.join(dir,'mcp-data'),MINIMAX_MUSIC_CONFIG:path.join(dir,'absent-config.json'),MINIMAX_MUSIC_BACKEND:'local',MINIMAX_LOCAL_BASE_URL:`http://127.0.0.1:${mock.address().port}`,MINIMAX_API_KEY:'cloud-key-must-not-leak',MINIMAX_LOCAL_API_KEY:''};
  const connect=async()=>{const client=new Client({name:'test-client',version:'1.0.0'});const transport=new StdioClientTransport({command:process.execPath,args:[path.join(root,'scripts/minimax-music.mjs'),'serve'],env,stderr:'pipe'});await client.connect(transport);return client;};
  let client=await connect();t.after(async()=>{await client?.close();});
  const tools=await client.listTools();assert.equal(tools.tools.length,11);
  const capabilities=await client.callTool({name:'music_capabilities',arguments:{}});assert.equal(capabilities.structuredContent.configured_backend,'local');assert.equal(JSON.stringify(capabilities).includes('cloud-key-must-not-leak'),false);
  const request={...base,backend:'local',prompt:'Quiet piano, no voice',duration_seconds:30,request_id:'integration-one'};
  const started=await client.callTool({name:'generate_instrumental',arguments:request});assert.equal(started.isError,false);const id=started.structuredContent.job_id;assert.ok(id);
  await client.close();client=await connect();
  let finished;for(let i=0;i<100;i++){finished=await client.callTool({name:'get_music_job',arguments:{job_id:id}});if(!['queued','running'].includes(finished.structuredContent.status))break;await new Promise(r=>setTimeout(r,100));}
  assert.equal(finished.structuredContent.status,'completed',JSON.stringify(finished));assert.deepEqual(fs.readFileSync(finished.structuredContent.result.audio_path),wav);assert.ok(finished.content.some(c=>c.type==='resource_link'));
  const duplicate=await client.callTool({name:'generate_instrumental',arguments:request});assert.equal(duplicate.structuredContent.job_id,id);assert.equal(duplicate.structuredContent.deduplicated,true);assert.equal(generationCalls,1);
  const conflict=await client.callTool({name:'generate_instrumental',arguments:{...request,prompt:'different'}});assert.equal(conflict.isError,true);assert.equal(generationCalls,1);
  const invalid=await client.callTool({name:'generate_song',arguments:{...base,backend:'cloud',lyrics:'歌词',duration_seconds:30}});assert.equal(invalid.isError,true);
});
