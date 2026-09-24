import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const binary=process.argv[2]||process.env.ELECTRON_BINARY;
if(!binary)throw new Error('Pass an installed Electron executable as the first argument.');
const env={...process.env,GARMENT_TEST_NODE:process.execPath};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(binary,[path.join(root,'test/ui.cjs')],{cwd:root,env,stdio:'inherit',windowsHide:true});
child.on('error',error=>{console.error(error);process.exitCode=1;});child.on('exit',code=>{process.exitCode=code??1;});
