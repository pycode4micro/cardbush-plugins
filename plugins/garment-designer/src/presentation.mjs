import { promises as fs } from 'node:fs';
import path from 'node:path';
import PDFDocument from 'pdfkit';
import SVGtoPDF from 'svg-to-pdfkit';
import * as fontkit from 'fontkit';
import pptxgen from 'pptxgenjs';
import JSZip from 'jszip';
import { Store, immutableWrite, sha256 } from './store.mjs';
import { sceneSchema, renderSvg, escapeXml } from './model.mjs';
import { overviewSvg, detailSvg, raster } from './render.mjs';
import { presentationSchema, presentationSlideSchema } from './presentation-schema.mjs';

const W = 960, H = 540;
const labels = {
  zh: { type: '服装设计方案', concept: '设计方向', views: '正背面款式', palette: '配色方案', summary: '方案回顾', vector: '矢量款式 · 非生产纸样', notes: '讲解备注', previous: '上一页', next: '下一页', print: '打印 / PDF', theme: '切换主题' },
  en: { type: 'GARMENT DESIGN', concept: 'Design direction', views: 'Front and back', palette: 'Color palette', summary: 'Design recap', vector: 'Vector concept · Not a production pattern', notes: 'Speaker notes', previous: 'Previous', next: 'Next', print: 'Print / PDF', theme: 'Theme' },
};
const short = (text, size = 90) => text.length > size ? text.slice(0, size - 1) + '…' : text;
const unique = list => [...new Set(list)];

export function defaultSlides(doc, language = 'zh') {
  const l = labels[language], scene = doc.scene, visible = scene.parts.filter(p => p.visible);
  const details = visible.filter(p => p.prompt || p.requirements.length).slice(0, 3);
  const intro = [scene.brief, scene.globalPrompt].filter(Boolean);
  const slides = [
    { title: short(scene.title, 60), visual: 'overview', bullets: intro.map(s => short(s)), notes: intro.join('\n\n') },
    { title: l.views, visual: 'overview', bullets: unique(visible.map(p => p.name)).slice(0, 4).map(s => short(s, 60)) },
    ...details.map(p => ({ title: short(p.name, 60), visual: 'part', part_id: p.id, bullets: [p.prompt, ...p.requirements].filter(Boolean).slice(0, 3).map(s => short(s, 80)), notes: [p.prompt, ...p.requirements].filter(Boolean).join('\n').slice(0, 4000) })),
    { title: l.palette, visual: 'palette', bullets: scene.globalPrompt ? [short(scene.globalPrompt, 120)] : [], notes: scene.globalPrompt.slice(0, 4000) },
  ];
  return slides.map(s => presentationSlideSchema.parse({ ...s, notes: s.notes?.slice(0, 4000) }));
}

function paletteSvg(scene) {
  const colors = unique(scene.parts.filter(p => p.visible).flatMap(p => [p.fill, ...p.elements.map(e => e.fill).filter(Boolean)])
    .filter(c => c !== 'none').map(c => c.length === 4 ? '#' + [...c.slice(1)].map(n => n + n).join('') : c).map(c => c.toUpperCase()));
  // Include every saved fill color; dense palettes remain available in the SVG.
  const columns = Math.min(6, Math.max(1, Math.ceil(Math.sqrt(colors.length * 1.5)))), rows = Math.max(1, Math.ceil(colors.length / columns));
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${columns * 150}" height="${rows * 145}" viewBox="0 0 ${columns * 150} ${rows * 145}"><rect width="100%" height="100%" fill="white"/>${colors.map((c, i) => `<g transform="translate(${i % columns * 150 + 15} ${Math.floor(i / columns) * 145 + 10})"><rect width="120" height="92" rx="8" fill="${c}" stroke="#D9D9D9"/><text x="60" y="118" text-anchor="middle" font-family="sans-serif" font-size="18" fill="#202020">${c}</text></g>`).join('')}</svg>`;
}

function dimensions(svg) {
  const root = svg.match(/^<svg\b[^>]*>/)?.[0] || '';
  return { width: Number(root.match(/\bwidth="([\d.]+)"/)?.[1]) || 800, height: Number(root.match(/\bheight="([\d.]+)"/)?.[1]) || 1000 };
}
function contain(svg, box) {
  const d = dimensions(svg), scale = Math.min(box.w / d.width, box.h / d.height);
  return { x: box.x + (box.w - d.width * scale) / 2, y: box.y + (box.h - d.height * scale) / 2, w: d.width * scale, h: d.height * scale };
}
const visualBox = { x: 44, y: 132, w: 490, h: 346 };

async function prepare(doc, input) {
  doc = { ...doc, scene: sceneSchema.parse(doc.scene) };
  if (!doc.scene.parts.some(p => p.visible)) throw new Error('No visible design parts to present.');
  const slides = input.slides ?? defaultSlides(doc, input.language);
  const cache = new Map();
  for (const slide of slides) {
    const part = slide.part_id && doc.scene.parts.find(p => p.id === slide.part_id);
    if (slide.visual === 'part' && (!part || !part.visible)) throw new Error(`Unknown or hidden design part: ${slide.part_id}`);
    if (['front', 'back'].includes(slide.visual) && !doc.scene.parts.some(p => p.visible && p.view === slide.visual)) throw new Error(`The saved design has no ${slide.visual} view. Do not invent one.`);
    const key = slide.visual === 'part' ? `part-${slide.part_id}` : slide.visual;
    if (!cache.has(key)) cache.set(key, slide.visual === 'part' ? await detailSvg(doc.scene, slide.part_id)
      : slide.visual === 'overview' ? overviewSvg(doc.scene) : slide.visual === 'palette' ? paletteSvg(doc.scene) : renderSvg(doc.scene, { view: slide.visual }));
  }
  return { doc, title: input.title ?? doc.scene.title, language: input.language, slides, visuals: cache };
}
const keyFor = slide => slide.visual === 'part' ? `part-${slide.part_id}` : slide.visual;
const footer = (deck, index) => `v${deck.doc.revision} · ${index + 1} / ${deck.slides.length}`;

export function renderHtml(deck) {
  const l = labels[deck.language];
  const pages = deck.slides.map((s, i) => `<section class="slide" aria-label="${escapeXml(s.title)}"><header><small>${l.type} / v${deck.doc.revision}</small><h1>${escapeXml(s.title)}</h1></header><div class="content"><figure>${deck.visuals.get(keyFor(s))}</figure><div class="copy"><ul>${s.bullets.map(t => `<li>${escapeXml(t)}</li>`).join('')}</ul></div></div><footer><span>${l.vector}</span><span>${footer(deck, i)}</span></footer></section>${s.notes ? `<aside class="notes" hidden><strong>${l.notes}</strong><p>${escapeXml(s.notes).replaceAll('\n', '<br>')}</p></aside>` : ''}`).join('');
  return `<!doctype html><html lang="${deck.language === 'zh' ? 'zh-CN' : 'en'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>${escapeXml(deck.title)}</title><style>
  :root{color-scheme:light dark;--bg:#f5f5f3;--page:#fff;--text:#222;--line:#ddd;--muted:#555;font-family:system-ui,'Microsoft YaHei','PingFang SC',sans-serif}
  @media(prefers-color-scheme:dark){:root:not([data-light]){--bg:#181818;--page:#242424;--text:#f2f2f2;--line:#444;--muted:#ccc}}
  :root[data-dark]{--bg:#181818;--page:#242424;--text:#f2f2f2;--line:#444;--muted:#ccc}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}main{max-width:1040px;margin:0 auto;padding:24px 20px 100px}.slide{background:var(--page);border:1px solid var(--line);border-radius:12px;padding:32px 40px;aspect-ratio:16/9;margin:0 0 24px;display:flex;flex-direction:column;scroll-margin:20px}header small{font-size:12px;letter-spacing:.12em;color:var(--muted)}h1{font-size:clamp(22px,3vw,32px);line-height:1.3;margin:12px 0 20px;overflow-wrap:anywhere}.content{display:grid;grid-template-columns:56% 1fr;gap:30px;flex:1;min-height:0}figure{margin:0;background:white;border-radius:8px;display:flex;align-items:center;justify-content:center;min-height:0;overflow:hidden}figure svg{display:block;width:100%;height:100%;max-height:350px}ul{padding-left:22px;margin:12px 0}li{font-size:18px;line-height:1.6;margin:0 0 16px;overflow-wrap:anywhere}footer{display:flex;justify-content:space-between;gap:20px;color:var(--muted);font-size:11px;margin-top:20px}.notes{border-left:3px solid var(--line);margin:0 0 24px;padding:12px 20px;overflow-wrap:anywhere}.notes p{line-height:1.7}nav{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:8px;padding:8px;border:1px solid var(--line);border-radius:10px;background:var(--page);max-width:calc(100% - 20px)}button{font:inherit;white-space:nowrap;border:1px solid var(--line);border-radius:6px;background:var(--page);color:var(--text);padding:7px 12px;cursor:pointer}button:focus-visible{outline:2px solid currentColor;outline-offset:2px}
  @media(max-width:650px){main{padding:12px 10px 100px}.slide{aspect-ratio:auto;padding:22px}.content{grid-template-columns:1fr;gap:12px}figure{height:320px}li{font-size:16px}nav{flex-wrap:wrap;width:max-content;justify-content:center}button{font-size:12px;padding:5px 8px}}
  @media print{@page{size:320mm 180mm;margin:0}:root{--page:white;--text:#222;--muted:#555;--line:#ddd;color-scheme:light}body,main{background:white;margin:0;padding:0;max-width:none}.slide{width:320mm;height:180mm;aspect-ratio:auto;border:0;border-radius:0;margin:0;break-after:page;padding:12mm 14mm;print-color-adjust:exact}.slide:last-of-type{break-after:auto}.content{grid-template-columns:56% 1fr}figure svg{max-height:116mm}nav,.notes{display:none!important}h1{font-size:28px}}
  </style></head><body><main>${pages}</main><nav aria-label="Presentation"><button data-action="previous">${l.previous}</button><button data-action="next">${l.next}</button><button data-action="notes">${l.notes}</button><button data-action="theme">${l.theme}</button><button data-action="print">${l.print}</button></nav><script>
  const pages=[...document.querySelectorAll('.slide')];let index=0;
  const fit=()=>{for(const page of pages){const copy=page.querySelector('.copy'),items=[...copy.querySelectorAll('li')];let size=matchMedia('(max-width:650px)').matches?16:18;items.forEach(li=>li.style.fontSize=size+'px');if(innerWidth<=650)continue;const content=page.querySelector('.content');while(size>13&&copy.scrollHeight>content.clientHeight){size--;items.forEach(li=>li.style.fontSize=size+'px')}}};
  const go=d=>{index=Math.max(0,Math.min(pages.length-1,index+d));pages[index].scrollIntoView({behavior:'auto',block:'start'})};
  const watcher=new IntersectionObserver(entries=>{for(const e of entries)if(e.isIntersecting)index=pages.indexOf(e.target)},{threshold:.6});pages.forEach(p=>watcher.observe(p));let lastWidth=0;new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(width!==lastWidth){lastWidth=width;requestAnimationFrame(fit)}}).observe(document.querySelector('main'));document.fonts.ready.then(fit);addEventListener('beforeprint',fit);
  document.querySelector('nav').addEventListener('click',e=>{const action=e.target.dataset.action;if(action==='previous')go(-1);if(action==='next')go(1);if(action==='print')print();if(action==='notes')document.querySelectorAll('.notes').forEach(n=>n.hidden=!n.hidden);if(action==='theme'){const root=document.documentElement,dark=root.hasAttribute('data-dark')||!root.hasAttribute('data-light')&&matchMedia('(prefers-color-scheme:dark)').matches;root.toggleAttribute('data-dark',!dark);root.toggleAttribute('data-light',dark)}});addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='PageDown'){e.preventDefault();go(1)}if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();go(-1)}});
  </script></body></html>`;
}

async function selectFont(deck, explicit = process.env.GARMENT_PRESENTATION_FONT) {
  const candidates = explicit ? [explicit] : process.platform === 'win32'
    ? [path.join(process.env.SystemRoot || 'C:\\Windows', 'Fonts', 'msyh.ttc'), path.join(process.env.SystemRoot || 'C:\\Windows', 'Fonts', 'arial.ttf')]
    : process.platform === 'darwin' ? ['/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/Supplemental/Arial.ttf']
      : ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc', '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'];
  const text = [labels[deck.language].type, labels[deck.language].vector, deck.title, ...deck.slides.flatMap(s => [s.title, ...s.bullets]), ...deck.doc.scene.parts.filter(p => p.visible).flatMap(p => p.elements.filter(e => e.type === 'text').map(e => e.text))].join('');
  const codes = unique([...text].filter(c => !/\s/u.test(c)).map(c => c.codePointAt(0)));
  for (const filename of candidates) {
    try {
      const bytes = await fs.readFile(filename), collection = fontkit.create(bytes), fonts = collection.fonts || [collection];
      const font = fonts.find(f => codes.every(code => f.hasGlyphForCodePoint(code)));
      if (font) return { bytes, family: font.postscriptName, filename };
    } catch (error) { if (explicit || !['ENOENT', 'EACCES'].includes(error.code)) throw error; }
  }
  throw new Error('PDF needs a font covering the presentation text. Install Noto Sans CJK or set GARMENT_PRESENTATION_FONT to an existing TTF/OTF/TTC font. HTML and PPTX do not require PDF font installation.');
}

async function renderPdf(deck, fontPath) {
  const font = await selectFont(deck, fontPath), chunks = [];
  const doc = new PDFDocument({ autoFirstPage: false, font: null, pdfVersion: '1.7', margin: 0, compress: true, info: { Title: deck.title, Creator: 'Garment Designer', Subject: `Design ${deck.doc.id} revision ${deck.doc.revision}` } });
  const complete = new Promise((resolve, reject) => { doc.on('data', data => chunks.push(data)); doc.on('end', () => resolve(Buffer.concat(chunks))); doc.on('error', reject); });
  // No default font assets are read from an installed node_modules directory.
  doc.registerFont('Design', font.bytes, font.family);
  for (const [i, slide] of deck.slides.entries()) {
    doc.addPage({ size: [W, H], margin: 0 });
    doc.rect(0, 0, W, H).fill('#FFFFFF');
    doc.font('Design').fontSize(11).fillColor('#555555').text(`${labels[deck.language].type} / v${deck.doc.revision}`, 44, 30, { width: 870 });
    let titleSize = 30;
    while (titleSize > 20 && doc.fontSize(titleSize).heightOfString(slide.title, { width: 870 }) > 65) titleSize--;
    doc.fillColor('#222222').fontSize(titleSize).text(slide.title, 44, 56, { width: 870, height: 70 });
    const svg = deck.visuals.get(keyFor(slide)), box = contain(svg, visualBox);
    SVGtoPDF(doc, svg, box.x, box.y, { width: box.w, height: box.h, preserveAspectRatio: 'xMidYMid meet', fontCallback: () => 'Design', warningCallback: message => { throw new Error('SVG export: ' + message); } });
    let size = 20;
    const lineGap=()=>size<=15?2:4, paragraphGap=()=>size<=15?8:16;
    const height = () => slide.bullets.reduce((sum, t) => sum + doc.font('Design').fontSize(size).heightOfString(t, { width: 320, lineGap: lineGap() }) + paragraphGap(), 0);
    while (size > 13 && height() > 328) size--;
    if (height() > 328) throw new Error(`Slide "${slide.title}" is too dense. Split it or move text into notes.`);
    let y = 145;
    for (const text of slide.bullets) {
      doc.circle(567, y + size * .65, 2.5).fill('#333333');
      doc.font('Design').fontSize(size).fillColor('#222222').text(text, 582, y, { width: 320, lineGap: lineGap() });
      y += doc.heightOfString(text, { width: 320, lineGap: lineGap() }) + paragraphGap();
    }
    doc.moveTo(44, 497).lineTo(916, 497).strokeColor('#DDDDDD').lineWidth(.6).stroke();
    doc.font('Design').fontSize(10).fillColor('#555555').text(labels[deck.language].vector, 44, 510, { width: 760 });
    doc.text(footer(deck, i), 804, 510, { width: 112, align: 'right' });
  }
  doc.end();
  return complete;
}

async function renderPptx(deck) {
  const pptx = new pptxgen();
  pptx.defineLayout({ name: 'DESIGN', width: W / 72, height: H / 72 }); pptx.layout = 'DESIGN';
  pptx.title = deck.title; pptx.subject = `Design ${deck.doc.id} revision ${deck.doc.revision}`; pptx.author = 'Garment Designer'; pptx.lang = deck.language === 'zh' ? 'zh-CN' : 'en-US';
  const fontFace = deck.language === 'zh' ? 'Microsoft YaHei' : 'Arial';
  pptx.theme = { headFontFace: fontFace, bodyFontFace: fontFace, lang: pptx.lang };
  for (const [i, page] of deck.slides.entries()) {
    const slide = pptx.addSlide(); slide.background = { color: 'FFFFFF' };
    const text = (value, x, y, w, h, size, extra = {}) => slide.addText(value, { x: x / 72, y: y / 72, w: w / 72, h: h / 72, fontFace, fontSize: size, color: '222222', margin: 0, breakLine: false, valign: 'top', fit: 'shrink', ...extra });
    text(`${labels[deck.language].type} / v${deck.doc.revision}`, 44, 30, 870, 18, 11, { color: '555555' });
    text(page.title, 44, 56, 870, 70, 30, { bold: true });
    const svg = deck.visuals.get(keyFor(page)), box = contain(svg, visualBox);
    slide.addImage({ data: 'image/svg+xml;base64,' + Buffer.from(svg).toString('base64'), x: box.x / 72, y: box.y / 72, w: box.w / 72, h: box.h / 72, altText: page.title });
    let bodySize=20;
    const estimate=()=>page.bullets.reduce((total,t)=>total+Math.ceil([...t].reduce((n,c)=>n+(/[\u0000-\u00ff]/.test(c)?.6:1),0)*bodySize/320)*bodySize*1.3+14,0);
    while(bodySize>13&&estimate()>330)bodySize--;
    if (page.bullets.length) text(page.bullets.map(t => ({ text: '• '+t, options: { breakLine: true } })), 563, 145, 340, 330, bodySize, { paraSpaceAfterPt: bodySize<=15?8:14 });
    slide.addShape(pptx.ShapeType.line, { x: 44 / 72, y: 497 / 72, w: 872 / 72, h: 0, line: { color: 'DDDDDD', width: .6 } });
    text(labels[deck.language].vector, 44, 510, 760, 16, 10, { color: '555555' });
    text(footer(deck, i), 804, 510, 112, 16, 10, { align: 'right', color: '555555' });
    slide.addNotes(page.notes || page.bullets.join('\n'));
  }
  const zip = await JSZip.loadAsync(await pptx.write({ outputType: 'nodebuffer', compression: true }));
  // PptxGenJS keeps SVGs in Office, but Node supplies a placeholder for older viewers.
  // Replace that compatibility image with a real local rendering of the same SVG.
  for (let i = 1; i <= deck.slides.length; i++) {
    const xml = await zip.file(`ppt/slides/slide${i}.xml`).async('string');
    const rels = await zip.file(`ppt/slides/_rels/slide${i}.xml.rels`).async('string');
    const targets = new Map([...rels.matchAll(/<Relationship\s[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"[^>]*\/>/g)].map(m => [m[1], path.posix.normalize('ppt/slides/' + m[2])]));
    for (const picture of xml.matchAll(/<p:pic\b[\s\S]*?<\/p:pic>/g)) {
      const pngId = picture[0].match(/<a:blip\s+r:embed="([^"]+)"/)?.[1];
      const svgId = picture[0].match(/<asvg:svgBlip\b[^>]*r:embed="([^"]+)"/)?.[1];
      if (!svgId) continue;
      const pngFile = targets.get(pngId), svgFile = targets.get(svgId);
      if (!pngFile?.endsWith('.png') || !svgFile?.endsWith('.svg') || !zip.file(svgFile)) throw new Error('PPTX vector/preview binding is missing.');
      zip.file(pngFile, await raster(await zip.file(svgFile).async('string'), 1200));
    }
  }
  return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
}

async function saveOnce(filename, data) {
  try { await immutableWrite(filename, data); } catch (error) { if (error.code !== 'EEXIST') throw error; }
}
const pending = new Map();
export async function exportPresentation(store, raw, { outputDir, fontPath } = {}) {
  const input = presentationSchema.parse(raw), doc = await store.get(input.project_id, input.revision);
  const deck = await prepare(doc, input);
  const fingerprint = sha256(JSON.stringify({ exporter: 1, id: doc.id, revision: doc.revision, scene: doc.scene, title: deck.title, language: deck.language, slides: deck.slides })).slice(0, 16);
  const directory = path.join(outputDir ? path.resolve(outputDir) : path.join(store.projectDir(doc.id), 'exports', 'presentations'), `v${doc.revision}-${fingerprint}`);
  // Different exports never share filenames; repeated requests reuse complete files.
  const key = `${directory}:${unique(input.formats).sort().join(',')}:${fontPath || process.env.GARMENT_PRESENTATION_FONT || ''}`;
  if (pending.has(key)) return pending.get(key);
  const work = (async () => {
    const files = [], errors = [];
    for (const [key, svg] of deck.visuals) await saveOnce(path.join(directory, key + '.svg'), svg);
    await saveOnce(path.join(directory, 'outline.json'), { project_id: doc.id, revision: doc.revision, title: deck.title, language: deck.language, slides: deck.slides });
    for (const format of unique(input.formats)) {
      const filename = path.join(directory, `design.${format}`);
      try {
        try { await fs.access(filename); } catch (error) {
          if (error.code !== 'ENOENT') throw error;
          const data = format === 'html' ? renderHtml(deck) : format === 'pdf' ? await renderPdf(deck, fontPath) : await renderPptx(deck);
          await saveOnce(filename, data);
        }
        files.push({ format, path: filename, bytes: (await fs.stat(filename)).size });
      } catch (error) { errors.push({ format, message: error.message }); }
    }
    return { projectId: doc.id, revision: doc.revision, title: deck.title, slides: deck.slides.length, directory, files, errors,
      outline: path.join(directory, 'outline.json'), vectors: [...deck.visuals.keys()].map(key => path.join(directory, key + '.svg')),
      note: 'PPTX contains editable text, embedded SVG and PNG compatibility previews. HTML is offline. PDF contains vector artwork and embedded text fonts. Design colors are unchanged; this is a concept presentation, not a manufacturing pattern.' };
  })();
  pending.set(key, work);
  try { return await work; } finally { if (pending.get(key) === work) pending.delete(key); }
}

export async function runPresentationCli(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!['--project-id', '--revision', '--formats', '--outline', '--data-dir', '--output-dir', '--font', '--language'].includes(key) || !value || value.startsWith('--')) throw new Error('Use --project-id ID --revision N [--formats pdf,pptx,html] [--outline plan.json] [--data-dir DIR] [--output-dir DIR] [--font FONT] [--language zh|en].');
    options[key.slice(2)] = value;
  }
  const outline = options.outline ? JSON.parse(await fs.readFile(path.resolve(options.outline), 'utf8')) : {};
  const result = await exportPresentation(new Store(options['data-dir']), { ...outline, project_id: options['project-id'] || outline.project_id,
    revision: options.revision ? Number(options.revision) : outline.revision, formats: options.formats ? options.formats.split(',') : outline.formats, language: options.language || outline.language || 'zh' }, { outputDir: options['output-dir'], fontPath: options.font });
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
  if (result.errors.length) process.exitCode = 1;
}
