import { z } from 'zod';

export const id = z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/);
export const color = z.string().regex(/^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|none)$/);
const n = z.number().finite().min(-10000).max(10000);
const size = z.number().finite().min(0).max(10000);
const style = { fill: color.optional(), stroke: color.optional(), strokeWidth: z.number().min(0).max(30).optional(), opacity: z.number().min(0).max(1).optional() };
export const elementSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('path'), d: z.string().min(1).max(16000).regex(/^[MmLlHhVvCcSsQqTtAaZz0-9eE.,+\s-]+$/), ...style }).strict(),
  z.object({ type: z.literal('rect'), x:n, y:n, width:size, height:size, rx:size.optional(), ...style }).strict(),
  z.object({ type: z.literal('ellipse'), cx:n, cy:n, rx:size, ry:size, ...style }).strict(),
  z.object({ type: z.literal('line'), x1:n, y1:n, x2:n, y2:n, ...style }).strict(),
  z.object({ type: z.literal('polyline'), points:z.array(z.tuple([n,n])).min(2).max(256), ...style }).strict(),
  z.object({ type: z.literal('text'), x:n, y:n, text:z.string().max(200), fontSize:z.number().min(4).max(240).default(24), ...style }).strict(),
]);
export const partSchema = z.object({
  id, name:z.string().min(1).max(100), view:z.enum(['front','back']).default('front'),
  kind:z.enum(['body','collar','sleeve','cuff','pocket','closure','hem','decoration','custom']).default('custom'),
  prompt:z.string().max(6000).default(''), requirements:z.array(z.string().max(400)).max(30).default([]),
  locked:z.boolean().default(false), visible:z.boolean().default(true), z:z.number().int().min(-1000).max(1000).default(0),
  fill:color.default('#dce3d7'), stroke:color.default('#333333'), strokeWidth:z.number().min(0).max(30).default(2),
  transform:z.object({x:n.default(0),y:n.default(0),rotate:z.number().min(-360).max(360).default(0),scaleX:z.number().min(0.05).max(10).default(1),scaleY:z.number().min(0.05).max(10).default(1)}).strict().optional(),
  elements:z.array(elementSchema).min(1).max(64),
}).strict();
export const sceneSchema = z.object({
  title:z.string().min(1).max(160), brief:z.string().max(12000).default(''),
  globalPrompt:z.string().max(10000).default(''), negativePrompt:z.string().max(6000).default(''),
  width:z.number().int().min(200).max(2000).default(800), height:z.number().int().min(200).max(2000).default(1000),
  parts:z.array(partSchema).min(1).max(80),
}).strict().superRefine((scene,ctx)=>{
  if(new Set(scene.parts.map(p=>p.id)).size!==scene.parts.length) ctx.addIssue({code:'custom',message:'Part IDs must be unique.'});
  if(JSON.stringify(scene).length>750000) ctx.addIssue({code:'custom',message:'Design exceeds 750 KB.'});
});
// Zod 4 keeps defaults inside partial objects. Strip only top-level defaults so
// editing a prompt cannot silently reset a part's color, layer, view or lock.
const optionalWithoutDefault=value=>(value instanceof z.ZodDefault?value.removeDefault():value).optional();
const partialTransform=z.object(Object.fromEntries(Object.entries(partSchema.shape.transform.unwrap().shape).map(([key,value])=>[key,optionalWithoutDefault(value)]))).strict();
const partialPart = z.object(Object.fromEntries(Object.entries(partSchema.shape).map(([key,value])=>
  [key,key==='id'?value:key==='transform'?partialTransform.optional():optionalWithoutDefault(value)]
))).strict();
export const patchSchema = z.object({
  title:z.string().min(1).max(160).optional(), brief:z.string().max(12000).optional(),
  globalPrompt:z.string().max(10000).optional(), negativePrompt:z.string().max(6000).optional(),
  parts:z.array(partialPart).max(80).default([]),
  remove:z.array(id).max(80).default([]), unlock:z.array(id).max(80).default([]),
}).strict();
export const findingSchema = z.object({partId:id.optional(),kind:z.enum(['deviation','suggestion','uncertain']), observation:z.string().min(1).max(2000), proposal:z.string().max(2000).default('')}).strict();
export function editScene(scene,input){
  const patch=patchSchema.parse(input), parts=new Map(scene.parts.map(p=>[p.id,p]));
  const touched=[...patch.parts.map(p=>p.id),...patch.remove];
  if(new Set(touched).size!==touched.length) throw new Error('A part cannot be edited or removed twice in one patch.');
  for(const key of touched) if(parts.get(key)?.locked&&!patch.unlock.includes(key)) throw new Error(`Part ${key} is locked; explicitly unlock it only when the user requests that change.`);
  for(const key of patch.unlock) {if(!parts.has(key)) throw new Error(`Unknown part: ${key}`); parts.set(key,{...parts.get(key),locked:false});}
  for(const key of patch.remove) {if(!parts.delete(key)) throw new Error(`Unknown part: ${key}`);}
  for(const change of patch.parts){const previous=parts.get(change.id);parts.set(change.id,partSchema.parse({...previous,...change,...(change.transform?{transform:{...previous?.transform,...change.transform}}:{})}));}
  const {parts:changes,remove,unlock,...global}=patch;
  return sceneSchema.parse({...scene,...global,parts:[...parts.values()]});
}
export function escapeXml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));}
function attributes(values){return Object.entries(values).filter(([,v])=>v!==undefined).map(([k,v])=>` ${k}="${escapeXml(v)}"`).join('');}
export function partSvg(part,{mask=false}={}){
  if(!part.visible) return '';
  const t=part.transform;
  const transform=t?`translate(${t.x} ${t.y}) rotate(${t.rotate}) scale(${t.scaleX} ${t.scaleY})`:undefined;
  const markup=part.elements.map(element=>{
    const {type,text,points,strokeWidth,fontSize,...rest}=element;
    const props={...rest,...(mask?{fill:type==='line'?'none':'#ffffff',stroke:'#ffffff',opacity:1}:{}),'stroke-width':strokeWidth};
    if(points) props.points=points.map(p=>p.join(',')).join(' ');
    if(fontSize){props['font-size']=fontSize;props['font-family']='sans-serif';}
    return type==='text'?`<text${attributes(props)}>${escapeXml(text)}</text>`:`<${type}${attributes(props)}/>`;
  }).join('');
  return `<g${attributes({'data-part-id':part.id,fill:mask?'#ffffff':part.fill,stroke:mask?'#ffffff':part.stroke,'stroke-width':part.strokeWidth,'stroke-linejoin':'round',transform})}>${markup}</g>`;
}
export function renderSvg(scene,{view='front',partId,mask=false}={}){
  scene=sceneSchema.parse(scene);
  if(partId&&!scene.parts.some(p=>p.id===partId)) throw new Error('Unknown part.');
  const parts=scene.parts.filter(p=>p.view===view&&(!partId||p.id===partId)).sort((a,b)=>a.z-b.z);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${scene.width}" height="${scene.height}" viewBox="0 0 ${scene.width} ${scene.height}"><title>${escapeXml(scene.title)}</title><rect width="100%" height="100%" fill="${mask?'#000000':'#ffffff'}"/>${parts.map(p=>partSvg(p,{mask})).join('')}</svg>`;
}
