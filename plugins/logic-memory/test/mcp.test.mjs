import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, copyFile, readFile, writeFile, rm, access } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { dataDirectory, importLegacy } from '../dist/storage.js';

const entry = resolve('runtime/cli.mjs');
const lesson = { scenario: 'socket connection ECONNRESET', bias: 'Assuming server failure', correction: 'Check client connection reuse', evidence: 'reused connection test passed', evidence_state: 'verified' };
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'logic plugin '));
  t.after(() => rm(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 }));
  return root;
}
async function connect(directory, executable = entry) {
  const env = { ...process.env, LOGIC_MEMORY_DATA_DIR: directory };
  delete env.NODE_OPTIONS; delete env.ELECTRON_RUN_AS_NODE;
  const transport = new StdioClientTransport({ command: process.execPath, args: [executable], env, stderr: 'pipe' });
  let stderr = '';
  transport.stderr?.on('data', data => { stderr += data; });
  const client = new Client({ name: 'independent-test-client', version: '1.0.0' });
  try { await client.connect(transport, { timeout: 10000 }); }
  catch (error) { await transport.close(); throw Error(`${error.message}\n${stderr}`); }
  return client;
}
async function call(client, name, input) {
  const result = await client.callTool({ name, arguments: input });
  assert.notEqual(result.isError, true, JSON.stringify(result));
  assert.deepEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

test('standalone bundled MCP has two standard tools, persistent search and explicit idempotent feedback', async t => {
  const root = await fixture(t), data = join(root, 'persistent data');
  const standalone = join(root, 'plugin without node modules'); await mkdir(standalone);
  const copiedEntry = join(standalone, 'cli.mjs'); await copyFile(entry, copiedEntry);
  let client = await connect(data, copiedEntry);
  try {
    const tools = (await client.listTools()).tools;
    assert.deepEqual(tools.map(tool => tool.name).sort(), ['consult_logic', 'learn_logic']);
    assert.equal(tools.find(tool => tool.name === 'consult_logic').annotations.readOnlyHint, true);
    assert.equal(tools.find(tool => tool.name === 'learn_logic').annotations.readOnlyHint, false);
    const schema = JSON.stringify(tools);
    assert.doesNotMatch(schema, /cardbush\/|session_id|turn_id/);
    const learned = await call(client, 'learn_logic', lesson);
    const found = await call(client, 'consult_logic', { query: 'socket connection', decision_context: 'ECONNRESET' });
    assert.equal(found.matched_logic[0].logic_id, learned.logic_id);
    for (const field of ['query', 'scenario_conditions', 'cognitive_patterns']) assert.equal(field in found, false);
    for (const args of [{}, { query: '' }, { mode: 'unsupported' }, { query: 'socket', invented: true }]) {
      assert.equal((await client.callTool({ name: 'consult_logic', arguments: args })).isError, true);
    }
    assert.equal((await client.callTool({ name: 'learn_logic', arguments: { logic_id: learned.logic_id, feedback: 'helpful' } })).isError, true);
    const feedback = { logic_id: learned.logic_id, feedback: 'helpful', source_id: 'test-event-1' };
    await call(client, 'learn_logic', feedback); await call(client, 'learn_logic', feedback);
    assert.equal(JSON.parse(await readFile(join(data, 'logic.json'), 'utf8'))[0].positive_feedback_count, 1);
    await call(client, 'learn_logic', { ...feedback, feedback: 'unhelpful' });
    assert.equal(JSON.parse(await readFile(join(data, 'logic.json'), 'utf8'))[0].positive_feedback_count, 0);
    assert.equal(JSON.stringify((await client.listTools()).tools), schema);
    await client.close(); client = await connect(data, copiedEntry);
    const inventory = await call(client, 'consult_logic', { mode: 'list' });
    assert.equal(inventory.total_count, 1); assert.equal(inventory.records[0].logic_id, learned.logic_id);
  } finally { await client.close(); }
});

test('separate MCP processes serialize shared-store writes without dropping lessons', async t => {
  const root = await fixture(t);
  const clients = await Promise.all(Array.from({ length: 4 }, () => connect(root)));
  try {
    await Promise.all(clients.map((client, index) => Promise.all(Array.from({ length: 6 }, (_, ordinal) =>
      call(client, 'learn_logic', { ...lesson, scenario: `process ${index} lesson ${ordinal}` })))));
    let offset = 0; const ids = new Set();
    do {
      const page = await call(clients[0], 'consult_logic', { mode: 'list', offset, max_results: 5 });
      assert.equal(page.total_count, 24); page.records.forEach(record => ids.add(record.logic_id)); offset = page.next_offset;
    } while (offset !== null);
    assert.equal(ids.size, 24);
  } finally { await Promise.all(clients.map(client => client.close())); }
});

test('legacy import copies validated bytes, preserves source and refuses to overwrite an existing store', async t => {
  const root = await fixture(t), source = join(root, 'old logic.json'), data = join(root, 'new data');
  const original = JSON.stringify([{ logic_id: 'old-id', scenario: '编码检测', correction: '检查 BOM', confidence: 0.8, evidence: 'legacy evidence' }], null, 2);
  await writeFile(source, original);
  const { stdout } = await promisify(execFile)(process.execPath, [entry, 'import', '--from', source], {
    env: { ...process.env, LOGIC_MEMORY_DATA_DIR: data }, windowsHide: true,
  });
  assert.equal(JSON.parse(stdout).imported, 1);
  assert.equal(await readFile(source, 'utf8'), original);
  assert.equal(await readFile(join(data, 'logic.json'), 'utf8'), original);
  await assert.rejects(importLegacy(source, data), { code: 'EEXIST' });
  const client = await connect(data);
  try { assert.equal((await call(client, 'consult_logic', { query: '编码检测' })).matched_logic[0].logic_id, 'old-id'); }
  finally { await client.close(); }
  await writeFile(source, '{broken');
  await assert.rejects(importLegacy(source, join(root, 'invalid destination')));
  await assert.rejects(access(join(root, 'invalid destination', 'logic.json')));
  assert.equal(await readFile(join(data, 'logic.json'), 'utf8'), original);
  assert.throws(() => dataDirectory({ LOGIC_MEMORY_DATA_DIR: 'relative' }), /absolute/);
});
