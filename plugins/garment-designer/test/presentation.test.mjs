import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import JSZip from 'jszip';
import { Store } from '../src/store.mjs';
import { template } from '../src/templates.mjs';
import { exportPresentation } from '../dist/presentation.mjs';

async function fixture(t) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'garment-presentation-'));
  t.after(async()=>{assert.equal(path.dirname(path.resolve(root)),path.resolve(os.tmpdir()));assert.match(path.basename(root),/^garment-presentation-/);await fs.rm(root,{recursive:true,force:true});});
  const store=new Store(root),scene=template('jacket','雾蓝短外套','日常穿着，明确的轮廓和局部细节。');
  scene.parts.find(p=>p.id==='collar').prompt='左侧宽翻领与细银边，保留原来的领口比例。';
  const doc=await store.create(scene);
  return {root,store,doc};
}

test('bundled exporter produces real SVG HTML, editable text/SVG PPTX, notes and compatibility previews',async t=>{
  const {store,doc}=await fixture(t);
  const result=await exportPresentation(store,{project_id:doc.id,revision:1,formats:['html','pptx'],slides:[
    {title:'整体款式',visual:'overview',bullets:['宽松的短衣身','雾蓝主体色'],notes:'讲解轮廓和比例。'},
    {title:'翻领细节',visual:'part',part_id:'collar',bullets:['局部设计'],notes:'结合领口细节说明。'},
  ]});
  assert.deepEqual(result.errors,[]);assert.equal(result.files.length,2);
  const html=await fs.readFile(result.files.find(f=>f.format==='html').path,'utf8');
  assert.equal((html.match(/<section class="slide"/g)||[]).length,2);assert.match(html,/<svg/);assert.match(html,/翻领细节/);assert.match(html,/讲解轮廓和比例/);
  for(const root of html.matchAll(/<svg\b[^>]*>/g))assert.match(root[0],/viewBox=/,'inline SVG must scale its contents instead of cropping the overview');
  assert.doesNotMatch(html,/<(?:script|link)[^>]*(?:src|href)=/i);
  const zip=await JSZip.loadAsync(await fs.readFile(result.files.find(f=>f.format==='pptx').path));
  assert.match(await zip.file('ppt/slides/slide1.xml').async('string'),/<a:t>整体款式<\/a:t>/);
  assert.match(await zip.file('ppt/slides/slide1.xml').async('string'),/<asvg:svgBlip/);
  assert.match(await zip.file('ppt/notesSlides/notesSlide1.xml').async('string'),/讲解轮廓和比例/);
  const vectors=Object.values(zip.files).filter(f=>f.name.endsWith('.svg'));assert.equal(vectors.length,2);
  assert.match(await vectors[1].async('string'),/data-part-id="collar"/);
  const previews=Object.values(zip.files).filter(f=>f.name.endsWith('.png'));assert.equal(previews.length,2);
  for(const file of previews){const bytes=await file.async('nodebuffer');assert.equal(bytes.toString('hex',0,8),'89504e470d0a1a0a');assert(bytes.readUInt32BE(16)>300);assert(bytes.length>5000,'real garment preview, not a placeholder');}
  assert.equal((await store.get(doc.id)).revision,1,'export never edits the design');
});

test('PDF embeds vector drawing and a real CJK font; missing fonts report partial success',async t=>{
  const {root,store,doc}=await fixture(t);
  const partial=await exportPresentation(store,{project_id:doc.id,revision:1,formats:['pdf','html']},{fontPath:path.join(root,'missing.ttf')});
  assert.deepEqual(partial.files.map(f=>f.format),['html']);assert.deepEqual(partial.errors.map(f=>f.format),['pdf']);
  // Use the platform's font discovery. CI without CJK fonts verifies the clear error.
  const result=await exportPresentation(store,{project_id:doc.id,revision:1,formats:['pdf']});
  if(result.errors.length){assert.match(result.errors[0].message,/font covering/);return;}
  const bytes=await fs.readFile(result.files[0].path),pdf=bytes.toString('latin1');
  assert.match(pdf,/^%PDF-/);assert.match(pdf,/\/FontFile[23]/);assert.match(pdf,/\/ToUnicode/);
  assert.equal((pdf.match(/\/Type \/Page\b/g)||[]).length,result.slides);
  assert.doesNotMatch(pdf,/\/Subtype \/Image/,'the design is drawn as vector paths, not a page screenshot');
});

test('exports pin old revisions and concurrent repeats do not overwrite complete files',async t=>{
  const {store,doc}=await fixture(t);
  await store.edit(doc.id,1,{title:'新版款式'},'new revision');
  const input={project_id:doc.id,revision:1,formats:['html','pptx']};
  const results=await Promise.all(Array.from({length:4},()=>exportPresentation(store,input)));
  assert(results.every(r=>r.directory===results[0].directory&&r.errors.length===0));
  const before=await fs.stat(results[0].files[0].path);await exportPresentation(store,input);const after=await fs.stat(results[0].files[0].path);
  assert.equal(before.mtimeMs,after.mtimeMs);assert.equal((await store.get(doc.id)).revision,2);
  assert.match(await fs.readFile(results[0].files[0].path,'utf8'),/雾蓝短外套/);
  assert.doesNotMatch(await fs.readFile(results[0].files[0].path,'utf8'),/新版款式/);
});

test('unknown views/parts, dense slides and traversal fail before exporting; prose is escaped',async t=>{
  const {store,doc}=await fixture(t);
  for(const slide of [{title:'未知部位',visual:'part',part_id:'missing'}, {title:'密集',bullets:['x'.repeat(120),'y'.repeat(120),'z'.repeat(120)]}])
    await assert.rejects(exportPresentation(store,{project_id:doc.id,revision:1,slides:[slide]}));
  await assert.rejects(exportPresentation(store,{project_id:'../outside',revision:1}));
  const result=await exportPresentation(store,{project_id:doc.id,revision:1,formats:['html'],slides:[{title:'<script>alert(1)</script>',bullets:['<img src="https://example.invalid/track">'],notes:'</aside><script>alert(2)</script>'}]});
  const html=await fs.readFile(result.files[0].path,'utf8');assert.doesNotMatch(html,/<script>alert|<img src="https/);assert.match(html,/&lt;script&gt;/);
  const saved=await store.get(doc.id);await store.commit(doc.id,1,d=>({...d,scene:{...d.scene,parts:saved.scene.parts.filter(p=>p.view==='front')}}),'front only');
  await assert.rejects(exportPresentation(store,{project_id:doc.id,revision:2,slides:[{title:'背面',visual:'back'}]}),/no back view/);
});
