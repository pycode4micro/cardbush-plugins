import { promises as fs } from 'node:fs';
import path from 'node:path';
import { Store, immutableWrite, sha256 } from './store.mjs';
import { id, renderSvg, findingSchema } from './model.mjs';
import { template } from './templates.mjs';
import { raster, detailSvg, overviewSvg } from './render.mjs';

async function putOnce(filename,value){try{await immutableWrite(filename,value);}catch(error){if(error.code!=='EEXIST')throw error;}}
async function readOptional(filename){try{return JSON.parse(await fs.readFile(filename,'utf8'));}catch(error){if(error.code==='ENOENT')return null;throw error;}}
export class GarmentService{
  constructor(root){this.store=new Store(root);}
  async create({scene,template:base='shirt',title='我的衣服设计',brief=''}){return this.store.create(scene||template(base,title,brief));}
  async history(projectId){const revisions=await this.store.revisions(projectId);return Promise.all(revisions.map(async revision=>{const doc=await this.store.get(projectId,revision);return {revision,createdAt:doc.createdAt,note:doc.note};}));}
  async reference(projectId,revision,filename,description,partId){
    const doc=await this.store.get(projectId,revision);
    if(partId&&!doc.scene.parts.some(p=>p.id===partId))throw new Error('Reference targets an unknown part.');
    const asset=await this.store.importImage(projectId,filename);
    return this.store.commit(projectId,revision,d=>{
      if(d.references.length>=8)throw new Error('最多保留 8 张参考图。请先移除不需要的参考图。');
      return {...d,references:[...d.references,{...asset,description,partId}]};
    },'添加参考图');
  }
  async preview(projectId,revision,view='front',partId){
    const doc=await this.store.get(projectId,revision);
    const svg=partId?await detailSvg(doc.scene,partId):view==='overview'?overviewSvg(doc.scene):renderSvg(doc.scene,{view});
    return {doc,svg,png:await raster(svg,partId?768:1200)};
  }
  jobDir(projectId,jobId){return path.join(this.store.projectDir(projectId),'generations',id.parse(jobId));}
  async jobs(projectId){
    const names=await fs.readdir(path.join(this.store.projectDir(projectId),'generations')).catch(error=>{if(error.code==='ENOENT')return [];throw error;});
    const jobs=[];
    for(const name of names){if(!id.safeParse(name).success)continue;const job=await readOptional(path.join(this.jobDir(projectId,name),'job.json'));if(job)jobs.push(await this.status(projectId,name));}
    return jobs.sort((a,b)=>b.createdAt.localeCompare(a.createdAt));
  }
  async prepare(projectId,revision,requestId,confirmed){
    if(confirmed!==true)throw new Error('请先让用户确认当前设计版本，再准备效果图。');
    id.parse(requestId);
    const current=await this.store.get(projectId);
    if(current.revision!==revision)throw new Error(`设计已更新到 v${current.revision}，请重新确认后生成。`);
    const jobId='gen-'+sha256(JSON.stringify([projectId,revision,requestId])).slice(0,24),dir=this.jobDir(projectId,jobId);
    const previous=await readOptional(path.join(dir,'job.json'));if(previous)return this.status(projectId,jobId);
    const parts=current.scene.parts.filter(p=>p.visible);
    if(!parts.length)throw new Error('没有可见部位，无法生成效果图。');
    const materials=[];
    const save=async(name,svg,width=1200)=>{
      await putOnce(path.join(dir,name+'.svg'),svg);
      const filename=path.join(dir,name+'.png');await putOnce(filename,await raster(svg,width));return filename;
    };
    const overview=await save('overview',overviewSvg(current.scene),1600);
    const viewFiles=[];
    for(const view of ['front','back'])if(parts.some(p=>p.view===view))viewFiles.push({view,path:await save(view,renderSvg(current.scene,{view}))});
    for(const part of parts){
      const detail=await save('detail-'+part.id,await detailSvg(current.scene,part.id),768);
      const mask=await save('mask-'+part.id,renderSvg(current.scene,{view:part.view,partId:part.id,mask:true}),800);
      materials.push({partId:part.id,name:part.name,view:part.view,prompt:part.prompt,requirements:part.requirements,detail,mask});
    }
    const prompt=[
      'Create a clothing product visualization faithfully matching the attached vector design. This is a visual design, not a manufacturing pattern.',
      'The vector overview controls silhouette, relative placement, visible part count, front/back construction, and colors. Front and back are two views of the same garment.',
      'Detail images explain the named parts; they are not additional garments. User reference photos are secondary inspiration and must not override the confirmed vector shape.',
      'Use a clean neutral background and a clear product view. Preserve the designed garment; do not invent accessories, logos or extra pockets.',
      `Title: ${current.scene.title}`,`Design intent: ${current.scene.brief}`,`Overall appearance: ${current.scene.globalPrompt}`,
      ...materials.map(m=>`[${m.partId}; ${m.view}; ${m.name}] ${m.prompt}\nRequired: ${m.requirements.join('; ')||'Match the vector design.'}`),
      `Avoid: ${current.scene.negativePrompt||'Unrequested design changes.'}`,
      ...current.references.map(r=>`Optional reference (${r.partId||'overall'}): ${r.description}`),
    ].join('\n\n');
    const manifest={schemaVersion:1,id:jobId,projectId,revision,requestId,createdAt:new Date().toISOString(),confirmedByUser:true,snapshot:current,
      prompt,overview,views:viewFiles,parts:materials,references:current.references.map(r=>({...r,path:path.join(this.store.projectDir(projectId),r.path)})),
      note:'Prepared locally. No image generation request has been sent. Masks are optional supporting files, not evidence that a provider supports masked editing.'};
    await putOnce(path.join(dir,'prompt.txt'),prompt);
    if((await this.store.get(projectId)).revision!==revision)throw new Error('准备期间设计发生变化，请重新确认当前版本。');
    await putOnce(path.join(dir,'job.json'),manifest);
    return this.status(projectId,jobId);
  }
  async status(projectId,jobId){
    const dir=this.jobDir(projectId,jobId),job=await readOptional(path.join(dir,'job.json'));if(!job)throw new Error('Generation job not found.');
    const [claim,result,failure,review,current]=await Promise.all([readOptional(path.join(dir,'claim.json')),readOptional(path.join(dir,'result.json')),readOptional(path.join(dir,'failure.json')),readOptional(path.join(dir,'review.json')),this.store.get(projectId)]);
    const {snapshot,...compact}=job;
    return {...compact,status:result?'completed':failure?'needs_attention':claim?'claimed':'prepared',claim,result,failure,review,stale:current.revision!==job.revision};
  }
  async claim(projectId,jobId){
    const job=await this.status(projectId,jobId);
    if(job.stale)throw new Error('设计已有新版本。请重新确认后创建新的生成任务。');
    if(job.status!=='prepared')throw new Error('这个生成任务已被领取或完成。请核对已有结果，不要重复调用付费生图。');
    try{await immutableWrite(path.join(this.jobDir(projectId,jobId),'claim.json'),{claimedAt:new Date().toISOString()});}
    catch(error){if(error.code==='EEXIST')throw new Error('另一执行者已经领取此任务；不要重复生图。');throw error;}
    return {...await this.status(projectId,jobId),proceed:true,instruction:'Call the chosen image provider once. If the response is lost or ambiguous, inspect provider status before any new paid call.'};
  }
  async attach(projectId,jobId,filename,provider){
    const job=await this.status(projectId,jobId);if(!job.claim)throw new Error('领取生成任务后才能保存效果图。');
    const asset=await this.store.importImage(projectId,filename),result={asset,provider,recordedAt:new Date().toISOString()};
    const existing=job.result;if(existing){if(existing.asset.id===asset.id)return job;throw new Error('此任务已有不同的效果图。请保留原结果，为新一次生成创建独立任务。');}
    try{await immutableWrite(path.join(this.jobDir(projectId,jobId),'result.json'),result);}catch(error){if(error.code!=='EEXIST')throw error;const other=await this.status(projectId,jobId);if(other.result?.asset.id!==asset.id)throw new Error('另一张效果图已保存，当前结果未覆盖。');}
    return this.status(projectId,jobId);
  }
  async fail(projectId,jobId,message){
    const job=await this.status(projectId,jobId);if(!job.claim)throw new Error('该任务尚未提交，不能标记为生图失败。');
    await putOnce(path.join(this.jobDir(projectId,jobId),'failure.json'),{message,recordedAt:new Date().toISOString()});return this.status(projectId,jobId);
  }
  async review(projectId,jobId,findings){
    const job=await this.status(projectId,jobId);if(!job.result)throw new Error('尚无可分析的效果图。');
    const doc=await this.store.get(projectId,job.revision);
    findings=findings.map(f=>findingSchema.parse(f));
    for(const f of findings)if(f.partId&&!doc.scene.parts.some(p=>p.id===f.partId))throw new Error('Analysis references an unknown design part.');
    const report={revision:job.revision,resultHash:job.result.asset.id,findings,recordedAt:new Date().toISOString()};
    await immutableWrite(path.join(this.jobDir(projectId,jobId),'review.json'),report).catch(error=>{if(error.code==='EEXIST')throw new Error('此结果已经保存过分析。请读取已有分析；后续修改应建立新设计版本。');throw error;});
    return this.status(projectId,jobId);
  }
  async document(projectId,revision){
    const doc=await this.store.get(projectId,revision);
    return {document:doc,history:await this.history(projectId),jobs:(await this.jobs(projectId)).map(({id,revision,status,stale,review,result})=>({id,revision,status,stale,review,result})),svg:{front:renderSvg(doc.scene,{view:'front'}),back:renderSvg(doc.scene,{view:'back'})}};
  }
}
