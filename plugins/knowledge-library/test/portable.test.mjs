import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, cp, mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { PDFDocument, StandardFonts } from 'pdf-lib';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('runtime copied out of repository needs no node_modules; packaged PDF worker works', async () => {
  const directory = await mkdtemp(path.join(tmpdir(),'knowledge-portable-')), packageDir = path.join(directory,'Plugin With Spaces');
  await mkdir(packageDir); await cp(path.join(root,'runtime'),path.join(packageDir,'runtime'),{recursive:true});
  const client = new Client({name:'portable-test',version:'1.0.0'}), transport = new StdioClientTransport({command:process.execPath,args:[path.join(packageDir,'runtime/server.mjs')],cwd:packageDir,env:{...process.env,KNOWLEDGE_DATA_DIR:path.join(directory,'data')},stderr:'pipe'});
  let stderr='';transport.stderr?.on('data',part=>{stderr+=part;});
  try {
    await client.connect(transport);
    const call=async(name,args)=>{const result=await client.callTool({name,arguments:args});assert.ok(!result.isError,JSON.stringify(result)+'\n'+stderr);return result.structuredContent;};
    await call('knowledge_library',{library_id:'portable',name:'Portable'});
    const pdf=await PDFDocument.create(),font=await pdf.embedFont(StandardFonts.Helvetica);pdf.addPage().drawText('Portable parser refund window: 21 days.',{font,x:40,y:700,size:12});
    const result=await call('knowledge_import',{library_id:'portable',files:[{name:'terms.pdf',data_base64:Buffer.from(await pdf.save()).toString('base64')}]});
    assert.deepEqual(result.failed,[],stderr);
    const found=await call('knowledge_search',{query:'refund window'});assert.match(found.results[0].text,/21 days/);assert.equal(found.results[0].location.page,1);
    assert.match((await client.readResource({uri:'ui://knowledge-library/library'})).contents[0].text,/资料库/);
    assert.equal((await readFile(path.join(packageDir,'runtime/server.mjs'),'utf8')).includes('D:/proj/cardbush'),false);
  }finally{await client.close();}
});
