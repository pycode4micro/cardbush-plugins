import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { KnowledgeAPI } from '../src/api.mjs';
const directory=await mkdtemp(path.join(tmpdir(),'knowledge-benchmark-'));
const api=new KnowledgeAPI({root:directory});
try {
  await api.call('knowledge_library',{library_id:'equipment',name:'设备操作资料'});
  const started=performance.now();
  for(let batch=0;batch<10;batch++){
    const documents=Array.from({length:100},(_,i)=>{const id='KB'+String(batch*100+i).padStart(5,'0');return{source_key:id,title:`${id} 设备维护手册`,text:`# ${id} 设备维护手册\n设备编号 ${id}。专用维护周期为 ${batch*100+i+10} 天。\n`+'维护时检查运行状态和温度，记录工单编号并保留操作记录。\n'.repeat(25)};});
    const result=await api.call('knowledge_import',{library_id:'equipment',documents});assert.equal(result.failed.length,0);
  }
  const indexed=performance.now()-started,latencies=[];
  for(let i=0;i<100;i++){
    const key='KB'+String((i*97)%1000).padStart(5,'0'),before=performance.now();
    const found=await api.call('knowledge_search',{query:key+' 维护周期',library_ids:['equipment'],limit:3});
    latencies.push(performance.now()-before);assert.ok(found.results.some(r=>r.source===key));
  }
  latencies.sort((a,b)=>a-b);
  const report={documents:1000,queries:100,retrieval_hits_at_3:100,index_ms:+indexed.toFixed(2),query_p50_ms:+latencies[49].toFixed(2),query_p95_ms:+latencies[94].toFixed(2),platform:process.platform,node:process.version,scope:'synthetic small-corpus local benchmark; excludes network and LLM latency',at:new Date().toISOString()};
  await mkdir('release',{recursive:true});await writeFile('release/benchmark.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}finally{api.store.close();}
