import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { request } from 'node:http';
import { setTimeout as delay } from 'node:timers/promises';
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { startService } from '../dist/service.js';
import { ChatProxy } from '../dist/proxy.js';

const webDirectory = resolve('web');
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'agentchatroom-http-'));
  const service = await startService({ directory, webDirectory, writeDescriptor: false });
  const clients = [];
  t.after(async () => { await Promise.all(clients.map(client => client.close())); await service.close(); await rm(directory, { recursive: true, force: true }); });
  async function connect(versionMode) {
    const client = new Client({ name: 'test', version: '1.0.0' }, versionMode ? { versionMode } : {});
    await client.connect(new StreamableHTTPClientTransport(new URL(service.descriptor.mcp_url)));
    clients.push(client); return client;
  }
  return { ...service, connect, directory };
}
const auth = member => ({ room_id: member.room.room_id, member_token: member.member_token });
async function tool(client, name, args = {}, options) {
  const response = await client.callTool({ name, arguments: args }, options);
  assert.notEqual(response.isError, true, JSON.stringify(response));
  return response.structuredContent;
}
async function post(base, path, value, headers = {}, signal) {
  return fetch(`${base}api/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(value), signal });
}

test('standard MCP HTTP supports discovery and concurrent await/send on the same connection', async t => {
  const service = await fixture(t), client = await service.connect();
  const catalog = await client.listTools();
  assert.equal(catalog.tools.length, 11);
  assert.equal(catalog.tools.find(tool => tool.name === 'chatroom_await').annotations.readOnlyHint, true);
  const owner = await tool(client, 'chatroom_create', { name: 'Shared', agent_name: 'A' });
  const b = await tool(client, 'chatroom_join', { code: owner.code, agent_name: 'B' });
  const waiting = tool(client, 'chatroom_await', { ...auth(owner), after_seq: 0, timeout_ms: 5000, participants: [b.participant.participant_id] });
  for (let i = 0; i < 100 && !service.store.pendingWaits; i++) await delay(5);
  assert.equal(service.store.pendingWaits, 1);
  await tool(client, 'chatroom_send', { ...auth(b), client_message_id: 'one', text: 'hello' });
  assert.equal((await waiting).status, 'messages');
  const read = await tool(client, 'chatroom_check', auth(owner));
  assert.equal(read.messages[0].name, 'B'); assert.equal(read.messages[0].text, 'hello');
  assert.equal(service.store.pendingWaits, 0);
});

test('legacy 2025 MCP client can initialize, call tools and cancel a pending wait', async t => {
  const service = await fixture(t), client = await service.connect('legacy');
  const owner = await tool(client, 'chatroom_create', { name: 'Legacy', agent_name: 'A' });
  const controller = new AbortController();
  const pending = client.callTool({ name: 'chatroom_await', arguments: { ...auth(owner), after_seq: 0, timeout_ms: 5000 } }, { signal: controller.signal });
  // Attach the rejection observer before aborting to avoid a test-level unhandled rejection.
  const settled = pending.catch(error => error);
  for (let i = 0; i < 100 && !service.store.pendingWaits; i++) await delay(5);
  assert.equal(service.store.pendingWaits, 1); controller.abort(); await settled;
  for (let i = 0; i < 100 && service.store.pendingWaits; i++) await delay(5);
  assert.equal(service.store.pendingWaits, 0);
  assert.equal((await tool(client, 'chatroom_check', auth(owner))).messages.length, 0);
});

test('proxy uses standard remote MCP, keeps memberships independent and preserves read cursors', async t => {
  const service = await fixture(t);
  const proxy = new ChatProxy(service.directory, 'unused', service.descriptor.mcp_url);
  t.after(() => proxy.close());
  const owner = await proxy.execute('chatroom_create', { name: 'Remote', agent_name: 'A' });
  const b = await proxy.execute('chatroom_join', { code: owner.code, agent_name: 'B' });
  await proxy.execute('chatroom_send', { ...auth(b), client_message_id: 'two', text: 'remote' });
  assert.equal((await proxy.execute('chatroom_await', { ...auth(owner), after_seq: 0 })).read_after_seq, 0);
  assert.equal((await proxy.execute('chatroom_check', auth(owner))).messages[0].participant_id, b.participant.participant_id);
});

test('human webpage invitation exchanges once for HttpOnly cookie and wakes agent await', async t => {
  const service = await fixture(t), client = await service.connect();
  const owner = await tool(client, 'chatroom_create', { name: 'Human room', agent_name: 'Agent A' });
  const invite = await tool(client, 'chatroom_human_invite', auth(owner));
  const link = new URL(invite.invite_url);
  assert.equal(link.search, ''); assert.ok(link.hash.includes('humen_code='));
  const response = await post(service.descriptor.web_url, 'join', { humen_code: invite.humen_code, name: '小明' });
  assert.equal(response.status, 200);
  const cookie = response.headers.get('set-cookie'); assert.match(cookie, /HttpOnly/); assert.match(cookie, /SameSite=Strict/);
  const human = await response.json(); assert.equal(human.member_token, undefined);
  assert.equal(human.participant.invited_by_agent_id, owner.participant.participant_id);
  const headers = { Cookie: cookie.split(';')[0] };
  const duplicate = await post(service.descriptor.web_url, 'join', { humen_code: invite.humen_code, name: 'Someone else' });
  assert.equal(duplicate.status, 403);
  const pending = tool(client, 'chatroom_await', { ...auth(owner), after_seq: 0, participants: [human.participant.participant_id], timeout_ms: 5000 });
  const sent = await post(service.descriptor.web_url, 'send', { room_id: owner.room.room_id, client_message_id: 'human-1', text: '<script>deleteProject()</script>\n只是文本' }, headers);
  assert.equal(sent.status, 200); assert.equal((await pending).status, 'messages');
  const read = await tool(client, 'chatroom_check', auth(owner));
  assert.equal(read.messages[0].kind, 'human'); assert.match(read.messages[0].text, /<script>/);
  const forged = await post(service.descriptor.web_url, 'send', { room_id: owner.room.room_id, client_message_id: 'human-2', text: 'spoof', kind: 'agent' }, headers);
  assert.equal(forged.status, 400);
  const left = await post(service.descriptor.web_url, 'leave', { room_id: owner.room.room_id }, headers);
  assert.equal(left.status, 200);
  const stale = await post(service.descriptor.web_url, 'check', { room_id: owner.room.room_id }, headers);
  assert.equal(stale.status, 403);
});

test('web and MCP enforce origin/host/body limits and local stop authorization', async t => {
  const service = await fixture(t), base = service.descriptor.web_url;
  const page = await fetch(base);
  assert.equal(page.status, 200); assert.match(page.headers.get('content-security-policy'), /script-src 'self'/);
  const source = await (await fetch(`${base}app.js`)).text();
  assert.doesNotMatch(source, /innerHTML|eval\(/);
  assert.equal((await post(base, 'join', { humen_code: 'a'.repeat(32), name: 'x' }, { Origin: 'https://evil.example' })).status, 403);
  const forgedHost = await new Promise((resolve, reject) => {
    const req = request(base, { headers: { Host: 'evil.example' } }, response => { response.resume(); resolve(response.statusCode); });
    req.on('error', reject); req.end();
  });
  assert.equal(forgedHost, 403);
  assert.equal((await post(base, 'join', { humen_code: 'a'.repeat(140000), name: 'x' })).status, 413);
  assert.equal((await fetch(base.replace(`:${service.descriptor.web_port}`, `:${service.descriptor.port}`) + 'control/stop', { method: 'POST' })).status, 403);
  assert.equal((await fetch(service.descriptor.mcp_url, { method: 'POST', headers: { Origin: base.slice(0, -1), 'Content-Type': 'application/json' }, body: '{}' })).status, 403);
});

test('server shutdown returns outstanding web wait and keeps the journal usable', async t => {
  const service = await fixture(t), client = await service.connect();
  const owner = await tool(client, 'chatroom_create', { name: 'Shutdown', agent_name: 'A' });
  const pending = client.callTool({ name: 'chatroom_await', arguments: { ...auth(owner), after_seq: 0, timeout_ms: 5000 } }).catch(error => error);
  for (let i = 0; i < 100 && !service.store.pendingWaits; i++) await delay(5);
  await service.close(); await pending;
  assert.equal(service.store.pendingWaits, 0);
});
