import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { KnowledgeAPI } from './api.mjs';
import { seedDemo } from './samples.mjs';

// Preview only. Production Agent access uses stdio MCP under the host's identity.
const root = path.resolve(process.env.KNOWLEDGE_DEMO_DATA_DIR || '.demo-data');
const api = new KnowledgeAPI({ root, readOnly: false, allowed: undefined, importRoots: [] });
await seedDemo(api);
const token = randomBytes(32).toString('hex'), nonce = randomBytes(18).toString('base64');
const template = await readFile(new URL('./library.html', import.meta.url), 'utf8');
const html = template.replace('<script>', `<script nonce="${nonce}">window.__DEMO_TOKEN__=${JSON.stringify(token)};`);
let origin;
const server = createServer(async (request, response) => {
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'`);
  const json = (status, value) => { response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }); response.end(JSON.stringify(value)); };
  if (request.headers.host !== new URL(origin).host) { json(403, { error: 'Invalid host' }); return; }
  if (request.method === 'GET' && request.url === '/') { response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); response.end(html); return; }
  if (request.method !== 'POST' || request.url !== '/api/tool') { json(404, { error: 'Not found' }); return; }
  if (request.headers.origin !== origin || request.headers['x-knowledge-demo'] !== token) { json(403, { error: 'Invalid preview session' }); return; }
  if (!request.headers['content-type']?.startsWith('application/json')) { json(415, { error: 'Expected JSON' }); return; }
  try {
    let size = 0; const parts = [];
    for await (const part of request) { size += part.length; if (size > 24 * 1024 * 1024) throw new Error('请求超过 24 MB。'); parts.push(part); }
    const body = JSON.parse(Buffer.concat(parts).toString('utf8'));
    // The browser preview intentionally cannot scan arbitrary host paths.
    if (body.name === 'knowledge_import' && body.arguments?.paths?.length) throw new Error('网页 demo 仅支持上传和文本。主机目录导入请使用 MCP 插件及管理员允许的目录。');
    json(200, await api.call(body.name, body.arguments));
  } catch (error) { json(400, { error: error.message }); }
});
server.requestTimeout = 60000;
await new Promise(resolve => server.listen(Number(process.env.PORT || 0), '127.0.0.1', resolve));
origin = `http://127.0.0.1:${server.address().port}`;
await mkdir(root, { recursive: true });
await writeFile(path.join(root, 'preview.json'), JSON.stringify({ url: origin, pid: process.pid, data: root }, null, 2));
console.log(`Knowledge Library demo: ${origin}\nSynthetic sample data: ${root}`);
const stop = () => { server.close(() => { api.store.close(); process.exit(0); }); };
process.on('SIGINT', stop); process.on('SIGTERM', stop);
