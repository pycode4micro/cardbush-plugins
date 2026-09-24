// Accepts an existing, built CardBush checkout. Uses its real plugin installer
// in an isolated temp directory; never touches the user's actual installed apps.
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const host=process.argv[2];if(!host)throw Error('Pass a built CardBush checkout path.');
const {installLocalProductPlugin}=await import(pathToFileURL(path.join(host,'dist-electron/localPluginInstall.js')));
const {loadProductPluginCatalog,loadEnabledProductPluginMcpServers}=await import(pathToFileURL(path.join(host,'dist-electron/productPlugins.js')));
const {resolvePluginManifest}=await import(pathToFileURL(path.join(host,'dist-electron/pluginManifest.js')));
const stage=await fs.mkdtemp(path.join(os.tmpdir(),'garment-cardbush-install-'));
let client;
try{
 const installed=path.join(stage,'installed');
 const version=JSON.parse(await fs.readFile(path.join(root,'.codex-plugin/plugin.json'),'utf8')).version;
 const receipt=await installLocalProductPlugin(path.join(root,`release/garment-designer-${version}.zip`),installed);assert.equal(receipt.id,'garment-designer');
 const plugin=path.join(installed,receipt.id),resolved=await resolvePluginManifest(plugin);assert.deepEqual(resolved.issues,[]);assert.equal(resolved.format,'openai');
 const catalog=await loadProductPluginCatalog([{path:installed,source:'user'}]);assert.equal(catalog.length,1);assert(catalog[0].components.some(c=>c.kind==='skill'));assert(catalog[0].components.some(c=>c.kind==='mcp'));
 const servers=await loadEnabledProductPluginMcpServers([{path:installed,source:'user'}],path.join(stage,'not-configured.json'));assert.equal(servers.length,1);
 const transportConfig=servers[0].transport;assert(transportConfig.args[0].startsWith(plugin));
 const transport=new StdioClientTransport({command:process.execPath,args:transportConfig.args,env:{...Object.fromEntries(Object.entries(process.env).filter(([,v])=>typeof v==='string')),...transportConfig.env,GARMENT_DESIGNER_DATA_DIR:path.join(stage,'data')},stderr:'pipe',cwd:plugin});
 client=new Client({name:'cardbush-package-test',version:'1'},{capabilities:{}});await client.connect(transport);
 assert.equal((await client.listTools()).tools.length,7);
 const result=await client.callTool({name:'garment_project',arguments:{action:'create',title:'打包安装验证',template:'dress'}});assert(!result.isError);
 const preview=await client.callTool({name:'garment_preview',arguments:{project_id:result.structuredContent.projectId}});assert(!preview.isError);assert(preview.content.some(c=>c.type==='image'));
 const presentation=await client.callTool({name:'garment_present',arguments:{project_id:result.structuredContent.projectId,revision:1}});assert(!presentation.isError);assert.deepEqual(presentation.structuredContent.errors,[]);assert.equal(presentation.structuredContent.files.length,3);
 for(const file of presentation.structuredContent.files)assert((await fs.stat(file.path)).size>1000);
 assert.equal(catalog[0].components.filter(c=>c.kind==='skill').length,2);
 const resource=await client.readResource({uri:'ui://garment-designer/editor'});assert(resource.contents[0].text.includes('确认此版本'));
 console.log('PASS: CardBush ZIP import, manifest/skill compatibility, resolved MCP transport, isolated installed server (no node_modules), real PNG preview and MCP App resource.');
}finally{
 if(client)await client.close();
 assert.equal(path.dirname(path.resolve(stage)),path.resolve(os.tmpdir()));assert.match(path.basename(stage),/^garment-cardbush-install-/);
 await fs.rm(stage,{recursive:true,force:true});
}
