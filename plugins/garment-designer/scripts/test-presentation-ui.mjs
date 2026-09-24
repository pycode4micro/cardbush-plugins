import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const binary=process.argv[2]||process.env.ELECTRON_BINARY;
if(!binary)throw new Error('Pass an installed Electron executable.');
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(binary,[path.join(root,'test/presentation-ui.cjs')],{cwd:root,env,windowsHide:true,stdio:'inherit'});
child.on('error',error=>{console.error(error);process.exitCode=1;});child.on('exit',code=>{process.exitCode=code??1;});
