import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, cp, mkdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { localServiceAction } from '../dist/local.js';

const pluginRoot = resolve('.');
const entry = join(pluginRoot, 'runtime/cli.mjs');
const auth = member => ({ room_id: member.room.room_id, member_token: member.member_token });
async function connect(entryPath, directory, autoStart = '1') {
  const client = new Client({ name: 'process-test', version: '1.0.0' });
  const transport = new StdioClientTransport({ command: process.execPath, args: [entryPath],
    env: { ...process.env, AGENTCHATROOM_DATA_DIR: directory, AGENTCHATROOM_SERVER_URL: '', AGENTCHATROOM_AUTOSTART: autoStart }, stderr: 'pipe' });
  let stderr = '';
  transport.stderr?.on('data', chunk => { stderr += chunk; });
  try { await client.connect(transport, { timeout: 10000 }); }
  catch (error) { throw new Error(`${error.message}\n${stderr}`); }
  client.testStderr = () => stderr;
  return client;
}
async function tool(client, name, args = {}) {
  const response = await client.callTool({ name, arguments: args });
  assert.notEqual(response.isError, true, JSON.stringify(response) + (client.testStderr?.() ?? '')); return response.structuredContent;
}

test('portable and compatibility manifests resolve to the bundled standalone runtime', async () => {
  const portable = JSON.parse(await readFile(join(pluginRoot, 'mcp.json'), 'utf8'));
  const compat = JSON.parse(await readFile(join(pluginRoot, '.mcp.json'), 'utf8'));
  assert.deepEqual(portable.mcpServers, compat.mcpServers);
  const manifest = JSON.parse(await readFile(join(pluginRoot, 'plugin.json'), 'utf8'));
  assert.equal(manifest.name, 'agentchatroom');
  assert.equal(manifest.$schema, 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json');
  assert.equal(resolve(portable.mcpServers.agentchatroom.args[0].replace('$' + '{PLUGIN_ROOT}', pluginRoot)), entry);
  assert.equal(portable.mcpServers.agentchatroom.env.AGENTCHATROOM_AUTOSTART, '1');
  assert.ok((await readFile(entry)).length > 0);
});

test('copied plugin starts/stops/restarts through MCP, shares one writer, retains history and closes its owned service', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'agentchatroom-process-'));
  const copiedRoot = join(directory, 'independent plugin');
  await mkdir(copiedRoot);
  await cp(pluginRoot, copiedRoot, { recursive: true, filter: source => !['node_modules', 'dist', '.git', 'outputs'].some(name => source === join(pluginRoot, name)) });
  const standalone = join(copiedRoot, 'runtime/cli.mjs');
  const data = join(directory, 'persistent data');
  const clients = [];
  try {
    const a = await connect(standalone, data), b = await connect(standalone, data); clients.push(a, b);
    assert.equal((await a.listTools()).tools.length, 12);
    assert.equal((await tool(a, 'chatroom_service')).status, 'stopped', 'listing tools and status do not start a service');
    const first = await tool(a, 'chatroom_service', { action: 'start' });
    assert.equal(first.lifecycle, 'plugin_connection'); assert.equal(first.owned_by_this_connection, true);
    assert.equal(first.control_token, undefined); assert.equal(first.service_id, undefined);
    const [statusA, statusB] = await Promise.all([tool(a, 'chatroom_service', { action: 'start' }), tool(b, 'chatroom_service', { action: 'start' })]);
    assert.equal(statusA.pid, statusB.pid);
    assert.equal(statusB.owned_by_this_connection, false);
    const owner = await tool(a, 'chatroom_create', { name: 'Persistent', agent_name: 'A' });
    const member = await tool(b, 'chatroom_join', { code: owner.code, agent_name: 'B' });
    const wait = tool(a, 'chatroom_await', { ...auth(owner), after_seq: 0, timeout_ms: 5000 });
    await tool(b, 'chatroom_send', { ...auth(member), text: 'preserved', client_message_id: 'durable' });
    assert.equal((await wait).status, 'messages');
    await b.close();
    assert.equal((await localServiceAction('status', data, standalone)).status, 'running');
    const restarted = await tool(a, 'chatroom_service', { action: 'restart' });
    assert.equal(restarted.mcp_url, statusA.mcp_url); assert.equal(restarted.web_url, statusA.web_url);
    assert.equal((await tool(a, 'chatroom_check', auth(member))).messages[0].text, 'preserved');
    const dedup = await tool(a, 'chatroom_send', { ...auth(member), text: 'preserved', client_message_id: 'durable' });
    assert.equal(dedup.duplicate, true);
    const interrupted = a.callTool({ name: 'chatroom_await', arguments: { ...auth(owner), after_seq: 1, timeout_ms: 15000 } }).catch(error => error);
    await delay(100);
    assert.equal((await tool(a, 'chatroom_service', { action: 'stop' })).status, 'stopped');
    await interrupted;
    const stoppedRead = await a.callTool({ name: 'chatroom_check', arguments: auth(owner) });
    assert.equal(stoppedRead.structuredContent.error.code, 'service_not_running');
    assert.equal((await tool(a, 'chatroom_service')).status, 'stopped');
    assert.equal((await tool(a, 'chatroom_service', { action: 'stop' })).status, 'stopped');
    await tool(a, 'chatroom_service', { action: 'start' });
    assert.equal((await tool(a, 'chatroom_check', auth(owner))).messages[0].text, 'preserved');
    await a.close(); clients.length = 0;
    for (let attempt = 0; attempt < 50 && (await localServiceAction('status', data, standalone)).status !== 'stopped'; attempt++) await delay(100);
    assert.equal((await localServiceAction('status', data, standalone)).status, 'stopped');
    const c = await connect(standalone, data, '0'); clients.push(c);
    const noImplicitStart = await c.callTool({ name: 'chatroom_check', arguments: auth(owner) });
    assert.equal(noImplicitStart.structuredContent.error.code, 'service_not_running');
    await tool(c, 'chatroom_service', { action: 'start' });
    assert.equal((await tool(c, 'chatroom_check', auth(owner))).messages[0].text, 'preserved');
  } finally {
    await Promise.all(clients.map(client => client.close()));
    await localServiceAction('stop', data, standalone);
    await rm(directory, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});

test('a plugin reuses an external service without taking ownership of its lifetime', async () => {
  const data = await mkdtemp(join(tmpdir(), 'agentchatroom-external-'));
  let client;
  try {
    const external = await localServiceAction('start', data, entry);
    client = await connect(entry, data);
    const status = await tool(client, 'chatroom_service', { action: 'start' });
    assert.equal(status.pid, external.pid); assert.equal(status.owned_by_this_connection, false);
    await tool(client, 'chatroom_create', { name: 'External', agent_name: 'A' });
    await client.close();
    assert.equal((await localServiceAction('status', data, entry)).status, 'running');
  } finally {
    await client?.close(); await localServiceAction('stop', data, entry);
    await rm(data, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});

test('simultaneous plugin startup chooses one owner and closing the other connection preserves it', async () => {
  const data = await mkdtemp(join(tmpdir(), 'agentchatroom-racing-start-'));
  const clients = [];
  try {
    clients.push(await connect(entry, data), await connect(entry, data));
    const statuses = await Promise.all(clients.map(client => tool(client, 'chatroom_service', { action: 'start' })));
    assert.equal(statuses[0].pid, statuses[1].pid);
    assert.equal(statuses.filter(status => status.owned_by_this_connection).length, 1);
    const reuser = statuses.findIndex(status => !status.owned_by_this_connection);
    await clients[reuser].close();
    assert.equal((await localServiceAction('status', data, entry)).status, 'running');
    await clients[1 - reuser].close();
    assert.equal((await localServiceAction('status', data, entry)).status, 'stopped');
  } finally {
    await Promise.all(clients.map(client => client.close()));
    await localServiceAction('stop', data, entry);
    await rm(data, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});
