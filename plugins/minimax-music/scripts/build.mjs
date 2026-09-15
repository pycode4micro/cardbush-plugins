import {build} from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
await build({entryPoints:[path.join(root,'src/main.mjs')],outfile:path.join(root,'scripts/minimax-music.mjs'),bundle:true,platform:'node',target:'node22',format:'esm',banner:{js:'import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);'},legalComments:'eof'});
const notices=[];
function collectModules(dir) {
  for(const item of fs.readdirSync(dir,{withFileTypes:true})) {
    if(!item.isDirectory()||item.name.startsWith('.'))continue;
    const full=path.join(dir,item.name);
    if(item.name.startsWith('@')) {collectModules(full);continue;}
    const meta=path.join(full,'package.json');if(!fs.existsSync(meta))continue;
    const pkg=JSON.parse(fs.readFileSync(meta,'utf8'));
    const licenses=fs.readdirSync(full).filter(n=>/^(license|licence|copying)(\.|$)/i.test(n)&&fs.statSync(path.join(full,n)).isFile());
    notices.push(`${pkg.name}@${pkg.version} — ${pkg.license||'see upstream license'}\n`+licenses.map(n=>fs.readFileSync(path.join(full,n),'utf8')).join('\n'));
    if(fs.existsSync(path.join(full,'node_modules')))collectModules(path.join(full,'node_modules'));
  }
}
collectModules(path.join(root,'node_modules'));
fs.writeFileSync(path.join(root,'THIRD_PARTY_LICENSES.txt'),('Build dependency license notices (includes development tooling)\n\n'+notices.join('\n\n========================================\n\n')).replace(/[ \t]+$/gm,''));
console.log('Built standalone scripts/minimax-music.mjs');
