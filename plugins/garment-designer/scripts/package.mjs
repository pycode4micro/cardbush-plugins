import { promises as fs } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
for(const filename of ['dist/server.mjs','dist/editor.html','dist/index_bg.wasm','TESTING.md'])await fs.access(path.join(root,filename));
const child=spawn(process.env.PYTHON||'python',[path.join(root,'scripts/package.py'),root],{cwd:root,stdio:'inherit',windowsHide:true});
child.on('error',error=>{console.error(error.message);process.exitCode=1;});
child.on('exit',code=>{process.exitCode=code??1;});
