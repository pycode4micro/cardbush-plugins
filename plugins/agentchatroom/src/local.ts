import { spawn } from 'node:child_process';
import { mkdir, open, readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { startService, type Descriptor } from './service.js';
import { ChatError } from './store.js';

export function dataDirectory() {
  return resolve(process.env.AGENTCHATROOM_DATA_DIR || join(process.platform === 'win32'
    ? process.env.LOCALAPPDATA || homedir() : process.env.XDG_STATE_HOME || join(homedir(), '.local', 'state'), 'agentchatroom'));
}

/** Shared configuration for an explicitly launched daemon and a plugin-owned service. */
export async function openLocalService(directory: string, entryPath: string) {
  let previous: { port?: number; web_port?: number } = {};
  try { previous = JSON.parse(await readFile(join(directory, 'service.json'), 'utf8')); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  const port = (name: string, saved?: number) => {
    const value = Number(process.env[name]?.trim() || saved || 0);
    if (!Number.isInteger(value) || value < 0 || value > 65535) throw new Error(`Invalid ${name}.`);
    return value;
  };
  return startService({ directory, webDirectory: resolve(dirname(entryPath), '../web'),
    host: process.env.AGENTCHATROOM_BIND_HOST,
    port: port('AGENTCHATROOM_PORT', previous.port), webPort: port('AGENTCHATROOM_WEB_PORT', previous.web_port),
    publicMcpUrl: process.env.AGENTCHATROOM_PUBLIC_MCP_URL, publicWebUrl: process.env.AGENTCHATROOM_PUBLIC_WEB_URL });
}

export type ServiceAction = 'start' | 'status' | 'stop' | 'restart';

/** Ordinary MCP-owned HTTP listeners, with no detached child or host-specific protocol. */
export class PluginServiceController {
  private owned?: Awaited<ReturnType<typeof openLocalService>>;
  private queue: Promise<unknown> = Promise.resolve();
  private closed = false;
  private paused = false;
  private closing?: Promise<void>;
  constructor(private directory: string, private entryPath: string, private autoStart = true) {}

  private serial<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(operation);
    this.queue = result.catch(() => undefined);
    return result;
  }
  private requireOpen() {
    if (this.closed) throw new ChatError('service_stopping', 'This plugin connection is closing.', 503);
  }
  private async start(): Promise<Descriptor> {
    this.requireOpen();
    const running = await runningService(this.directory);
    if (running) return running;
    await this.owned?.close(); this.owned = undefined;
    try { this.owned = await openLocalService(this.directory, this.entryPath); }
    catch (error) {
      // Another MCP connection can win the writer lock. Reuse only a healthy service.
      if (!(error instanceof ChatError) || error.code !== 'service_running') throw error;
      for (let attempt = 0; attempt < 80; attempt++) {
        this.requireOpen();
        const service = await runningService(this.directory);
        if (service) return service;
        await delay(100);
      }
      throw new ChatError('startup_failed', 'Another local service holds the data directory but is not ready. Inspect the service log.', 503);
    }
    if (this.closed) { await this.owned.close(); this.owned = undefined; this.requireOpen(); }
    return this.owned!.descriptor;
  }
  private summary(service?: Descriptor): Record<string, unknown> {
    if (!service) return { status: 'stopped', scope: 'local', data_directory: this.directory, auto_start: this.autoStart && !this.paused };
    const owned = this.owned?.descriptor.service_id === service.service_id;
    return { status: 'running', scope: 'local', mcp_url: service.mcp_url, web_url: service.web_url,
      pid: service.pid, started_at: service.started_at, data_directory: this.directory,
      lifecycle: owned ? 'plugin_connection' : 'existing_service', owned_by_this_connection: owned };
  }
  private async stop() {
    if (this.owned) { await this.owned.close(); this.owned = undefined; }
    const status = await localServiceAction('stop', this.directory, this.entryPath);
    if (status.status !== 'stopped') throw new ChatError('shutdown_timeout', 'The local service is still stopping. Check status before starting again.', 503);
  }
  action(action: ServiceAction): Promise<Record<string, unknown>> {
    return this.serial(async () => {
      this.requireOpen();
      if (action === 'status') return this.summary(await runningService(this.directory));
      if (action === 'stop' || action === 'restart') { this.paused = true; await this.stop(); }
      if (action === 'stop') return this.summary();
      const service = await this.start(); this.paused = false;
      return this.summary(service);
    });
  }
  local(): Promise<Descriptor> {
    return this.serial(async () => {
      this.requireOpen();
      const running = await runningService(this.directory);
      if (running) return running;
      if (this.autoStart && !this.paused) return this.start();
      throw new ChatError('service_not_running', 'The local chatroom service is stopped. Call chatroom_service with action="start" to run it inside this plugin; no manual script is required.', 503);
    });
  }
  close(): Promise<void> {
    this.closed = true;
    return this.closing ??= this.serial(async () => {
      // A reused external service belongs to its launcher, not this connection.
      await this.owned?.close(); this.owned = undefined;
    });
  }
}
function controlBase(info: Descriptor) {
  if (!Number.isInteger(info.port) || info.port < 1 || info.port > 65535) throw new Error('Invalid local service descriptor.');
  return `http://${info.bind_host?.includes(':') ? '[::1]' : '127.0.0.1'}:${info.port}`;
}
export async function runningService(directory: string): Promise<Descriptor | undefined> {
  let info: Descriptor;
  try { info = JSON.parse(await readFile(join(directory, 'service.json'), 'utf8')) as Descriptor; }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return; throw error; }
  if (!Number.isInteger(info.pid) || info.pid <= 0) throw new Error('Invalid service PID.');
  try {
    process.kill(info.pid, 0);
    const response = await fetch(`${controlBase(info)}/health`, { redirect: 'error', signal: AbortSignal.timeout(1500) });
    const health = await response.json() as { service_id?: string };
    if (response.ok && health.service_id === info.service_id) return info;
  } catch { /* The descriptor survives service shutdown; a health check establishes liveness. */ }
}
let starting: Promise<Descriptor> | undefined;
export async function ensureLocalService(directory: string, entryPath: string) {
  if (starting) return starting;
  starting = (async () => {
    const running = await runningService(directory);
    if (running) return running;
    await mkdir(directory, { recursive: true, mode: 0o700 });
    const log = await open(join(directory, 'service.log'), 'a', 0o600);
    let childError: Error | undefined;
    try {
      const child = spawn(process.execPath, [entryPath, 'serve'], {
        cwd: directory, detached: true, windowsHide: true,
        stdio: ['ignore', log.fd, log.fd], env: { ...process.env, AGENTCHATROOM_DATA_DIR: directory },
      });
      child.once('error', error => { childError = error; });
      child.unref();
    } finally { await log.close(); }
    // Competing clients may start together; the exclusive store lock chooses one writer.
    for (let attempt = 0; attempt < 80; attempt++) {
      if (childError) throw childError;
      const service = await runningService(directory);
      if (service) return service;
      await delay(100);
    }
    throw new ChatError('startup_failed', `Room service did not become ready. Inspect ${join(directory, 'service.log')}.`, 503);
  })();
  try { return await starting; } finally { starting = undefined; }
}
export async function localServiceAction(action: 'start' | 'status' | 'stop', directory: string, entryPath: string): Promise<Record<string, unknown>> {
  const service = action === 'start' ? await ensureLocalService(directory, entryPath) : await runningService(directory);
  if (!service) return { status: 'stopped', data_directory: directory };
  if (action === 'stop') {
    const response = await fetch(`${controlBase(service)}/control/stop`, { method: 'POST',
      headers: { Authorization: `Bearer ${service.control_token}` }, redirect: 'error', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('Failed to stop local service.');
    for (let attempt = 0; attempt < 50; attempt++) {
      if (!await runningService(directory)) return { status: 'stopped', data_directory: directory };
      await delay(100);
    }
    return { status: 'stopping', data_directory: directory };
  }
  return { status: 'running', mcp_url: service.mcp_url, web_url: service.web_url, pid: service.pid,
    started_at: service.started_at, data_directory: directory };
}
