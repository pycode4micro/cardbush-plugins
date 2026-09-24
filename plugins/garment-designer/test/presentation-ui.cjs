// Real offline HTML renderer and real PDF/PPTX files; no user projects or providers.
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs/promises');
const path=require('node:path');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.test-data','presentation-ui');
app.setPath('userData',path.join(out,'profile'));
let win;
app.whenReady().then(async()=>{
  try{
    const {Store}=await import(pathToFileURL(path.join(root,'src/store.mjs')));
    const {template}=await import(pathToFileURL(path.join(root,'src/templates.mjs')));
    const {exportPresentation}=await import(pathToFileURL(path.join(root,'dist/presentation.mjs')));
    const store=new Store(path.join(out,'data'));
    const scene=template('jacket','轻雾蓝 · 不对称领外套','用日常廓形承载非对称领口细节。');
    scene.globalPrompt='雾蓝主体，哑光材质意向，领片用稍深的同色系区分。';
    for(const part of scene.parts)if(part.id!=='buttons')part.fill=part.id==='collar'?'#7e9daa':'#b8cdd5';
    scene.parts.find(p=>p.id==='collar').elements=[{type:'path',d:'M340 145 L395 195 L335 270 L300 180 Z M460 145 L405 195 L450 250 L495 180 Z'}];
    const doc=await store.create(scene);
    const slides=[
      {title:scene.title,visual:'overview',bullets:['雾蓝色主体与稍深领片形成层次','左右领片不同宽度，建立识别细节','先讨论轮廓和配色，再确认材质视觉'],notes:'这是保存版本中的矢量方案。材质仅是视觉意向，尚未做生产或实物验证。'},
      {title:'正背面款式关系',visual:'overview',bullets:['同一件外套，正背面保持整体比例一致','正面以门襟与口袋组织视觉节奏'],notes:'指出背面并未增加未经设计的装饰。'},
      {title:'领口：非对称的视觉重点',visual:'part',part_id:'collar',bullets:['左领片更宽，右领片更收敛','保留与衣身的连接位置','深浅配色突出领片关系'],notes:'放大图只展示领口这一部位，不是另一件衣服。'},
      {title:'配色与材质意向',visual:'palette',bullets:['主体为浅雾蓝','领片以同色系较深颜色区分','材质意向为哑光；矢量稿不模拟实物纹理']},
      {title:'排版边界测试：较长中文也要完整显示',visual:'front',bullets:Array.from({length:5},(_,i)=>`${i+1}、`+'设计说明用于表达已有款式的轮廓比例配色关系与部位细节，讲解中保留事实依据并避免把未验证的材质意向当作生产结论。'.slice(0,53))},
    ];
    const result=await exportPresentation(store,{project_id:doc.id,revision:1,slides});
    assert.deepEqual(result.errors,[]);assert.equal(result.files.length,3);
    await fs.mkdir(out,{recursive:true});await fs.writeFile(path.join(out,'receipt.json'),JSON.stringify(result,null,2));
    win=new BrowserWindow({show:false,width:1080,height:780,webPreferences:{contextIsolation:true,sandbox:true,backgroundThrottling:false}});
    const errors=[];win.webContents.on('console-message',event=>{if(event.level==='error')errors.push(event.message);});
    await win.loadFile(result.files.find(f=>f.format==='html').path);
    await win.webContents.executeJavaScript('document.fonts.ready');
    await new Promise(r=>setTimeout(r,150));
    const inspect=()=>win.webContents.executeJavaScript(`([...document.querySelectorAll('.slide')].map(s=>{const f=s.querySelector('footer').getBoundingClientRect(),list=s.querySelector('ul').getBoundingClientRect();return {title:s.querySelector('h1').textContent,overlap:list.bottom>f.top+1,svg:!!s.querySelector('svg'),font:parseFloat(getComputedStyle(s.querySelector('li')).fontSize)}}))`);
    const layout=await inspect();assert(layout.every(s=>s.svg&&!s.overlap&&s.font>=13),JSON.stringify(layout));
    assert.equal(await win.webContents.executeJavaScript(`([...document.querySelectorAll('figure svg')].every(svg=>{const view=svg.viewBox.baseVal;return view.width>0&&view.height>0&&[...svg.querySelectorAll('[data-part-id]')].every(p=>{const r=p.getBoundingClientRect(),b=svg.getBoundingClientRect();return r.left>=b.left-1&&r.right<=b.right+1&&r.top>=b.top-1&&r.bottom<=b.bottom+1})}))`),true,'complete vector parts stay inside their scaled viewport');
    await fs.writeFile(path.join(out,'html-overview.png'),(await win.webContents.capturePage()).toPNG());
    await win.webContents.executeJavaScript("dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'}))");
    await new Promise(r=>setTimeout(r,550));
    const navigation=await win.webContents.executeJavaScript("({top:document.querySelectorAll('.slide')[1].getBoundingClientRect().top,scroll:scrollY,index})");
    assert(Math.abs(navigation.top)<30,'keyboard advances to the next slide: '+JSON.stringify(navigation));
    await win.webContents.executeJavaScript("document.querySelector('[data-action=notes]').click();document.querySelector('[data-action=theme]').click()");
    assert.equal(await win.webContents.executeJavaScript("document.querySelector('.notes').hidden"),false);
    assert(await win.webContents.executeJavaScript("document.documentElement.hasAttribute('data-dark')||document.documentElement.hasAttribute('data-light')"));
    await win.webContents.executeJavaScript("document.querySelectorAll('.notes').forEach(n=>n.hidden=true);document.querySelectorAll('.slide')[2].scrollIntoView({block:'start'})");
    await new Promise(r=>setTimeout(r,100));await fs.writeFile(path.join(out,'html-detail.png'),(await win.webContents.capturePage()).toPNG());
    await win.webContents.executeJavaScript("document.querySelectorAll('.slide')[4].scrollIntoView({block:'start'})");
    await new Promise(r=>setTimeout(r,100));await fs.writeFile(path.join(out,'html-dense.png'),(await win.webContents.capturePage()).toPNG());
    win.setSize(430,860);await new Promise(r=>setTimeout(r,120));
    assert.equal(await win.webContents.executeJavaScript('document.documentElement.scrollWidth<=innerWidth'),true,'mobile layout must not overflow horizontally');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'PASS',checks:['vector HTML layout','dense Chinese text','keyboard navigation','notes','theme','narrow viewport'],files:result.files,layout}));
  }catch(error){console.error(error);process.exitCode=1;}finally{win?.destroy();app.exit(process.exitCode||0);}
});
