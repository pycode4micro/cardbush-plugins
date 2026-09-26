import { parentPort, workerData } from 'node:worker_threads';
import { parseDocument } from 'htmlparser2';
import { extractRawText } from 'mammoth';
import { extractText, getDocumentProxy } from 'unpdf';

const MAX_TEXT = 2_000_000;
globalThis.fetch = async () => { throw new Error('文档解析不允许联网。'); };

function htmlText(html) {
  const root = parseDocument(html, { decodeEntities: true });
  const skip = new Set(['script','style','template','noscript','iframe','object','svg']);
  const block = new Set(['p','div','br','hr','li','tr','table','section','article','h1','h2','h3','h4','pre']);
  function walk(node) {
    if (node.type === 'text') return node.data;
    if (skip.has(node.name) || node.attribs?.hidden !== undefined || node.attribs?.['aria-hidden'] === 'true') return '';
    const text = (node.children || []).map(walk).join('');
    return block.has(node.name) ? '\n' + text + '\n' : text;
  }
  return walk(root).replace(/[ \t]+/g, ' ').replace(/\n\s*\n\s*\n/g, '\n\n');
}

async function extract({ bytes, extension }) {
  const buffer = Buffer.from(bytes);
  if (extension === '.pdf') {
    const pdf = await getDocumentProxy(new Uint8Array(buffer), { isEvalSupported: false, useSystemFonts: false, verbosity: 0 });
    try {
      if (pdf.numPages > 500) throw new Error('demo 每份 PDF 最多 500 页，请拆分后导入。');
      const result = await extractText(pdf, { mergePages: false });
      const segments = result.text.map((text, i) => ({ page: i + 1, text }));
      const emptyPages = segments.filter(s => !s.text.trim()).map(s => s.page);
      return { segments, warnings: emptyPages.length ? [`${emptyPages.length} 页没有提取出文字（例如扫描页），本次未索引这些页面，需要 OCR。`] : [] };
    } finally { await pdf.loadingTask.destroy(); }
  }
  if (extension === '.docx') {
    const result = await extractRawText({ buffer }, { externalFileAccess: false });
    return { segments: [{ text: result.value }], warnings: result.messages.map(m => String(m.message)).slice(0, 10) };
  }
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(buffer); }
  catch { throw new Error('文本文件不是 UTF-8 编码，请先转换编码。'); }
  if (extension === '.html' || extension === '.htm') text = htmlText(text);
  if (text.includes('\0')) throw new Error('文件含二进制数据，不是可索引的文本。');
  return { segments: [{ text }], warnings: [] };
}

try {
  const result = await extract(workerData);
  if (result.segments.reduce((sum,s) => sum + s.text.length, 0) > MAX_TEXT) throw new Error('提取文本超过 200 万字符，请拆分文档。');
  if (!result.segments.some(s => s.text.trim())) throw new Error('没有提取到文本。扫描 PDF 需要先做 OCR；空文档未建立索引。');
  parentPort.postMessage({ result });
} catch (error) { parentPort.postMessage({ error: error.message || '文档解析失败。' }); }
