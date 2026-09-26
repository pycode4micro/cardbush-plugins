import { McpServer, ResourceTemplate } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { readFile } from 'node:fs/promises';
import { KnowledgeAPI, TOOL_DEFINITIONS } from './api.mjs';

const api = new KnowledgeAPI();
const server = new McpServer({ name: 'knowledge-library', version: '0.1.0' });
const uri = 'ui://knowledge-library/library';
for (const [name, definition] of Object.entries(TOOL_DEFINITIONS)) {
  if (api.store.readOnly && !definition.readOnly) continue;
  server.registerTool(name, { description: definition.description, inputSchema: definition.schema,
    annotations: { readOnlyHint: definition.readOnly, destructiveHint: false, openWorldHint: false },
    ...(definition.app ? { _meta: { ui: { resourceUri: uri } } } : {}),
  }, async args => {
    try { const data = await api.call(name, args); return { content: [{ type: 'text', text: JSON.stringify(data) }], structuredContent: data }; }
    catch (error) { return { isError: true, content: [{ type: 'text', text: error.message || '资料库操作失败。' }] }; }
  });
}
server.registerResource('library-panel', uri, { mimeType: 'text/html;profile=mcp-app' }, async () => ({ contents: [{ uri, mimeType: 'text/html;profile=mcp-app',
  text: await readFile(new URL('./library.html', import.meta.url), 'utf8'), _meta: { ui: { csp: { connectDomains: [], resourceDomains: [] } } },
}] }));
server.registerResource('knowledge-source', new ResourceTemplate('knowledge://documents/{document}/revisions/{revision}/chunks/{chunk}', { list: undefined }), { mimeType: 'text/plain' }, async (uri, variables) => {
  const data = await api.call('knowledge_read', { document_id: variables.document, revision: Number(variables.revision), chunk: Number(variables.chunk), limit: 1 });
  return { contents: [{ uri: uri.href, mimeType: 'text/plain', text: JSON.stringify(data) }] };
});
process.on('exit', () => api.store.close());
await server.connect(new StdioServerTransport());
