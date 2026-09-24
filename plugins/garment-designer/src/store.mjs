import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomUUID, createHash } from 'node:crypto';
import { id, sceneSchema, editScene } from './model.mjs';

export const sha256=value=>createHash('sha256').update(value).digest('hex');
export async function immutableWrite(filename,value){
  await fs.mkdir(path.dirname(filename),{recursive:true});
  const temp=filename+'.'+randomUUID()+'.tmp';
  try{
    const handle=await fs.open(temp,'wx',0o600);
    try{await handle.writeFile(typeof value==='string'||Buffer.isBuffer(value)?value:JSON.stringify(value,null,2));await handle.sync();}finally{await handle.close();}
    // Publishing an already complete inode is atomic; concurrent writers never
    // overwrite each other or expose a partially written revision.
    await fs.link(temp,filename);
  }finally{await fs.unlink(temp).catch(()=>{});}
}
export class Store{
  constructor(root=process.env.GARMENT_DESIGNER_DATA_DIR||path.join(os.homedir(),'.garment-designer')){this.root=path.resolve(root);}
  projectDir(projectId){return path.join(this.root,'projects',id.parse(projectId));}
  async create(scene){
    const projectId=randomUUID();const doc={schemaVersion:1,id:projectId,revision:1,createdAt:new Date().toISOString(),scene:sceneSchema.parse(scene),references:[],note:'初稿'};
    await immutableWrite(path.join(this.projectDir(projectId),'revisions','1.json'),doc);return doc;
  }
  async revisions(projectId){
    const names=await fs.readdir(path.join(this.projectDir(projectId),'revisions'));
    return names.filter(n=>/^\d+\.json$/.test(n)).map(n=>Number.parseInt(n)).sort((a,b)=>b-a);
  }
  async get(projectId,revision){
    const versions=await this.revisions(projectId);const current=revision??versions[0];
    if(!versions.includes(current))throw new Error('Design revision not found.');
    return JSON.parse(await fs.readFile(path.join(this.projectDir(projectId),'revisions',current+'.json'),'utf8'));
  }
  async list(){
    const names=await fs.readdir(path.join(this.root,'projects')).catch(error=>{if(error.code==='ENOENT')return [];throw error;});
    const result=[];
    for(const name of names){if(!id.safeParse(name).success)continue;try{const doc=await this.get(name);result.push({id:name,title:doc.scene.title,revision:doc.revision,updatedAt:doc.createdAt});}catch(error){if(error.code!=='ENOENT')throw error;}}
    return result.sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt));
  }
  async commit(projectId,expectedRevision,operation,note){
    const current=await this.get(projectId);
    if(current.revision!==expectedRevision)throw new Error(`版本冲突：当前为 v${current.revision}，请重新读取后再修改。`);
    const next=await operation(structuredClone(current));
    next.id=projectId;next.revision=expectedRevision+1;next.createdAt=new Date().toISOString();next.note=String(note||'修改设计').slice(0,1000);next.scene=sceneSchema.parse(next.scene);
    try{await immutableWrite(path.join(this.projectDir(projectId),'revisions',next.revision+'.json'),next);}catch(error){if(error.code==='EEXIST')throw new Error('版本冲突：另一项修改已经保存，请重新读取。');throw error;}
    return next;
  }
  edit(projectId,expectedRevision,patch,note){return this.commit(projectId,expectedRevision,doc=>({...doc,scene:editScene(doc.scene,patch)}),note);}
  async restore(projectId,expectedRevision,sourceRevision){const source=await this.get(projectId,sourceRevision);return this.commit(projectId,expectedRevision,doc=>({...doc,scene:source.scene,references:source.references}),`恢复自 v${sourceRevision}`);}
  async importImage(projectId,filename){
    if(!path.isAbsolute(filename))throw new Error('Image path must be absolute on the plugin server.');
    const stat=await fs.stat(filename);if(!stat.isFile()||stat.size>12*1024*1024)throw new Error('Image must be a file no larger than 12 MB.');
    const data=await fs.readFile(filename);let mime,ext;
    if(data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))){mime='image/png';ext='png';}
    else if(data[0]===255&&data[1]===216&&data[2]===255){mime='image/jpeg';ext='jpg';}
    else if(data.toString('ascii',0,4)==='RIFF'&&data.toString('ascii',8,12)==='WEBP'){mime='image/webp';ext='webp';}
    else throw new Error('Only PNG, JPEG and WebP images are supported.');
    const assetId=sha256(data),relative=`assets/${assetId}.${ext}`;
    await immutableWrite(path.join(this.projectDir(projectId),relative),data).catch(error=>{if(error.code!=='EEXIST')throw error;});
    return {id:assetId,path:relative,mime,bytes:data.length};
  }
  async asset(projectId,asset){
    if(!/^assets\/[a-f0-9]{64}\.(png|jpg|webp)$/.test(asset.path))throw new Error('Invalid image asset.');
    return fs.readFile(path.join(this.projectDir(projectId),asset.path));
  }
}
