import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
const root = process.env.CARDBUSH_SOURCE_ROOT;
if (!root) throw new Error('Set CARDBUSH_SOURCE_ROOT to a built CardBush checkout.');
const load = file => import(pathToFileURL(path.join(root, file)).href);
const { resolvePluginManifest } = await load('dist-electron/pluginManifest.js');
const { resolvePluginMcpConnection } = await load('dist-electron/pluginMcpConfiguration.mjs');
const { ToolRegistry } = await load('packages/bush-runtime/dist/index.js');
const { McpClientManager } = await load('packages/bush-mcp-client/dist/index.js');
const { BUSH_MCP_SNAPSHOT_PROTOCOL } = await load('packages/bush-protocol/dist/index.js');

test('CardBush standard manifest resolves portable server and namespaced discovery', async () => {
  const manifest = await resolvePluginManifest(path.resolve('.'));
  assert.equal(manifest.manifest.name, 'knowledge-library'); assert.deepEqual(manifest.extensions.issues, []);
  const connection = resolvePluginMcpConnection('knowledge-library','knowledge-library',path.resolve('.'),manifest.manifest.mcpServers,{}, {}, [], path.resolve('unused-data'));
  assert.equal(connection.transport.kind, 'stdio');
  assert.equal(connection.transport.args[0].replaceAll('\\','/'), path.resolve('runtime/server.mjs').replaceAll('\\','/'));
  const registry = new ToolRegistry(), manager = new McpClientManager({ registry });
  const data = await mkdtemp(path.join(tmpdir(), 'knowledge-cardbush-'));
  try {
    const applied = await manager.apply({ protocol: BUSH_MCP_SNAPSHOT_PROTOCOL, snapshotId:'knowledge-demo', revision:1, servers:[{
      id:'plugin_knowledge_library', transport:{...connection.transport, command:process.execPath, env:{KNOWLEDGE_DATA_DIR:data}},
      versionMode:'auto', defaultToolPolicy:{permission:'ask',parallelSafe:false,visibleToChild:true},toolPolicies:{},
    }] });
    assert.equal(applied.servers[0].tools.length, 7);
    assert.equal(registry.resolve('mcp__plugin_knowledge_library__knowledge_search').mcpHook.readOnly, true);
    assert.equal(registry.resolve('mcp__plugin_knowledge_library__knowledge_import').mcpHook.readOnly, false);
  } finally { await manager.close(); }
});
