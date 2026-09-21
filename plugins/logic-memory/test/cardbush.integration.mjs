import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';

const root = process.env.CARDBUSH_SOURCE_ROOT;
if (!root) throw Error('Set CARDBUSH_SOURCE_ROOT to a built CardBush checkout.');
const load = file => import(pathToFileURL(resolve(root, file)).href);
const { resolvePluginManifest } = await load('dist-electron/pluginManifest.js');
const { resolvePluginMcpConnection } = await load('dist-electron/pluginMcpConfiguration.mjs');
const { ToolRegistry, InMemoryRuntimeHost, ToolExecutionCoordinator } = await load('packages/bush-runtime/dist/index.js');
const { McpClientManager } = await load('packages/bush-mcp-client/dist/index.js');
const { BUSH_MCP_SNAPSHOT_PROTOCOL } = await load('packages/bush-protocol/dist/index.js');

test('optional repository plugin uses portable manifest and is absent from bundled/default plugins', async () => {
  const manifest = await resolvePluginManifest(resolve('.'));
  assert.equal(manifest.format, 'openai'); assert.equal(manifest.manifest.name, 'logic-memory');
  assert.equal(manifest.extensions.issues.length, 0);
  const config = resolvePluginMcpConnection('logic-memory', 'logic-memory', resolve('.'), manifest.manifest.mcpServers, {}, {}, [], resolve('unused-test-data'));
  assert.equal(config.transport.kind, 'stdio'); assert.equal(config.transport.command, 'node');
  assert.equal(config.transport.args[0].replaceAll('\\', '/'), resolve('runtime/cli.mjs').replaceAll('\\', '/'));
  const marketplace = JSON.parse(await readFile('../../.agents/plugins/marketplace.json', 'utf8'));
  assert.equal(marketplace.plugins.find(plugin => plugin.name === 'logic-memory').policy.installation, 'AVAILABLE');
  const bundled = JSON.parse(await readFile(join(root, 'assets/plugins/marketplace.json'), 'utf8'));
  assert.ok(!bundled.plugins.some(plugin => plugin.name === 'logic-memory'));
});

test('CardBush exposes namespaced tools only after explicit connection and removes them on disconnect', async t => {
  const data = await mkdtemp(join(tmpdir(), 'logic-cardbush-')); t.after(() => rm(data, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }));
  const registry = new ToolRegistry(); new InMemoryRuntimeHost({ toolRegistry: registry, dataRoot: data });
  assert.ok(!registry.resolve('consult_logic')); assert.ok(!registry.resolve('learn_logic'));
  const manager = new McpClientManager({ registry });
  const snapshot = { protocol: BUSH_MCP_SNAPSHOT_PROTOCOL, snapshotId: 'logic-test', revision: 1, servers: [{
    id: 'logic_memory', transport: { kind: 'stdio', command: process.execPath, args: [resolve('runtime/cli.mjs')], env: { LOGIC_MEMORY_DATA_DIR: data } },
    versionMode: 'auto', defaultToolPolicy: { permission: 'ask', parallelSafe: false, visibleToChild: true }, toolPolicies: {},
  }] };
  try {
    const applied = await manager.apply(snapshot);
    assert.equal(applied.servers[0].tools.length, 2);
    assert.equal(registry.resolve('mcp__logic_memory__consult_logic').mcpHook.readOnly, true);
    let permissions = 0;
    const coordinator = new ToolExecutionCoordinator({ registry, permissions: { request: async input => {
      permissions++; return { protocol: 'bush.runtime_permission_answer.v1', permissionId: 'p', answerId: 'a', decision: 'allow_once', grantedCapabilityIds: input.capabilityIds };
    } } });
    const outcome = await coordinator.execute({ protocol: 'bush.tool_call.v1', id: 'learn', name: 'mcp__logic_memory__learn_logic',
      argumentsText: JSON.stringify({ scenario: 'host connection', correction: 'standard MCP only' }) },
      { requestId: 'r', sessionId: 's', turnId: 't', round: 1, ordinal: 0 });
    assert.equal(outcome.kind, 'returned', JSON.stringify(outcome)); assert.equal(outcome.result.structuredContent.status, 'learned');
    assert.equal(permissions, 1);
    await manager.apply({ ...snapshot, snapshotId: 'removed', revision: 2, servers: [] });
    assert.ok(!registry.resolve('mcp__logic_memory__learn_logic')); assert.ok(!registry.resolve('mcp__logic_memory__consult_logic'));
  } finally { await manager.close(); }
});
