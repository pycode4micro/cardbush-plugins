import { build } from 'esbuild';
import { promises as fs } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
await fs.mkdir(path.join(root,'dist'),{recursive:true});
const bundle={absWorkingDir:root,bundle:true,metafile:true,platform:'node',format:'esm',target:'node22',banner:{js:"import { createRequire as __createRequire } from 'node:module'; const require = __createRequire(import.meta.url);"}};
const server=await build({...bundle,entryPoints:['src/server.mjs'],outfile:'dist/server.mjs',external:['./presentation.mjs']});
const presentation=await build({...bundle,entryPoints:['src/presentation.mjs'],outfile:'dist/presentation.mjs'});
const ui=await build({absWorkingDir:root,entryPoints:['ui/editor.mjs'],bundle:true,write:false,platform:'browser',format:'iife',target:'chrome120'});
const html=await fs.readFile(path.join(root,'ui/editor.html'),'utf8');
await fs.writeFile(path.join(root,'dist/editor.html'),html.replace('/* APP_SCRIPT */',()=>ui.outputFiles[0].text.replaceAll('</script','<\\/script')));
await fs.copyFile(path.join(root,'node_modules/@resvg/resvg-wasm/index_bg.wasm'),path.join(root,'dist/index_bg.wasm'));
const dependencies=[...new Set([...Object.keys(server.metafile.inputs),...Object.keys(presentation.metafile.inputs)].map(p=>p.match(/^node_modules\/((?:@[^/]+\/)?[^/]+)/)?.[1]).filter(Boolean))].sort();
let notices='Third-party software bundled in Garment Designer\n\n';
for(const name of dependencies){
  const directory=path.join(root,'node_modules',name),pkg=JSON.parse(await fs.readFile(path.join(directory,'package.json'),'utf8'));
  notices+=`\n${'='.repeat(70)}\n${name} ${pkg.version}\nLicense: ${pkg.license||'See upstream notices'}\n`;
  if(pkg.repository)notices+=`Source: ${typeof pkg.repository==='string'?pkg.repository:pkg.repository.url}\n`;
  const files=(await fs.readdir(directory)).filter(n=>/^(LICENSE|LICENCE|COPYING|NOTICE)([.-]|$)/i.test(n));
  for(const name of files){const filename=path.join(directory,name);if((await fs.stat(filename)).isFile())notices+='\n'+await fs.readFile(filename,'utf8')+'\n';}
  if(name==='@resvg/resvg-wasm')notices+='\nUnmodified resvg-js 2.6.2 source: https://github.com/yisibl/resvg-js/tree/v2.6.2\nMPL-2.0 text is included in third-party/resvg-LICENSE.txt.\n';
}
await fs.writeFile(path.join(root,'THIRD_PARTY_NOTICES.txt'),notices);
console.log('Built portable MCP server, vector renderer, presentation exporter and interactive editor.');
