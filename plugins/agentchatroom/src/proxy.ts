import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { ChatError } from './store.js';
import { ensureLocalService, runningService } from './local.js';
import type { Executor } from './mcp.js';
import type { Descriptor } from './service.js';

export class ChatProxy {
  private clients = new Map<string, Promise<Client>>();
  constructor(private directory: string, private entryPath: string, private defaultUrl?: string, private autoStart = true,
    private resolveLocal?: () => Promise<Descriptor>) {}
  execute: Executor = async (name, input, signal, serverUrl) => {
    const endpoint = serverUrl ?? this.defaultUrl ?? (await this.local()).mcp_url;
    const url = new URL(endpoint);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) throw new ChatError('invalid_server', 'Expected an HTTP(S) MCP endpoint without URL credentials or fragment.');
    const key = url.href;
    if (!this.clients.has(key)) {
      if (this.clients.size >= 32) throw new ChatError('capacity', 'Too many connected servers; reconnect this plugin to clear connections.');
      let connecting: Promise<Client> | undefined;
      connecting = (async () => {
        const client = new Client({ name: 'agentchatroom-plugin', version: '0.1.1' });
        const transport = new StreamableHTTPClientTransport(url, { fetch: (resource, init) => fetch(resource, { ...init, redirect: 'error' }) });
        try { await client.connect(transport, { timeout: 10000 }); return client; }
        catch (error) {
          await client.close().catch(() => {});
          if (this.clients.get(key) === connecting) this.clients.delete(key);
          throw error;
        }
      })();
      this.clients.set(key, connecting);
    }
    const pending = this.clients.get(key)!;
    let client: Client | undefined;
    try {
      client = await pending;
      const value = await client.callTool({ name, arguments: input as Record<string, unknown> },
        { signal, timeout: name === 'chatroom_await' ? 55000 : 15000 });
      if (value.isError) {
        const detail = (value.structuredContent as { error?: { code?: string; message?: string } } | undefined)?.error;
        throw new ChatError(detail?.code ?? 'remote_error', detail?.message ?? 'Remote room operation failed.');
      }
      if (!value.structuredContent) throw new ChatError('invalid_response', 'The server did not return a structured room result.');
      return value.structuredContent as Record<string, unknown>;
    } catch (error) {
      // Never retry a write implicitly: send's caller supplies an idempotency key.
      if (!(error instanceof ChatError) && !signal?.aborted) {
        if (this.clients.get(key) === pending) this.clients.delete(key);
        await client?.close().catch(() => {});
      }
      if (signal?.aborted) return { status: 'cancelled' };
      throw error;
    }
  };
  private async local() {
    if (this.resolveLocal) return this.resolveLocal();
    if (this.autoStart) return ensureLocalService(this.directory, this.entryPath);
    const running = await runningService(this.directory);
    if (running) return running;
    throw new ChatError('external_service_required', 'Start the independent room service using start-service.cmd (Windows) or start-service.sh (macOS/Linux) in the plugin folder, outside the agent host. Then retry. Hosts such as CardBush clean up MCP child processes; a room service launched inside that process tree cannot outlive it. Alternatively configure a remote server_url.', 503);
  }
  async disconnect(endpoint: string) {
    const key = new URL(endpoint).href, pending = this.clients.get(key);
    this.clients.delete(key);
    if (pending) { try { await (await pending).close(); } catch {} }
  }
  async close() {
    await Promise.all([...this.clients.values()].map(async pending => { try { await (await pending).close(); } catch {} }));
    this.clients.clear();
  }
}
