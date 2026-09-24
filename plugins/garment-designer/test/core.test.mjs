import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { GarmentService } from '../src/service.mjs';
import { sceneSchema, renderSvg, editScene } from '../src/model.mjs';
import { template } from '../src/templates.mjs';
import { raster, detailSvg } from '../src/render.mjs';
const roots=[];
async function fresh(){const root=await fs.mkdtemp(path.join(os.tmpdir(),'garment-designer-test-'));roots.push(root);return new GarmentService(root);}
test.after(async()=>{for(const root of roots){const resolved=path.resolve(root);assert.equal(path.dirname(resolved),path.resolve(os.tmpdir()));assert.match(path.basename(resolved),/^garment-designer-test-/);await fs.rm(resolved,{recursive:true,force:true});}});
test('prompt-only edits preserve layer, back view, color, requirements and unrelated geometry',()=>{
  const scene=template('shirt','测试');const p=scene.parts.find(p=>p.id==='body-back');p.z=12;p.fill='#224466';p.requirements=['保持开口'];
  const next=editScene(scene,{parts:[{id:p.id,prompt:'柔和棉质'}]});
  assert.deepEqual({...next.parts.find(q=>q.id===p.id),prompt:''},p);
  assert.deepEqual(next.parts.find(q=>q.id==='collar'),scene.parts.find(q=>q.id==='collar'));
});
test('arbitrary asymmetric parts are supported, and templates are not mandatory',async()=>{
  const s=await fresh();const d=await s.create({scene:{title:'自由款式',parts:[{id:'cape',name:'右侧披肩',kind:'custom',elements:[{type:'path',d:'M400 100 C800 250 500 500 750 850 L450 500 Z'}]}]}});
  const next=await s.store.edit(d.id,1,{parts:[{id:'trim',name:'包边',kind:'custom',z:5,fill:'none',elements:[{type:'polyline',points:[[400,100],[550,240],[750,850]]}]}]},'增加装饰');
  assert.equal(next.scene.parts.length,2);assert.equal(next.scene.parts[1].fill,'none');
});
test('moving a transformed detail does not reset rotation or scale',()=>{
  const scene=template('shirt','小装饰');scene.parts[0].transform={x:10,y:20,rotate:12,scaleX:.8,scaleY:1.2};
  const next=editScene(scene,{parts:[{id:scene.parts[0].id,transform:{x:30}}]});
  assert.deepEqual(next.parts[0].transform,{x:30,y:20,rotate:12,scaleX:.8,scaleY:1.2});
});
test('locked parts require explicit unlock, and invalid edits never create a revision',async()=>{
  const s=await fresh(),d=await s.create({});await s.store.edit(d.id,1,{parts:[{id:'collar',locked:true}]});
  await assert.rejects(()=>s.store.edit(d.id,2,{parts:[{id:'collar',fill:'#ffffff'}]}),/locked/);
  assert.equal((await s.store.get(d.id)).revision,2);
  const next=await s.store.edit(d.id,2,{unlock:['collar'],parts:[{id:'collar',fill:'#ffffff'}]});assert.equal(next.scene.parts.find(p=>p.id==='collar').locked,false);
});
test('malicious markup, external fills, duplicate IDs and path traversal are rejected',async()=>{
  const s=await fresh(),scene=template('shirt','<script>alert(1)</script>');
  scene.parts[0].elements=[{type:'text',x:100,y:100,text:'</text><script>evil()</script>',fontSize:24}];
  const svg=renderSvg(scene);assert(!svg.includes('<script>'));assert(svg.includes('&lt;script&gt;'));
  assert.throws(()=>editScene(scene,{parts:[{id:'collar',fill:'url(https://evil.example/pixel)'}]}));
  assert.throws(()=>editScene(scene,{parts:[{id:'collar',elements:[{type:'path',d:'M0 0"/><script/>'}]}]}));
  assert.throws(()=>sceneSchema.parse({...scene,parts:[scene.parts[0],scene.parts[0]]}));
  assert.throws(()=>s.store.projectDir('../outside'));
  await assert.rejects(()=>s.store.asset('project',{path:'../../secret.txt'}),/Invalid image/);
});
test('competing revision writers do not lose updates',async()=>{
  const s=await fresh(),d=await s.create({});
  const results=await Promise.allSettled(Array.from({length:8},(_,n)=>new GarmentService(s.store.root).store.edit(d.id,1,{title:'设计'+n})));
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal((await s.store.get(d.id)).revision,2);
  assert.deepEqual(await s.store.revisions(d.id),[2,1]);
});
test('version conflict protection also holds between separate Node processes',async()=>{
  const s=await fresh(),d=await s.create({});
  const moduleUrl=pathToFileURL(path.resolve('src/service.mjs')).href;
  const code=`import {GarmentService} from ${JSON.stringify(moduleUrl)};try {const s=new GarmentService(process.argv[1]);await s.store.edit(process.argv[2],1,{title:process.argv[3]});process.stdout.write('saved');}catch(e){if(e.message.includes('版本冲突'))process.stdout.write('conflict');else throw e;}`;
  const run=n=>new Promise((resolve,reject)=>{const child=spawn(process.execPath,['--input-type=module','-e',code,s.store.root,d.id,'独立进程 '+n],{windowsHide:true});let out='',err='';child.stdout.on('data',v=>out+=v);child.stderr.on('data',v=>err+=v);child.on('error',reject);child.on('close',code=>code?reject(Error(err)):resolve(out));});
  const results=await Promise.all([run(1),run(2),run(3)]);assert.equal(results.filter(v=>v==='saved').length,1);assert.equal(results.filter(v=>v==='conflict').length,2);
});
test('restore creates a new version while preserving all history',async()=>{
  const s=await fresh(),d=await s.create({title:'起稿'});await s.store.edit(d.id,1,{title:'修改后'});
  const restored=await s.store.restore(d.id,2,1);assert.equal(restored.revision,3);assert.equal(restored.scene.title,'起稿');assert.equal((await s.store.get(d.id,2)).scene.title,'修改后');
});
test('all built-in templates render locally, including close-ups and narrow trims',async()=>{
  for(const name of ['shirt','jacket','dress','trousers']){const scene=template(name,name);const png=await raster(renderSvg(scene));assert.equal(png.toString('ascii',1,4),'PNG');assert(png.readUInt32BE(20)<=1600);}
  const scene=sceneSchema.parse({title:'细滚边',parts:[{id:'trim',name:'细滚边',elements:[{type:'rect',x:400,y:50,width:1,height:900}],strokeWidth:0}]});
  const png=await raster(await detailSvg(scene,'trim'),768);assert(png.readUInt32BE(20)<=1600);assert(png.length<200000);
});
test('confirmation, stale revision rejection, idempotent prepare, and one-time claims',async()=>{
  const s=await fresh(),d=await s.create({template:'dress'});
  await assert.rejects(()=>s.prepare(d.id,1,'attempt',false),/确认/);
  const prepared=await s.prepare(d.id,1,'attempt',true);assert.equal(prepared.status,'prepared');assert(prepared.parts.every(p=>p.detail&&p.mask));
  assert.equal((await s.prepare(d.id,1,'attempt',true)).id,prepared.id);
  const claims=await Promise.allSettled(Array.from({length:5},()=>s.claim(d.id,prepared.id)));assert.equal(claims.filter(r=>r.status==='fulfilled').length,1);
  await s.store.edit(d.id,1,{title:'新版'});
  assert.equal((await s.status(d.id,prepared.id)).stale,true);
  await assert.rejects(()=>s.prepare(d.id,1,'another',true),/更新/);
  const unused=await s.prepare(d.id,2,'next',true);await s.store.edit(d.id,2,{title:'又修改'});await assert.rejects(()=>s.claim(d.id,unused.id),/新版本/);
});
test('timeout recovery attaches actual results once and reviews the frozen version',async()=>{
  const s=await fresh(),d=await s.create({template:'dress'}),job=await s.prepare(d.id,1,'fixture-only',true);
  const fixture=path.join(s.store.root,'fixture.png');await fs.writeFile(fixture,(await s.preview(d.id)).png);
  await assert.rejects(()=>s.attach(d.id,job.id,fixture,'test-fixture'),/领取/);
  await s.claim(d.id,job.id);await s.fail(d.id,job.id,'测试超时，未调用任何真实生图服务');await assert.rejects(()=>s.claim(d.id,job.id),/已被领取/);
  await s.store.edit(d.id,1,{title:'后来的版本'});
  const attached=await s.attach(d.id,job.id,fixture,'test-fixture');assert.equal(attached.status,'completed');assert.equal(attached.revision,1);assert(attached.stale);
  assert.equal((await s.attach(d.id,job.id,fixture,'test-fixture')).result.asset.id,attached.result.asset.id);
  const reviewed=await s.review(d.id,job.id,[{partId:'body-front',kind:'uncertain',observation:'测试夹具，不代表真实生图验收'}]);assert.equal(reviewed.review.revision,1);
  await assert.rejects(()=>s.review(d.id,job.id,[{partId:'missing',kind:'deviation',observation:'invalid'}]),/unknown design part/);
  assert.equal((await s.store.get(d.id)).scene.title,'后来的版本');
});
test('optional references are copied and versioned without depending on source lifetime',async()=>{
  const s=await fresh(),d=await s.create({});const filename=path.join(s.store.root,'ref.png');await fs.writeFile(filename,(await s.preview(d.id)).png);
  const attached=await s.reference(d.id,1,filename,'只参考领型','collar');await fs.unlink(filename);
  assert.equal(attached.references.length,1);assert((await s.store.asset(d.id,attached.references[0])).length>0);assert.equal((await s.store.get(d.id,1)).references.length,0);
  await assert.rejects(()=>s.reference(d.id,2,'relative.png','',undefined),/absolute/);
});
