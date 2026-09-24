// Hidden Electron window, isolated data, real packaged MCP server, fake host bridge.
// Does not connect to CardBush's user profile or any image provider.
const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'..');
const testDir=path.join(root,'.test-data','ui');
app.setPath('userData',path.join(testDir,'electron-profile'));
let win,client;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
app.whenReady().then(async()=>{
 try{
  await fs.mkdir(testDir,{recursive:true});
  const {Client}=await import('@modelcontextprotocol/sdk/client/index.js');
  const {StdioClientTransport}=await import('@modelcontextprotocol/sdk/client/stdio.js');
  const transport=new StdioClientTransport({command:process.env.GARMENT_TEST_NODE,args:[path.join(root,'dist/server.mjs')],env:{...Object.fromEntries(Object.entries(process.env).filter(([,v])=>typeof v==='string')),GARMENT_DESIGNER_DATA_DIR:path.join(testDir,'data')},stderr:'pipe'});
  client=new Client({name:'ui-test',version:'1.0.0'},{capabilities:{}});await client.connect(transport);
  let initial=await client.callTool({name:'garment_project',arguments:{action:'create',title:'轻雾蓝 · 不对称领外套',template:'jacket',brief:'日常穿着，右侧领片略收窄，金属细包边。'}});
  const project=initial.structuredContent.projectId;
  initial=await client.callTool({name:'garment_edit',arguments:{project_id:project,expected_revision:1,patch:{globalPrompt:'雾蓝色棉质短外套，柔和哑光表面',parts:initial._meta.garment.document.scene.parts.filter(p=>p.id!=='buttons').map(p=>({id:p.id,fill:'#b8cdd5',...(p.id==='collar'?{fill:'#7e9daa',prompt:'不对称的尖翻领。左侧更宽，边缘细银色包边。',elements:[{type:'path',d:'M340 145 L395 195 L335 270 L300 180 Z M460 145 L405 195 L450 250 L495 180 Z'}]}:{} )}))}}});
  let delay=0;const messages=[];
  ipcMain.handle('garment-ui-test',async(event,{method,params})=>{
    if(method==='ui/initialize')return {protocolVersion:'2026-01-26',hostInfo:{name:'test-host',version:'1'},hostCapabilities:{serverTools:{}},hostContext:{theme:'light',locale:'zh-CN',styles:{variables:{'--color-background-primary':'#ffffff','--color-background-secondary':'#f5f5f5','--color-text-primary':'#202020','--color-text-secondary':'#595959','--color-border-primary':'#dedede','--font-sans':'system-ui, sans-serif'}}}};
    if(method==='tools/call'){if(delay)await sleep(delay);return client.callTool(params);}
    if(method==='ui/message'){messages.push(params);return {};}
    throw Error('Unexpected UI method '+method);
  });
  const preload=path.join(testDir,'preload.cjs');await fs.writeFile(preload,"const {contextBridge,ipcRenderer}=require('electron');contextBridge.exposeInMainWorld('testHost',{call:(method,params)=>ipcRenderer.invoke('garment-ui-test',{method,params})});");
  const fixture=path.join(testDir,'host.html');
  await fs.writeFile(fixture,`<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#eef1eb}iframe{display:block;width:100%;height:880px;border:0}</style><iframe sandbox="allow-scripts" id="app"></iframe><script>
  const frame=document.querySelector('iframe');window.initial=${JSON.stringify(initial).replaceAll('<','\\u003c')};window.events=[];
  addEventListener('message',async event=>{if(event.source!==frame.contentWindow||event.data?.jsonrpc!=='2.0')return;const m=event.data;window.events.push({method:m.method});if(m.method==='ui/notifications/initialized'){frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.initial},'*');return;}if(!m.id)return;try{const result=await testHost.call(m.method,m.params);frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result},'*');}catch(e){frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,error:{code:-32000,message:e.message}},'*');}});
  frame.srcdoc=${JSON.stringify(await fs.readFile(path.join(root,'dist/editor.html'),'utf8')).replaceAll('<','\\u003c')};
  </script>`);
  win=new BrowserWindow({show:false,width:1024,height:1040,webPreferences:{preload,contextIsolation:true,sandbox:true,backgroundThrottling:false}});
  const jsErrors=[];win.webContents.on('console-message',event=>{if(event.level==='error')jsErrors.push(event.message);});
  await win.loadFile(fixture);
  let frame;
  const run=code=>frame.executeJavaScript(code,true);
  const waitFor=async code=>{for(let i=0;i<100;i++){if(frame&&await run(code))return;await sleep(40);}throw Error('UI wait failed: '+code);};
  for(let i=0;i<60;i++){frame=win.webContents.mainFrame.frames.find(f=>f.url.startsWith('about:srcdoc'));if(frame)break;await sleep(40);}
  assert(frame);await waitFor("!document.getElementById('workspace').classList.contains('hidden')");
  assert.equal(await run("document.getElementById('revision').textContent"),'v2');
  await run("document.getElementById('parts').value='collar';document.getElementById('parts').dispatchEvent(new Event('change',{bubbles:true}));");
  await waitFor("document.getElementById('part-prompt').value.includes('不对称')");
  await fs.mkdir(path.join(root,'assets'),{recursive:true});
  const paintFrame=async()=>{await run('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');await sleep(120);};
  await paintFrame();
  await fs.writeFile(path.join(root,'assets','screenshot-light.png'),(await win.webContents.capturePage({x:0,y:0,width:1024,height:880})).toPNG());
  await run("document.getElementById('part-prompt').value='窄领，缝一条银线';document.getElementById('part-prompt').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('save').click();");
  await waitFor("document.getElementById('revision').textContent==='v3'");
  let latest=await client.callTool({name:'garment_project',arguments:{action:'get',project_id:project}});
  assert.equal(latest.structuredContent.document.scene.parts.find(p=>p.id==='collar').fill,'#7e9daa');
  // A slow save disables inputs, preventing typed-ahead changes from being discarded.
  delay=250;await run("document.getElementById('part-prompt').value='保留细银边';document.getElementById('part-prompt').dispatchEvent(new Event('input'));document.getElementById('save').click();");
  assert(await run("document.getElementById('part-prompt').disabled"));await waitFor("document.getElementById('revision').textContent==='v4'");delay=0;
  // Another writer changes a field while this panel has a different field unsaved.
  await run("document.getElementById('part-prompt').value='我的未保存草稿';document.getElementById('part-prompt').dispatchEvent(new Event('input'));");
  await client.callTool({name:'garment_edit',arguments:{project_id:project,expected_revision:4,patch:{parts:[{id:'collar',fill:'#779999'}]}}});
  await run("document.getElementById('save').click();");await waitFor("document.getElementById('status').textContent.includes('版本冲突')");
  assert.equal(await run("document.getElementById('part-prompt').value"),'我的未保存草稿');
  await run("document.getElementById('refresh').click();");await waitFor("document.getElementById('revision').textContent==='v5'");
  assert.equal(await run("document.getElementById('part-prompt').value"),'我的未保存草稿');
  await run("document.getElementById('save').click();");await waitFor("document.getElementById('revision').textContent==='v6'");
  latest=await client.callTool({name:'garment_project',arguments:{action:'get',project_id:project}});assert.equal(latest.structuredContent.document.scene.parts.find(p=>p.id==='collar').fill,'#779999');
  // Theme notification, narrow layout and historical version are all real DOM checks.
  await win.webContents.executeJavaScript("document.querySelector('iframe').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{theme:'dark',styles:{variables:{'--color-background-primary':'#181818','--color-background-secondary':'#252525','--color-text-primary':'#f2f2f2','--color-text-secondary':'#c2c2c2','--color-border-primary':'#3c3c3c','--cardbush-accent':'#b7d9c3'}}}},'*');");
  await waitFor("getComputedStyle(document.documentElement).colorScheme==='dark'");
  assert.equal(await run("getComputedStyle(document.body).backgroundColor"),'rgb(24, 24, 24)');
  assert.equal(await run("getComputedStyle(document.body).color"),'rgb(242, 242, 242)');
  assert.equal(await run("getComputedStyle(document.querySelector('.canvas')).backgroundColor"),'rgb(255, 255, 255)', 'design canvas stays color-neutral');
  await paintFrame();
  await fs.writeFile(path.join(root,'assets','screenshot-dark.png'),(await win.webContents.capturePage({x:0,y:0,width:1024,height:880})).toPNG());
  win.setSize(420,980);await sleep(100);assert(await run("document.documentElement.scrollWidth<=innerWidth+1"));
  await run("document.getElementById('history').value='2';document.getElementById('history').dispatchEvent(new Event('change'));");
  await waitFor("document.getElementById('revision').textContent==='v2'");assert(await run("document.getElementById('generate').disabled"));assert(await run("document.getElementById('part-prompt').disabled"));
  await run("document.getElementById('restore').click();");await waitFor("document.getElementById('revision').textContent==='v7'");
  await run("document.getElementById('instruction').value='右袖改成披肩';document.getElementById('ask').click();");
  await waitFor("document.getElementById('status').textContent.includes('请求已交给对话')");assert(messages.at(-1).content[0].text.includes('右袖改成披肩'));
  // Generation prepares a frozen packet and asks the host; it never calls a provider here.
  await run("document.getElementById('generate').click();");await waitFor("document.querySelectorAll('.job').length===1 && !document.getElementById('generate').disabled");
  assert(messages.at(-1).content[0].text.includes('我确认衣服设计'));assert(messages.at(-1).content[0].text.includes('v7'));
  // Use a local vector-rendered fixture to test result viewing, without paid generation.
  latest=await client.callTool({name:'garment_project',arguments:{action:'get',project_id:project}});
  const job=latest._meta.garment.jobs[0];
  const preview=await client.callTool({name:'garment_preview',arguments:{project_id:project}}),fixturePath=JSON.parse(preview.content[0].text).png;
  await client.callTool({name:'garment_render',arguments:{action:'claim',project_id:project,job_id:job.id}});
  await client.callTool({name:'garment_render',arguments:{action:'attach',project_id:project,job_id:job.id,path:fixturePath,provider:'UI-test-fixture-not-generated'}});
  const review=await client.callTool({name:'garment_review',arguments:{action:'context',project_id:project,job_id:job.id}});assert.equal(review.content.filter(c=>c.type==='image').length,2);
  await client.callTool({name:'garment_review',arguments:{action:'save',project_id:project,job_id:job.id,findings:[{kind:'uncertain',observation:'矢量测试夹具，未执行真实生图验收'}]}});
  await run("document.getElementById('refresh').click();");await waitFor("document.querySelector('.findings')!==null");
  await run("[...document.querySelectorAll('.job button')].find(b=>b.textContent==='查看效果').click();");
  await waitFor("document.querySelector('.job img')?.naturalWidth>0");
  assert.equal(jsErrors.length,0,jsErrors.join('\n'));
  console.log('PASS: MCP App selection/save, slow saves, conflict draft recovery, theme, 420px layout, history/restore, chat handoff, confirmed generation packet, image loading and review display.');
 }catch(error){console.error(error.stack);process.exitCode=1;}
 finally{if(client)await client.close();if(win)win.destroy();app.exit(process.exitCode||0);}
});
