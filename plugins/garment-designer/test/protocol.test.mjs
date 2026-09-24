import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

test('packaged stdio server: discovery, tools, resource, errors, restart persistence',async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'garment-designer-protocol-'));
  const connect=async()=>{
    const transport=new StdioClientTransport({command:process.execPath,args:[path.resolve('dist/server.mjs')],env:{...Object.fromEntries(Object.entries(process.env).filter(([,v])=>typeof v==='string')),GARMENT_DESIGNER_DATA_DIR:root},stderr:'pipe'});
    const client=new Client({name:'garment-test',version:'1.0.0'},{capabilities:{}});await client.connect(transport);return client;
  };
  let client;
  try{
    client=await connect();const tools=await client.listTools();assert.equal(tools.tools.length,6);assert(tools.tools.find(t=>t.name==='garment_project')._meta.ui.resourceUri);
    const resources=await client.listResources();assert.equal(resources.resources[0].mimeType,'text/html;profile=mcp-app');
    const page=await client.readResource({uri:resources.resources[0].uri});assert(page.contents[0].text.includes('确认此版本'));assert(!page.contents[0].text.includes('/* APP_SCRIPT */'));assert(!page.contents[0].text.includes('<script src='));
    const create=await client.callTool({name:'garment_project',arguments:{action:'create',title:'协议实测',template:'shirt'}});assert(!create.isError);const id=create.structuredContent.projectId;assert(create._meta.garment.document);
    const edit=await client.callTool({name:'garment_edit',arguments:{project_id:id,expected_revision:1,patch:{parts:[{id:'collar',prompt:'窄小圆领',fill:'#445577'}]}}});assert(!edit.isError);assert.equal(edit.structuredContent.revision,2);
    const stale=await client.callTool({name:'garment_edit',arguments:{project_id:id,expected_revision:1,patch:{title:'不应覆盖'}}});assert(stale.isError);
    const preview=await client.callTool({name:'garment_preview',arguments:{project_id:id}});assert.equal(preview.content[1].type,'image');assert.equal(preview.content[1].mimeType,'image/png');
    const unconfirmed=await client.callTool({name:'garment_render',arguments:{action:'prepare',project_id:id,revision:2,request_id:'attempt'}});assert(unconfirmed.isError);
    const missing=await client.callTool({name:'garment_project',arguments:{action:'get'}});assert(missing.isError);
    await client.close();client=await connect();const reopened=await client.callTool({name:'garment_project',arguments:{action:'get',project_id:id}});assert.equal(reopened.structuredContent.revision,2);assert.equal(reopened.structuredContent.document.scene.parts.find(p=>p.id==='collar').fill,'#445577');
  }finally{if(client)await client.close();assert.equal(path.dirname(path.resolve(root)),path.resolve(os.tmpdir()));assert.match(path.basename(root),/^garment-designer-protocol-/);await fs.rm(root,{recursive:true,force:true});}
});
