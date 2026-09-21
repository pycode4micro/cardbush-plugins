#!/usr/bin/env node
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { fileURLToPath } from 'node:url';
import { createChatServer } from './mcp.js';
import { ChatProxy } from './proxy.js';
import { dataDirectory, localServiceAction, openLocalService, PluginServiceController } from './local.js';

const entry = fileURLToPath(import.meta.url), directory = dataDirectory();
const command = process.argv[2] ?? 'stdio';
try {
  if (command === 'serve') {
    const service = await openLocalService(directory, entry);
    const stop = () => { void service.close().catch(() => { process.exitCode = 1; }); };
    process.once('SIGINT', stop); process.once('SIGTERM', stop);
    console.error(`Agent Chatroom ready: ${service.descriptor.mcp_url} | ${service.descriptor.web_url}`);
  } else if (['start', 'status', 'stop'].includes(command)) {
    console.log(JSON.stringify(await localServiceAction(command as 'start' | 'status' | 'stop', directory, entry), null, 2));
  } else if (command === 'stdio') {
    const autoStart = process.env.AGENTCHATROOM_AUTOSTART !== '0';
    const local = new PluginServiceController(directory, entry, autoStart);
    const proxy = new ChatProxy(directory, entry, process.env.AGENTCHATROOM_SERVER_URL?.trim() || undefined, autoStart, () => local.local());
    const handle = serveStdio(() => createChatServer(proxy.execute, { proxy: true,
      service: async action => {
        if (action === 'stop' || action === 'restart') {
          const status = await local.action('status');
          if (typeof status.mcp_url === 'string') await proxy.disconnect(status.mcp_url);
        }
        return local.action(action);
      } }));
    let closing: Promise<void> | undefined;
    const close = () => closing ??= (async () => {
      await local.close(); await proxy.close(); await handle.close();
    })().catch(() => { process.exitCode = 1; });
    process.stdin.once('end', () => { void close(); });
    process.once('SIGINT', () => { void close(); }); process.once('SIGTERM', () => { void close(); });
  } else throw new Error('Usage: node cli.mjs [stdio|serve|start|status|stop]');
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Chatroom startup failed.'); process.exitCode = 1;
}
