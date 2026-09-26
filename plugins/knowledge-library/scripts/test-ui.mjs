import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { readFileSync } from 'node:fs';
const source = process.env.CARDBUSH_SOURCE_ROOT;
if (!source) throw new Error('Set CARDBUSH_SOURCE_ROOT to the CardBush checkout containing Electron.');
const require = createRequire(path.join(source,'package.json'));
const env = {...process.env, KNOWLEDGE_TEST_NODE:process.execPath}; delete env.ELECTRON_RUN_AS_NODE; delete env.NODE_OPTIONS;
const start = Date.now();
const result = spawnSync(require('electron'),['test/ui.cjs'],{env,windowsHide:true,stdio:'inherit',timeout:60000});
if(result.error)console.error(result.error);
process.exitCode=result.status??1;
try {
  const report=JSON.parse(readFileSync('.test-data/ui-result.json','utf8'));
  if(!report.passed||Date.parse(report.at)<start)throw Error('UI did not reach assertions.');
}catch(error){console.error(error.message);process.exitCode=1;}
