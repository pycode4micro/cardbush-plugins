import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {loadConfig, requireAccess, safeError} from './config.mjs';
import {validateArgs, buildRequest} from './requests.mjs';
import {callApi, downloadAudio} from './api.mjs';

export function atomicJson(file, data) {
  fs.mkdirSync(path.dirname(file),{recursive:true,mode:0o700});
  const tmp = `${file}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(tmp,JSON.stringify(data,null,2)+'\n',{mode:0o600});
  fs.renameSync(tmp,file);
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
  return value;
}
function jobFile(cfg,id) {
  if (!/^[0-9a-f]{8}-[0-9a-f-]{27}$/.test(id)) throw new Error('Invalid job_id');
  return path.join(cfg.dataDir,'jobs',id+'.json');
}
function publicJob(job) {
  const {args,fingerprint,...safe} = job;
  return safe;
}
export function getJob(cfg,id) {
  const file = jobFile(cfg,id);
  const job = JSON.parse(fs.readFileSync(file,'utf8'));
  if (['queued','running'].includes(job.status) && Date.now()-Date.parse(job.updated_at)>10000) {
    let live = false;
    if(job.pid) try {process.kill(job.pid,0); live=true;} catch(e) {live=e.code==='EPERM';}
    if(!live) {
      job.status='unknown'; job.error='Worker stopped before recording a terminal result. Do not automatically submit another paid generation.';
      job.updated_at=new Date().toISOString(); atomicJson(file,job);
    }
  }
  return publicJob(job);
}
export function listJobs(cfg,limit=20) {
  if(!Number.isInteger(limit)||limit<1||limit>100) throw new Error('limit must be 1..100');
  const dir=path.join(cfg.dataDir,'jobs');
  if(!fs.existsSync(dir)) return {jobs:[]};
  const jobs=fs.readdirSync(dir).filter(n=>/^[0-9a-f-]{36}\.json$/.test(n)).map(n=>JSON.parse(fs.readFileSync(path.join(dir,n),'utf8')));
  jobs.sort((a,b)=>b.created_at.localeCompare(a.created_at));
  return {jobs:jobs.slice(0,limit).map(j=>getJob(cfg,j.job_id))};
}
export function startJob(op,args,cfg,entrypoint) {
  const backend=validateArgs(op,args,cfg);
  requireAccess(cfg,backend);
  const normalized={...args,backend};
  const fingerprint=crypto.createHash('sha256').update(JSON.stringify(canonical({op,args:normalized}))).digest('hex');
  const jobsDir=path.join(cfg.dataDir,'jobs');
  fs.mkdirSync(jobsDir,{recursive:true,mode:0o700});
  const id=crypto.randomUUID();
  if(args.request_id) {
    const reservation=path.join(jobsDir,'request-'+crypto.createHash('sha256').update(args.request_id).digest('hex')+'.json');
    try {fs.writeFileSync(reservation,JSON.stringify({job_id:id,fingerprint}),{flag:'wx',mode:0o600});}
    catch(e) {
      if(e.code!=='EEXIST') throw e;
      const prior=JSON.parse(fs.readFileSync(reservation,'utf8'));
      if(prior.fingerprint!==fingerprint) throw new Error('request_id already exists with different arguments. Use a new request_id only for an intentional new generation.');
      return {...getJob(cfg,prior.job_id),deduplicated:true};
    }
  }
  const title=(args.title || op).normalize('NFKC').replace(/[^\p{L}\p{N}_-]+/gu,'-').slice(0,40)||op;
  const output=path.join(args.output_dir,`${title}-${id}`);
  const file=jobFile(cfg,id);
  const job={job_id:id,operation:op,backend,status:'queued',title:args.title||op,created_at:new Date().toISOString(),updated_at:new Date().toISOString(),output_dir:output,args:normalized,fingerprint};
  atomicJson(file,job);
  try {
    fs.mkdirSync(args.output_dir,{recursive:true});
    fs.mkdirSync(output,{recursive:false});
    const child=spawn(process.execPath,[entrypoint,'worker',file],{detached:true,windowsHide:true,stdio:'ignore',env:process.env});
    child.on('error',e=>{
      const latest=JSON.parse(fs.readFileSync(file,'utf8')); latest.status='failed';latest.error=safeError(e,cfg);atomicJson(file,latest);
    });
    child.unref();
    // The worker owns subsequent writes; don't race it by updating the job here.
    return {...publicJob(job),poll_after_seconds:5};
  } catch(e) {job.status='failed';job.error=safeError(e,cfg);atomicJson(file,job);throw e;}
}
function smallResponse(json) {
  const result=structuredClone(json);
  if(result.data?.audio && !/^https:\/\//.test(result.data.audio)) result.data.audio='[saved as audio file]';
  return result;
}
function saveBytes(file,bytes) {
  if(!bytes.length) throw new Error('Refusing to save empty audio');
  const tmp=file+'.partial';
  fs.writeFileSync(tmp,bytes,{flag:'wx',mode:0o600});
  if(fs.existsSync(file)) throw new Error('Output already exists; it will not be overwritten');
  fs.renameSync(tmp,file);
}
export async function runWorker(file,dependencies={}) {
  const cfg=dependencies.cfg||loadConfig();
  const job=JSON.parse(fs.readFileSync(file,'utf8'));
  // A worker is single-use: never re-run a sent generation after a crash.
  const lock=file+'.lock';
  try {fs.writeFileSync(lock,String(process.pid),{flag:'wx',mode:0o600});} catch(e) {if(e.code==='EEXIST') return;throw e;}
  const update=(patch)=>{Object.assign(job,patch,{updated_at:new Date().toISOString()});atomicJson(file,job);};
  update({status:'running',pid:process.pid,phase:'preparing'});
  try {
    validateArgs(job.operation,job.args,cfg); requireAccess(cfg,job.backend);
    const req=buildRequest(job.operation,job.args,job.backend);
    const audit=structuredClone(req);
    if(audit.body.audio_base64) audit.body.audio_base64='[reference audio omitted]';
    atomicJson(path.join(job.output_dir,'request.json'),{operation:job.operation,backend:job.backend,...audit});
    update({phase:'generating'});
    let lastProgress=0;
    const response=await (dependencies.callApi||callApi)(cfg,job.backend,req,async progress=>{
      if(Date.now()-lastProgress>2000) {lastProgress=Date.now();update({progress});}
    });
    const raw=response.json;
    const result={job_id:job.job_id,operation:job.operation,backend:job.backend,provider_response:smallResponse(raw)};
    if(job.operation.startsWith('lyrics_')) {
      if(typeof raw.lyrics!=='string'||!raw.lyrics.trim()) throw new Error('MiniMax did not return lyrics');
      result.lyrics=raw.lyrics; result.song_title=raw.song_title;result.style_tags=raw.style_tags;
      result.lyrics_path=path.join(job.output_dir,'lyrics.txt');
      fs.writeFileSync(result.lyrics_path,raw.lyrics,{flag:'wx'});
    } else if(job.operation==='cover_preprocess') {
      if(typeof raw.cover_feature_id!=='string'||!raw.cover_feature_id) throw new Error('MiniMax did not return cover_feature_id');
      result.cover_feature_id=raw.cover_feature_id;result.formatted_lyrics=raw.formatted_lyrics;result.audio_duration=raw.audio_duration;
      result.feature_expires_at=new Date(Date.now()+24*3600000).toISOString();
      if(typeof raw.structure_result==='string') {try {result.structure=JSON.parse(raw.structure_result);} catch {result.structure_raw=raw.structure_result;}}
      else if(raw.structure_result!==undefined) result.structure=raw.structure_result;
      if(typeof raw.formatted_lyrics==='string') {result.lyrics_path=path.join(job.output_dir,'lyrics.txt');fs.writeFileSync(result.lyrics_path,raw.formatted_lyrics,{flag:'wx'});}
    } else {
      result.format=job.backend==='local' ? 'wav':req.body.audio_setting.format;
      result.sample_rate=raw.extra_info?.music_sample_rate||(job.backend==='local'?32000:req.body.audio_setting.sample_rate);
      result.duration_ms=raw.extra_info?.music_duration;
      result.audio_path=path.join(job.output_dir,'audio.'+result.format);
      let audio=response.audio;
      if(!audio && req.body.output_format==='url') {
        result.audio_url=raw.data?.audio;
        if(typeof result.audio_url!=='string') throw new Error('MiniMax did not return an audio URL');
        result.url_expires_at=new Date(Date.now()+24*3600000).toISOString();
        update({phase:'downloading',result});
        // Save the successful generation before any download that may fail.
        atomicJson(path.join(job.output_dir,'result.json'),result);
        try {audio=await (dependencies.downloadAudio||downloadAudio)(result.audio_url);}
        catch(e) {update({status:'download_failed',phase:'download_failed',result,error:safeError(e,cfg),recovery:'Call download_result with this job_id; do not regenerate.'});return;}
      }
      if(!audio) throw new Error('MiniMax returned no audio');
      saveBytes(result.audio_path,audio);
      result.audio_bytes=audio.length;
    }
    result.completed_at=new Date().toISOString();
    atomicJson(path.join(job.output_dir,'result.json'),result);
    update({status:'completed',phase:'completed',result});
  } catch(e) {update({status:e.uncertain?'unknown':'failed',phase:'error',error:safeError(e,cfg),error_code:e.code,automatic_retry:false});}
}
export async function retryDownload(cfg,id,downloader=downloadAudio) {
  const file=jobFile(cfg,id);const job=JSON.parse(fs.readFileSync(file,'utf8'));
  if(job.status==='completed') return publicJob(job);
  if(job.status!=='download_failed'||!job.result?.audio_url) throw new Error('Only download_failed jobs can retry their download');
  const lock=file+'.download-lock';
  const handle=fs.openSync(lock,'wx',0o600);
  try {
    if(fs.existsSync(job.result.audio_path)) throw new Error('Audio already exists; refusing to overwrite');
    const bytes=await downloader(job.result.audio_url);
    saveBytes(job.result.audio_path,bytes);
    job.result.audio_bytes=bytes.length;job.result.completed_at=new Date().toISOString();
    atomicJson(path.join(job.output_dir,'result.json'),job.result);
    job.status='completed';job.phase='completed';job.updated_at=new Date().toISOString();delete job.error;delete job.recovery;
    atomicJson(file,job);return publicJob(job);
  } finally {fs.closeSync(handle);fs.unlinkSync(lock);}
}
