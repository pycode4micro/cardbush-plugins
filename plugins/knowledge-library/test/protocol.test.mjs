import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('portable MCP handshake, tool annotations, resource citations, panel and read-only reconnect', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'knowledge-protocol-'));
  async function connect(readOnly = false) {
    const client = new Client({ name: 'knowledge-test', version: '1.0.0' });
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root,'runtime/server.mjs')], env: { ...process.env, KNOWLEDGE_DATA_DIR: directory, KNOWLEDGE_READ_ONLY: readOnly ? '1' : '0' }, stderr: 'pipe' });
    await client.connect(transport); return client;
  }
  let client = await connect();
  try {
    const tools = (await client.listTools()).tools; assert.equal(tools.length, 7);
    assert.equal(tools.find(t => t.name === 'knowledge_search').annotations.readOnlyHint, true);
    assert.equal(tools.find(t => t.name === 'knowledge_import').annotations.readOnlyHint, false);
    assert.equal(tools.find(t => t.name === 'knowledge_search')._meta?.ui, undefined, 'ordinary search never auto-opens an App');
    assert.ok(tools.find(t => t.name === 'knowledge_open')._meta.ui.resourceUri);
    const call = async (name, args) => { const result = await client.callTool({ name, arguments: args }); assert.ok(!result.isError, JSON.stringify(result)); return result.structuredContent; };
    await call('knowledge_library', { library_id: 'dept', name: '部门资料' });
    await call('knowledge_import', { library_id: 'dept', documents: [{ source_key: 'test', title: '试用制度', text: '试用产品返还时限为 21 天。' }] });
    const found = await call('knowledge_search', { query: '返还时限', library_ids: ['dept'] }); assert.equal(found.results.length, 1);
    const resource = await client.readResource({ uri: found.results[0].resource_uri }); assert.match(resource.contents[0].text, /21 天/);
    const html = await client.readResource({ uri: 'ui://knowledge-library/library' }); assert.match(html.contents[0].text, /资料库/);
    const invalid = await client.callTool({ name: 'knowledge_search', arguments: { query: 'x', include_secrets: true } }); assert.equal(invalid.isError, true);
    await client.close(); client = await connect(true);
    assert.equal((await client.listTools()).tools.length, 4);
    const persisted = await client.callTool({ name: 'knowledge_search', arguments: { query: '返还时限' } }); assert.equal(persisted.structuredContent.results.length, 1);
    const rejected = await client.callTool({ name: 'knowledge_import', arguments: { library_id: 'dept' } }); assert.ok(rejected.isError);
  } finally { await client.close(); }
});
