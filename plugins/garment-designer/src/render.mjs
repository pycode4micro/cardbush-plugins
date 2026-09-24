import { promises as fs } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { Resvg, initWasm } from '@resvg/resvg-wasm';
import { renderSvg, partSvg, escapeXml } from './model.mjs';

let ready;
async function initialize(){
  if(!ready) ready=(async()=>{
    const here=path.dirname(fileURLToPath(import.meta.url));
    const wasm=await fs.readFile(path.join(here,'index_bg.wasm')).catch(()=>fs.readFile(path.join(here,'../node_modules/@resvg/resvg-wasm/index_bg.wasm')));
    await initWasm(wasm);
    const candidates=process.platform==='win32'
      ? [path.join(process.env.SystemRoot||'C:\\Windows','Fonts','msyh.ttc')]
      : process.platform==='darwin'?['/System/Library/Fonts/PingFang.ttc']
      : ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'];
    const fonts=[];
    for(const filename of candidates){try{fonts.push(await fs.readFile(filename));break;}catch(error){if(error.code!=='ENOENT')throw error;}}
    return fonts;
  })();
  return ready;
}
export async function raster(svg,width=1000){
  const fonts=await initialize();
  // Thin trims and long closures must not allocate a 30,000-pixel-high bitmap.
  const tag=svg.match(/<svg\b[^>]*>/)?.[0]||'';
  const w=Number(tag.match(/\bwidth="([\d.eE+-]+)"/)?.[1])||800,h=Number(tag.match(/\bheight="([\d.eE+-]+)"/)?.[1])||1000;
  const zoom=Math.max(0.00001,Math.min(width/w,1600/h));
  const renderer=new Resvg(svg,{fitTo:{mode:'zoom',value:zoom},font:{fontBuffers:fonts},logLevel:'off'});
  try{const image=renderer.render();try{return Buffer.from(image.asPng());}finally{image.free();}}finally{renderer.free();}
}
export async function detailSvg(scene,partId){
  const part=scene.parts.find(p=>p.id===partId);if(!part)throw new Error('Unknown part.');
  const fonts=await initialize();
  const inside=partSvg(part),head=`<svg xmlns="http://www.w3.org/2000/svg" width="${scene.width}" height="${scene.height}">`;
  const renderer=new Resvg(head+inside+'</svg>',{font:{fontBuffers:fonts},logLevel:'off'});
  let bounds;
  try{const box=renderer.innerBBox();if(box){bounds={x:box.x,y:box.y,width:box.width,height:box.height};box.free();}}finally{renderer.free();}
  if(!bounds||!Number.isFinite(bounds.width)||bounds.width<1||bounds.height<1)return renderSvg(scene,{view:part.view,partId});
  const pad=Math.max(12,Math.max(bounds.width,bounds.height)*0.06);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${bounds.width+pad*2}" height="${bounds.height+pad*2}" viewBox="${bounds.x-pad} ${bounds.y-pad} ${bounds.width+pad*2} ${bounds.height+pad*2}"><rect x="${bounds.x-pad}" y="${bounds.y-pad}" width="${bounds.width+pad*2}" height="${bounds.height+pad*2}" fill="white"/>${inside}</svg>`;
}
export function overviewSvg(scene){
  const views=['front','back'].filter(view=>scene.parts.some(p=>p.view===view&&p.visible));
  const column=scene.width+40,height=scene.height+100;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${column*views.length}" height="${height}"><rect width="100%" height="100%" fill="white"/>${views.map((view,index)=>`<g transform="translate(${index*column+20} 60)"><text y="-20" font-family="sans-serif" font-size="24" fill="#444444">${view==='front'?'FRONT':'BACK'}</text>${scene.parts.filter(p=>p.view===view).sort((a,b)=>a.z-b.z).map(p=>partSvg(p)).join('')}</g>`).join('')}<title>${escapeXml(scene.title)}</title></svg>`;
}
