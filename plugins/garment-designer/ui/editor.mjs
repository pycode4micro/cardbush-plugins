import { partSvg } from '../src/model.mjs';
const $=id=>document.getElementById(id);
let serial=0,state=null,project=null,view='front',selected=null,dirty=false,busy=false,latestRevision=0;
const dirtyFields=new Set();
const pending=new Map();
function notify(method,params){parent.postMessage({jsonrpc:'2.0',method,params},'*');}
function rpc(method,params){
  const id='garment-'+(++serial);
  return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('连接超时。请刷新确认操作是否已保存；不要重复提交生图。'));},120000);pending.set(id,{resolve,reject,timer});parent.postMessage({jsonrpc:'2.0',id,method,params},'*');});
}
function theme(context){
  if(context?.theme)document.documentElement.style.colorScheme=context.theme==='dark'?'dark':'light';
  for(const [key,value] of Object.entries(context?.styles?.variables||{})){
    if(/^--[a-z0-9-]+$/.test(key)&&typeof value==='string')document.documentElement.style.setProperty(key,value);
  }
}
function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
function changed(event){dirty=true;if(event?.target?.id)dirtyFields.add(event.target.id);$('save').textContent='保存修改';}
function accept(result){
  const data=result?._meta?.garment;if(!data)return false;
  if(project&&data.document.id!==project)return false;
  state=data;project=data.document.id;latestRevision=Math.max(...data.history.map(v=>v.revision),data.document.revision);
  if(!data.document.scene.parts.some(p=>p.id===selected&&p.view===view))selected=data.document.scene.parts.find(p=>p.view===view)?.id;
  $('waiting').classList.add('hidden');$('workspace').classList.remove('hidden');paint();return true;
}
async function tool(name,args){const result=await rpc('tools/call',{name,arguments:args});if(result?.isError)throw new Error(result.content?.find(c=>c.type==='text')?.text||'保存失败');return result;}
async function work(fn){if(busy)return;busy=true;document.querySelectorAll('button,input,textarea,select').forEach(b=>b.disabled=true);try{await fn();}catch(error){status(error.message,true);}finally{busy=false;document.querySelectorAll('button,input,textarea,select').forEach(b=>b.disabled=false);setHistorical();resize();}}
function setHistorical(){const historical=state&&state.document.revision<latestRevision;for(const key of ['save','generate','ask'])$(key).disabled=busy||historical;for(const key of ['part-prompt','part-color','part-lock','requirements'])$(key).disabled=busy||historical||!currentPart();$('restore').classList.toggle('hidden',!historical);}
function resize(){notify('ui/notifications/size-changed',{height:Math.min(900,Math.ceil(document.body.scrollHeight))});}
function currentPart(){return state?.document.scene.parts.find(p=>p.id===selected);}
function paint(){
  const doc=state.document;
  $('title').textContent=doc.scene.title;$('revision').textContent='v'+doc.revision;
  $('history').replaceChildren(...state.history.map(h=>new Option(`v${h.revision} · ${h.note}`,String(h.revision),false,h.revision===doc.revision)));
  $('parts').replaceChildren(...doc.scene.parts.filter(p=>p.view===view).map(p=>new Option((p.locked?'🔒 ':'')+p.name,p.id,false,p.id===selected)));
  $('front').classList.toggle('active',view==='front');$('back').classList.toggle('active',view==='back');
  // Only render validated vector primitives; never insert returned HTML or images as markup.
  $('canvas').innerHTML=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${doc.scene.width} ${doc.scene.height}"><rect width="100%" height="100%" fill="white"/>${doc.scene.parts.filter(p=>p.view===view).sort((a,b)=>a.z-b.z).map(p=>partSvg(p)).join('')}</svg>`;
  fillPart();paintJobs();setHistorical();resize();
}
function fillPart(){
  const p=currentPart();dirty=false;dirtyFields.clear();$('save').textContent='保存部位';
  $('part-color').dataset.changed='';
  $('part-prompt').value=p?.prompt||'';$('part-color').value=p?.fill?.match(/^#[a-f\d]{6}$/i)?p.fill:'#dce3d7';$('color-value').textContent=p?.fill||'';$('part-lock').checked=!!p?.locked;$('requirements').value=p?.requirements.join('\n')||'';
  $('parts').value=selected||'';
  for(const g of $('canvas').querySelectorAll('g[data-part-id]'))g.classList.toggle('selected',g.getAttribute('data-part-id')===selected);
  $('detail').innerHTML=p?`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${state.document.scene.width} ${state.document.scene.height}">${partSvg({...p,visible:true})}</svg>`:'';
  const svg=$('detail').querySelector('svg'),g=svg?.querySelector('g');
  if(g){const b=g.getBBox(),pad=Math.max(12,Math.max(b.width,b.height)*.08);if(b.width>0&&b.height>0)svg.setAttribute('viewBox',`${b.x-pad} ${b.y-pad} ${b.width+2*pad} ${b.height+2*pad}`);}
  for(const key of ['part-prompt','part-color','part-lock','requirements'])$(key).disabled=!p;
}
function paintJobs(){
  $('results').replaceChildren();
  const words={prepared:'已准备，尚未生成',claimed:'已提交生成流程',completed:'效果图已保存',needs_attention:'生成需要处理'};
  for(const job of state.jobs){
    const row=document.createElement('div');row.className='job';
    const label=document.createElement('div');label.className='label';label.textContent=`v${job.revision} · ${words[job.status]}${job.stale?' · 早期版本':''}`;row.append(label);
    if(job.status==='prepared'&&!job.stale){const button=document.createElement('button');button.textContent='继续生成';button.onclick=()=>work(()=>message(`我确认衣服设计 ${project} 的 v${job.revision}。请继续已准备的生成任务 ${job.id}：检查生图能力，claim 成功后提交一次，再 attach 真实结果。`));row.append(button);}
    if(job.result){
      const show=document.createElement('button');show.textContent='查看效果';show.onclick=()=>work(async()=>{
        const result=await tool('garment_preview',{project_id:project,job_id:job.id}),data=result.content?.find(c=>c.type==='image');if(!data)throw new Error('效果图读取失败。');
        let img=row.querySelector('img');if(!img){img=document.createElement('img');img.alt=`v${job.revision} 效果图`;img.style.cssText='width:100%;max-height:500px;object-fit:contain;border-radius:8px;background:white';row.append(img);}
        img.src=`data:${data.mimeType};base64,${data.data}`;
      });row.append(show);
      const button=document.createElement('button');button.textContent=job.review?'继续讨论修改':'对照设计分析';button.onclick=()=>work(()=>message(`请查看衣服设计 ${project} 的效果图 ${job.id}，使用 garment_review context 读取真实图片并对照已确认的 v${job.revision}。区分偏差、建议和不确定项；已有分析可直接展示。`));row.append(button);
    }
    if(job.review){const list=document.createElement('ul');list.className='findings';for(const f of job.review.findings){const li=document.createElement('li');li.textContent=`${{deviation:'偏差',suggestion:'建议',uncertain:'待确认'}[f.kind]}：${f.observation}${f.proposal?' → '+f.proposal:''}`;list.append(li);}row.append(list);}
    $('results').append(row);
  }
}
async function save(){
  if(!dirty)return;
  const p=currentPart();if(!p)return;
  const prompt=$('part-prompt').value,requirements=$('requirements').value.split('\n').map(v=>v.trim()).filter(Boolean);
  const change={id:p.id};
  if(dirtyFields.has('part-prompt'))change.prompt=prompt;
  if(dirtyFields.has('requirements'))change.requirements=requirements;
  if(dirtyFields.has('part-lock'))change.locked=$('part-lock').checked;
  if(dirtyFields.has('part-color'))change.fill=$('part-color').value;
  if(p.locked&&change.locked!==false)throw new Error('这个部位已锁定。请先取消锁定，再保存修改。');
  const result=await tool('garment_edit',{project_id:project,expected_revision:state.document.revision,patch:{parts:[change],...(p.locked?{unlock:[p.id]}:{})},note:'在设计面板中修改 '+p.name});
  $('part-color').dataset.changed='';accept(result);status('已保存。若描述涉及形状变化，点击“发送修改要求”让 AI 更新矢量稿。');
}
async function message(text){
  const result=await rpc('ui/message',{role:'user',content:[{type:'text',text}]});
  if(result?.isError)throw new Error('消息未发送，请在对话中继续。');
  status('请求已交给对话处理。');return result;
}
$('canvas').onclick=event=>{const key=event.target.closest('[data-part-id]')?.getAttribute('data-part-id');if(!key||key===selected)return;work(async()=>{await save();selected=key;fillPart();});};
$('parts').onchange=event=>{const key=event.target.value;work(async()=>{await save();selected=key;fillPart();});};
for(const side of ['front','back'])$(side).onclick=()=>work(async()=>{await save();view=side;selected=state.document.scene.parts.find(p=>p.view===side)?.id;paint();});
for(const key of ['part-prompt','part-lock','requirements'])$(key).addEventListener('input',changed);
$('part-color').oninput=event=>{changed(event);$('part-color').dataset.changed='true';$('color-value').textContent=$('part-color').value;};
$('save').onclick=()=>work(save);
$('ask').onclick=()=>work(async()=>{
  const instruction=$('instruction').value.trim();if(!instruction)throw new Error('请先描述想修改的地方。');await save();
  await message(`请用 garment-design 修改设计 ${project} 当前 v${state.document.revision}（目前选中部位 ${selected||'整体'}）：${instruction}\n先用 garment_project get 核对最新版本，使用 garment_edit 修改矢量形状和对应描述，并用 garment_preview 展示。保留未要求修改和已锁定的部位；本次不生图。`);
  $('instruction').value='';
});
$('generate').onclick=()=>work(async()=>{
  if(dirty){await save();status('修改已保存，请检查当前版本，再点击“确认此版本，生成效果图”。');return;}
  const revision=state.document.revision,request_id='ui-'+crypto.randomUUID();
  const result=await tool('garment_render',{action:'prepare',project_id:project,revision,request_id,confirmed_by_user:true});accept(result);
  const job=result.structuredContent?.generation;if(!job)throw new Error('未取得生成任务，请刷新查看。');
  await message(`我确认衣服设计 ${project} 的 v${revision}，请使用已准备的生成任务 ${job.id} 生成一张完整效果图。请先检查可用的生图能力和限制，garment_render claim 成功后只提交一次，再 attach 实际结果；不要重新准备重复任务。`);
});
$('refresh').onclick=()=>work(async()=>{
  const target=selected,fields=[...dirtyFields],draft=Object.fromEntries(fields.map(key=>[key,key==='part-lock'?$(key).checked:$(key).value]));
  accept(await tool('garment_project',{action:'get',project_id:project}));
  if(fields.length){
    if(!state.document.scene.parts.some(p=>p.id===target)){
      $('instruction').value=`刚才对部位 ${target} 的草稿：${JSON.stringify(draft)}\n请结合当前设计处理。`;status('原部位已移除，未保存的内容保留在修改要求中，请确认后发送。');return;
    }
    selected=target;view=currentPart().view;paint();
    for(const key of fields){if(key==='part-lock')$(key).checked=draft[key];else $(key).value=draft[key];dirtyFields.add(key);}
    dirty=true;$('save').textContent='保存修改';status('已读取新版本，保留了你未保存的字段。请核对后再保存。');
  }else status('已读取最新设计。');
});
$('history').onchange=event=>{const revision=Number(event.target.value);work(async()=>{await save();accept(await tool('garment_project',{action:'get',project_id:project,revision}));status(revision<latestRevision?'正在查看历史版本。恢复会创建一个新版本。':'');});};
$('restore').onclick=()=>work(async()=>{accept(await tool('garment_project',{action:'restore',project_id:project,expected_revision:latestRevision,source_revision:state.document.revision}));status('已恢复为一个新版本。');});
window.addEventListener('message',event=>{
  if(event.source!==parent||event.data?.jsonrpc!=='2.0')return;
  const msg=event.data;
  if(msg.id&&pending.has(msg.id)){const p=pending.get(msg.id);pending.delete(msg.id);clearTimeout(p.timer);msg.error?p.reject(new Error(msg.error.message||'操作失败')):p.resolve(msg.result);return;}
  if(msg.method==='ui/notifications/tool-result'){
    if(dirty||busy){status('有新的设计结果。当前修改尚未合并，请先保存后刷新。');return;}
    if(!accept(msg.params)&&!state)$('waiting').textContent='请在对话中创建或打开一份衣服设计。';
  }
  if(msg.method==='ui/notifications/host-context-changed')theme(msg.params?.hostContext||msg.params);
  if(msg.method==='ui/resource-teardown'&&msg.id)parent.postMessage({jsonrpc:'2.0',id:msg.id,result:{}},'*');
});
new ResizeObserver(()=>resize()).observe(document.querySelector('main'));
(async()=>{try{const init=await rpc('ui/initialize',{appInfo:{name:'garment-designer',version:'0.1.0'},appCapabilities:{},protocolVersion:'2026-01-26'});theme(init.hostContext);notify('ui/notifications/initialized',{});}catch(error){$('waiting').textContent='交互面板连接失败。可以继续在对话中描述设计，由 AI 调用工具编辑。';}})();
