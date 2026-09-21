import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { tmpdir } from 'node:os';
import { Client } from '@modelcontextprotocol/client';
import { localServiceAction } from '../dist/local.js';

const hostRoot = process.env.CARDBUSH_SOURCE_ROOT;
if (!hostRoot) throw new Error('Set CARDBUSH_SOURCE_ROOT to a built CardBush checkout for optional host integration tests.');
const loadHost = file => import(pathToFileURL(resolve(hostRoot, file)).href);
const { resolvePluginManifest } = await loadHost('dist-electron/pluginManifest.js');
const { resolvePluginMcpConnection } = await loadHost('dist-electron/pluginMcpConfiguration.mjs');
const { ManagedStdioClientTransport } = await loadHost('packages/bush-mcp-client/dist/managedStdio.js');

const pluginRoot = resolve('.');
const entry = join(pluginRoot, 'runtime/cli.mjs');
const auth = member => ({ room_id: member.room.room_id, member_token: member.member_token });
async function tool(client, name, args = {}) {
  const response = await client.callTool({ name, arguments: args });
  assert.notEqual(response.isError, true, JSON.stringify(response) + (client.testStderr?.() ?? '')); return response.structuredContent;
}

test('portable manifest loads in CardBush and declares only standard skills and MCP', async () => {
  const manifest = await resolvePluginManifest(pluginRoot);
  assert.equal(manifest.format, 'agent-plugins');
  assert.equal(manifest.manifest.name, 'agentchatroom');
  assert.equal(manifest.skillRoots.length, 1);
  assert.equal(manifest.extensions.issues.length, 0);
  const connection = resolvePluginMcpConnection('agentchatroom', 'agentchatroom', pluginRoot, manifest.manifest.mcpServers, {}, {}, [], resolve('unused-test-data'));
  assert.equal(connection.transport.kind, 'stdio');
  assert.equal(connection.transport.command, 'node');
  assert.equal(connection.transport.args[0].replaceAll('\\', '/'), entry.replaceAll('\\', '/'));
  const portable = JSON.parse(await readFile(join(pluginRoot, 'mcp.json'), 'utf8'));
  const compat = JSON.parse(await readFile(join(pluginRoot, '.mcp.json'), 'utf8'));
  assert.deepEqual(portable.mcpServers, compat.mcpServers);
  assert.equal(manifest.manifest.runtime, undefined);
  assert.equal(connection.transport.env.AGENTCHATROOM_AUTOSTART, '1');
});

test('CardBush managed process-tree teardown preserves a service launched outside its MCP tree', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'agentchatroom-managed-'));
  const client = new Client({ name: 'cardbush-managed-test', version: '1.0.0' });
  const transport = new ManagedStdioClientTransport({ command: process.execPath, args: [entry],
    env: { ...process.env, AGENTCHATROOM_DATA_DIR: directory, AGENTCHATROOM_AUTOSTART: '0', AGENTCHATROOM_SERVER_URL: '' } });
  try {
    await client.connect(transport, { timeout: 10000 });
    const absent = await client.callTool({ name: 'chatroom_create', arguments: { name: 'No background child', agent_name: 'A' } });
    assert.equal(absent.structuredContent.error.code, 'service_not_running');
    const external = await localServiceAction('start', directory, entry);
    const owner = await tool(client, 'chatroom_create', { name: 'Managed', agent_name: 'A' });
    await tool(client, 'chatroom_send', { ...auth(owner), client_message_id: 'managed', text: 'outside the managed process tree' });
    await client.close();
    const surviving = await localServiceAction('status', directory, entry);
    assert.equal(surviving.status, 'running'); assert.equal(surviving.pid, external.pid);
  } finally {
    await client.close(); await localServiceAction('stop', directory, entry);
    await rm(directory, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});

test('CardBush starts the room service through the plugin and closes it with the MCP connection', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'agentchatroom-owned-'));
  const client = new Client({ name: 'cardbush-owned-test', version: '1.0.0' });
  const transport = new ManagedStdioClientTransport({ command: process.execPath, args: [entry],
    env: { ...process.env, AGENTCHATROOM_DATA_DIR: directory, AGENTCHATROOM_AUTOSTART: '1', AGENTCHATROOM_SERVER_URL: '' } });
  try {
    await client.connect(transport, { timeout: 10000 });
    const owner = await tool(client, 'chatroom_create', { name: 'Plugin-owned', agent_name: 'A' });
    const started = await tool(client, 'chatroom_service');
    assert.equal(started.lifecycle, 'plugin_connection');
    await tool(client, 'chatroom_send', { ...auth(owner), client_message_id: 'owned', text: 'saved before closing' });
    const restarted = await tool(client, 'chatroom_service', { action: 'restart' });
    assert.equal(restarted.web_url, started.web_url);
    assert.equal((await tool(client, 'chatroom_check', auth(owner))).messages[0].text, 'saved before closing');
    await client.close();
    assert.equal((await localServiceAction('status', directory, entry)).status, 'stopped');
    assert.match(await readFile(join(directory, 'events.jsonl'), 'utf8'), /saved before closing/);
  } finally {
    await client.close(); await localServiceAction('stop', directory, entry);
    await rm(directory, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});
