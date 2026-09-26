import { promises as fs } from 'node:fs';
import path from 'node:path';
import { Worker } from 'node:worker_threads';

export const SUPPORTED = ['.md','.markdown','.txt','.csv','.html','.htm','.pdf','.docx'];
export const MAX_BYTES = 16 * 1024 * 1024;
const MAX_FILES = 100;
const inside = (base, target) => { const relative = path.relative(base, target); return !relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative); };

async function permitted(filename, roots) {
  if (!path.isAbsolute(filename)) throw new Error('导入路径必须是插件运行主机上的绝对路径。');
  const actual = await fs.realpath(filename);
  if (roots && !(await Promise.all(roots.map(root => fs.realpath(root)))).some(root => inside(root, actual))) throw new Error('路径不在管理员允许的导入目录中。');
  return actual;
}

async function filesAt(paths, roots) {
  const files = [], skipped = [];
  let visited = 0;
  async function visit(filename, nested = false) {
    if (++visited > 3000) throw new Error('目录项过多，请选择更具体的资料目录。');
    const info = await fs.lstat(filename);
    if (info.isSymbolicLink()) { skipped.push({ path: filename, reason: '跳过符号链接' }); return; }
    const actual = await permitted(filename, roots);
    if (info.isDirectory()) {
      for (const name of (await fs.readdir(actual)).sort()) {
        if (name.startsWith('.') || ['node_modules','vendor','dist'].includes(name)) continue;
        await visit(path.join(actual, name), true);
      }
    } else if (info.isFile() && SUPPORTED.includes(path.extname(filename).toLowerCase())) {
      files.push(actual);
      if (files.length > MAX_FILES) throw new Error('每批最多 100 份文档，请按子目录分批导入。');
    } else if (!nested || skipped.length < 100) skipped.push({ path: filename, reason: '不支持的格式' });
  }
  for (const filename of paths) await visit(filename);
  return { files: [...new Set(files)], skipped };
}

export function extract(bytes, extension, timeout = 20000) {
  if (!SUPPORTED.includes(extension)) throw new Error('不支持的格式：' + extension);
  if (!bytes.length || bytes.length > MAX_BYTES) throw new Error('每份文档需为非空文件，且不超过 16 MB。');
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./extract-worker.mjs', import.meta.url), {
      workerData: { bytes, extension }, resourceLimits: { maxOldGenerationSizeMb: 192 },
      stdout: true, stderr: true,
    });
    // Parser diagnostics must never corrupt the MCP stdout transport.
    worker.stdout.resume(); worker.stderr.resume();
    let settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true; clearTimeout(timer); void worker.terminate();
      error ? reject(error) : resolve(result);
    };
    const timer = setTimeout(() => finish(new Error(`解析超过 ${Math.ceil(timeout / 1000)} 秒，已停止；请拆分或检查文档。`)), timeout);
    worker.once('message', message => finish(message.error ? new Error(message.error) : null, message.result));
    worker.once('error', error => finish(new Error('解析失败：' + error.message)));
    worker.once('exit', () => finish(new Error('解析进程未返回结果，未替换已有索引。')));
  });
}

export async function importDocuments(store, { library_id, paths = [], documents = [], files = [] }, roots) {
  store.writable(); store.library(library_id);
  const resolved = await filesAt(paths, roots), inputs = [];
  for (const filename of resolved.files) inputs.push({ source_key: filename, name: path.basename(filename), filename });
  for (const file of files) inputs.push({ source_key: file.source_key || 'upload:' + file.name, name: file.name, data_base64: file.data_base64 });
  for (const document of documents) inputs.push({ ...document, name: document.title, text: document.text });
  if (!inputs.length) return { library_id, imported: [], failed: [], skipped: resolved.skipped, note: '没有找到支持的文档。' };
  if (inputs.length > MAX_FILES) throw new Error('每批最多 100 份文档。');
  const result = { library_id, imported: [], failed: [], skipped: resolved.skipped };
  for (const item of inputs) {
    try {
      const old = store.current(library_id, item.source_key), title = item.title || item.name;
      let parsed;
      if (item.text !== undefined) parsed = { segments: [{ text: item.text }], warnings: [] };
      else {
        let bytes;
        if (item.filename) {
          await permitted(item.filename, roots);
          const file = await fs.open(item.filename, 'r');
          try {
            const info = await file.stat();
            if (!info.isFile() || info.size > MAX_BYTES) throw new Error('文件不是普通文件或超过 16 MB。');
            bytes = await file.readFile();
          } finally { await file.close(); }
        } else {
          if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(item.data_base64)) throw new Error('无效的 Base64 文件内容。');
          bytes = Buffer.from(item.data_base64, 'base64');
        }
        parsed = await extract(bytes, path.extname(item.name).toLowerCase());
      }
      const saved = store.put({ library_id, source_key: item.source_key, title, segments: parsed.segments, tags: item.tags || [], expected_revision: old?.revision || 0 });
      result.imported.push({ ...saved, warnings: parsed.warnings });
    } catch (error) { result.failed.push({ source: item.source_key, error: error.message }); }
  }
  return result;
}
