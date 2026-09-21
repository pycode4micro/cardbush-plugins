import { createMcpHandler, isLegacyRequest, isInitializeRequest, WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/server';
import { createChatServer, type Executor } from './mcp.js';
import { secret } from './store.js';

/** Standard HTTP sessions retain legacy cancellation routing; participant identity stays in tool credentials. */
export function createHttpMcp(execute: Executor) {
  const modern = createMcpHandler(context => createChatServer((name, args, signal) => execute(name, args,
    context.requestInfo?.signal && signal ? AbortSignal.any([context.requestInfo.signal, signal]) : signal)),
  { legacy: 'reject', keepAliveMs: 10000 });
  type Session = { transport: WebStandardStreamableHTTPServerTransport; server: ReturnType<typeof createChatServer>; touched: number };
  const sessions = new Map<string, Session>();
  const reap = setInterval(() => {
    for (const [id, session] of sessions) if (Date.now() - session.touched > 10 * 60 * 1000) {
      sessions.delete(id); void session.server.close().catch(() => {});
    }
  }, 60000);
  reap.unref();
  return {
    async fetch(request: Request, parsedBody?: unknown) {
      if (!await isLegacyRequest(request, parsedBody)) return modern.fetch(request, { parsedBody });
      const id = request.headers.get('mcp-session-id');
      let session = id ? sessions.get(id) : undefined;
      if (!session) {
        if (id || !isInitializeRequest(parsedBody)) return Response.json({ jsonrpc: '2.0', id: null,
          error: { code: -32000, message: 'MCP session is unavailable; initialize a new connection.' } }, { status: id ? 404 : 400 });
        if (sessions.size >= 256) return new Response('MCP session capacity reached.', { status: 429 });
        const server = createChatServer(execute);
        const transport = new WebStandardStreamableHTTPServerTransport({ sessionIdGenerator: secret,
          onsessioninitialized: id => { sessions.set(id, session!); },
          onsessionclosed: id => { sessions.delete(id); },
        });
        session = { transport, server, touched: Date.now() };
        await server.connect(transport);
        const onclose = transport.onclose;
        transport.onclose = () => { onclose?.(); if (transport.sessionId) sessions.delete(transport.sessionId); };
      }
      session.touched = Date.now();
      return session.transport.handleRequest(request, { parsedBody });
    },
    async close() {
      clearInterval(reap);
      await Promise.all([modern.close(), ...[...sessions.values()].map(session => session.server.close())]);
      sessions.clear();
    },
  };
}
