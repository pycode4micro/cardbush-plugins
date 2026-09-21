import { createServer, type IncomingMessage, type ServerResponse, type Server } from 'node:http';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { readFile, writeFile, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { z } from 'zod';
import { createHttpMcp } from './httpMcp.js';
import { ChatError, RoomStore, hash, secret } from './store.js';
import type { ToolName } from './schema.js';

export type ServiceOptions = { directory: string; webDirectory: string; host?: string; port?: number; webPort?: number;
  publicMcpUrl?: string; publicWebUrl?: string; writeDescriptor?: boolean };
export type Descriptor = { pid: number; service_id: string; port: number; web_port: number;
  mcp_url: string; web_url: string; control_token: string; started_at: string; bind_host: string };
const loopback = (host: string) => ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(host);
const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'" };
function json(res: ServerResponse, value: unknown, status = 200) {
  res.writeHead(status, { ...headers, 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(value));
}
async function body(req: IncomingMessage) {
  if (!req.headers['content-type']?.startsWith('application/json')) throw new ChatError('content_type', 'Expected application/json.', 415);
  const chunks: Buffer[] = []; let size = 0;
  await new Promise<void>((resolve, reject) => {
    req.on('data', chunk => {
      size += chunk.length;
      if (size > 128 * 1024) { chunks.length = 0; reject(new ChatError('body_limit', 'Request is too large.', 413)); }
      else chunks.push(chunk);
    });
    req.once('end', resolve); req.once('error', reject);
    req.once('aborted', () => reject(new ChatError('cancelled', 'Request aborted.')));
  });
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new ChatError('invalid_json', 'Invalid JSON body.'); }
}
function listen(server: Server, host: string, port: number) {
  return new Promise<number>((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => { server.off('error', reject); resolve((server.address() as { port: number }).port); });
  });
}
async function closeServer(server: Server) {
  if (!server.listening) return;
  const closed = new Promise<void>(resolve => server.close(() => resolve()));
  server.closeIdleConnections(); await closed;
}
function cookieName(room: string) {
  if (!/^[0-9a-f-]{36}$/i.test(room)) throw new ChatError('invalid_room', 'Invalid room id.');
  return `acr_${room}`;
}

export async function startService(options: ServiceOptions) {
  const host = options.host ?? '127.0.0.1';
  if (!['127.0.0.1', '::1', '0.0.0.0', '::'].includes(host)) throw new Error('Use a loopback or wildcard bind address.');
  if (!loopback(host) && (!options.publicMcpUrl || !options.publicWebUrl)) throw new Error('Network binding requires public MCP and web URLs.');
  const checkUrl = (text?: string) => {
    if (!text) return;
    const url = new URL(text);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash || url.search) throw new Error('Public URLs must be HTTP(S) without credentials/query/fragment.');
    return url;
  };
  const configuredMcp = checkUrl(options.publicMcpUrl), configuredWeb = checkUrl(options.publicWebUrl);
  if (configuredWeb && configuredWeb.pathname !== '/') throw new Error('The public web URL must use its own origin/root path.');
  const store = await RoomStore.open(options.directory);
  const files = new Map<string, { type: string; bytes: Buffer }>();
  try {
    for (const [path, file, type] of [['/', 'index.html', 'text/html'], ['/app.js', 'app.js', 'text/javascript'], ['/style.css', 'style.css', 'text/css']]) {
      files.set(path, { type, bytes: await readFile(join(options.webDirectory, file)) });
    }
  } catch (error) { await store.close(); throw error; }
  let descriptor: Descriptor;
  let stopping = false;
  const controllers = new Set<AbortController>();
  const mcp = createHttpMcp(async (name, args, signal) => {
    const value = await store.execute(name, args, signal);
    if (name === 'chatroom_human_invite') return { ...value, web_url: descriptor.web_url,
      invite_url: `${descriptor.web_url}#humen_code=${encodeURIComponent(String(value.humen_code))}` };
    if (name === 'chatroom_create' || name === 'chatroom_join') return { ...value, mcp_url: descriptor.mcp_url, web_url: descriptor.web_url };
    return value;
  });

  // No forwarded header is trusted for origin checks, operator control or rate-limit identity.
  const rates = new Map<string, { count: number; until: number }>();
  const allowedHosts = (req: IncomingMessage, publicUrl: URL | undefined, port: number) => {
    const local = [`127.0.0.1:${port}`, `localhost:${port}`, `[::1]:${port}`];
    if (![...local, ...(publicUrl ? [publicUrl.host] : [])].includes(req.headers.host ?? '')) throw new ChatError('host', 'Unrecognized Host header.', 403);
  };
  const handler = (web: boolean) => async (req: IncomingMessage, res: ServerResponse) => {
    const controller = new AbortController(); controllers.add(controller);
    const abort = () => controller.abort();
    req.once('aborted', abort); res.once('close', abort);
    try {
      if (stopping) throw new ChatError('stopping', 'Service is stopping.', 503);
      allowedHosts(req, web ? configuredWeb : configuredMcp, web ? descriptor.web_port : descriptor.port);
      const url = new URL(req.url ?? '/', `http://${req.headers.host}`);
      const origin = req.headers.origin;
      const localOrigins = [`http://127.0.0.1:${descriptor.web_port}`, `http://localhost:${descriptor.web_port}`, `http://[::1]:${descriptor.web_port}`];
      if (origin && (!web || ![...localOrigins, ...(configuredWeb ? [configuredWeb.origin] : [])].includes(origin))) throw new ChatError('origin', 'Cross-origin requests are not allowed.', 403);
      const key = req.socket.remoteAddress ?? 'unknown', now = Date.now();
      const rate = rates.get(key);
      if (rate && rate.until > now) {
        if (++rate.count > 600) throw new ChatError('rate_limit', 'Too many requests; retry later.', 429);
      } else {
        if (rates.size >= 10000) for (const [ip, entry] of rates) if (entry.until <= now) rates.delete(ip);
        if (rates.size >= 10000 && !rate) throw new ChatError('rate_limit', 'Server request capacity reached.', 429);
        rates.set(key, { count: 1, until: now + 60000 });
      }
      if (!web && url.pathname === '/health' && req.method === 'GET') return json(res, { service: 'agentchatroom', service_id: descriptor.service_id, version: '0.1.1' });
      if (!web && url.pathname === '/control/stop' && req.method === 'POST') {
        if (!loopback(req.socket.remoteAddress ?? '') || hash(req.headers.authorization ?? '') !== hash(`Bearer ${descriptor.control_token}`)) throw new ChatError('forbidden', 'Local operator credential required.', 403);
        json(res, { status: 'stopping' }); setImmediate(() => { void close(); }); return;
      }
      if (!web && url.pathname === '/mcp') {
        const parsed = req.method === 'POST' ? await body(req) : undefined;
        const bytes = parsed === undefined ? undefined : JSON.stringify(parsed);
        const request = new Request(`http://${req.headers.host}/mcp`, { method: req.method, headers: req.headers as Record<string, string>, body: bytes, signal: controller.signal });
        const response = await mcp.fetch(request, parsed);
        res.writeHead(response.status, { ...headers, ...Object.fromEntries(response.headers) });
        if (response.body) await pipeline(Readable.fromWeb(response.body as never), res); else res.end();
        return;
      }
      if (web && req.method === 'GET' && files.has(url.pathname)) {
        const file = files.get(url.pathname)!;
        res.writeHead(200, { ...headers, 'Content-Type': `${file.type}; charset=utf-8` }); res.end(file.bytes); return;
      }
      if (web && req.method === 'POST' && url.pathname.startsWith('/api/')) {
        // JSON + SameSite cookies + exact Origin validation protect all cookie-authenticated writes.
        const input = await body(req);
        if (url.pathname === '/api/join') {
          const joined = await store.redeemHuman(input);
          res.setHeader('Set-Cookie', `${cookieName(joined.room.room_id)}=${joined.member_token}; HttpOnly; SameSite=Strict; Path=/api; Max-Age=604800${configuredWeb?.protocol === 'https:' ? '; Secure' : ''}`);
          const { member_token: _token, ...value } = joined;
          return json(res, value);
        }
        const parsed = z.object({ room_id: z.string(), after_seq: z.number().optional(), limit: z.number().optional(),
          read_cursor: z.string().max(500).optional(), mentions: z.array(z.string().max(100)).max(100).optional(),
          text: z.string().optional(), client_message_id: z.string().optional(), reply_to: z.number().optional(), timeout_ms: z.number().optional() }).strict().parse(input);
        const cookie = cookieName(parsed.room_id);
        const token = (req.headers.cookie ?? '').split(';').map(part => part.trim()).find(part => part.startsWith(`${cookie}=`))?.slice(cookie.length + 1);
        if (!token) throw new ChatError('unauthorized', 'Join this room with a human invitation first.', 401);
        const names: Record<string, ToolName> = { '/api/check': 'chatroom_check', '/api/await': 'chatroom_await', '/api/send': 'chatroom_send', '/api/members': 'chatroom_members', '/api/leave': 'chatroom_leave' };
        const name = names[url.pathname];
        if (!name) throw new ChatError('not_found', 'Not found.', 404);
        const value = await store.execute(name, { ...parsed, member_token: token,
          ...(name === 'chatroom_await' ? { include_self: true } : {}) }, controller.signal);
        if (name === 'chatroom_leave') res.setHeader('Set-Cookie', `${cookie}=; HttpOnly; SameSite=Strict; Path=/api; Max-Age=0`);
        return json(res, value);
      }
      throw new ChatError('not_found', 'Not found.', 404);
    } catch (error) {
      if (controller.signal.aborted || res.destroyed) return;
      if (res.headersSent) { res.end(); return; }
      const known = error instanceof ChatError || error instanceof z.ZodError;
      if (!known) console.error('Chatroom request failed:', error instanceof Error ? error.name : 'unknown error');
      json(res, { error: error instanceof ChatError ? error.code : 'invalid_request',
        message: error instanceof ChatError ? error.message : error instanceof z.ZodError ? 'Invalid request fields.' : 'Service operation failed.' }, error instanceof ChatError ? error.status : known ? 400 : 500);
    } finally { controllers.delete(controller); req.off('aborted', abort); res.off('close', abort); }
  };
  const api = createServer(handler(false)), web = createServer(handler(true));
  for (const server of [api, web]) { server.requestTimeout = 15000; server.headersTimeout = 10000; server.maxConnections = 4096; }
  let closePromise: Promise<void> | undefined;
  function close() {
    return closePromise ??= (async () => {
      stopping = true;
      for (const controller of controllers) controller.abort();
      await store.close(); api.closeAllConnections(); web.closeAllConnections();
      await mcp.close();
      await Promise.all([closeServer(api), closeServer(web)]);
    })();
  }
  try {
    const port = await listen(api, host, options.port ?? 0), webPort = await listen(web, host, options.webPort ?? 0);
    const local = host === '::1' || host === '::' ? '[::1]' : '127.0.0.1';
    descriptor = { pid: process.pid, service_id: secret(), port, web_port: webPort, bind_host: host,
      mcp_url: options.publicMcpUrl ?? `http://${local}:${port}/mcp`, web_url: `${(options.publicWebUrl ?? `http://${local}:${webPort}`).replace(/\/$/, '')}/`,
      control_token: secret(), started_at: new Date().toISOString() };
    if (options.writeDescriptor !== false) {
      const path = join(options.directory, 'service.json');
      await writeFile(`${path}.tmp`, JSON.stringify(descriptor), { mode: 0o600 }); await rename(`${path}.tmp`, path);
    }
    return { descriptor, store, close };
  } catch (error) { await close(); throw error; }
}
