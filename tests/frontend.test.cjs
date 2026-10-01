const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
const core=require('../static/core.js');
test('history merges races, deduplicates, expires and bounds samples',()=>{
 const now=Date.now();const sample=i=>({timestamp:new Date(now-i*1000).toISOString(),cpu_percent:i});
 const rows=core.mergeHistory([sample(2),sample(1)],[sample(1),sample(0),sample(4000)],now);
 assert.equal(rows.length,3);assert.equal(rows.at(-1).cpu_percent,0);
 assert.equal(core.mergeHistory([],Array.from({length:4000},(_,i)=>({timestamp:new Date(now-i).toISOString()})),now).length,3600);
});
test('staleness follows sampling interval',()=>{assert.equal(core.stale(1000,1,7000),true);assert.equal(core.stale(1000,10,7000),false);});
test('application groups aggregate CPU and memory without enabling group termination',()=>{
 const rows=core.groupProcesses([{name:'A',application:'App',cpu_percent:100,memory_percent:2},{name:'B',application:'App',cpu_percent:50,memory_percent:3}],'cpu_percent');
 assert.equal(rows.length,1);assert.equal(rows[0].cpu_percent,150);assert.equal(rows[0].memory_percent,5);assert.equal(rows[0].grouped,true);
});
test('CSV exports only aggregate allowlist',()=>{const csv=core.csv([{timestamp:'time',cpu_percent:1,hostname:'secret',cmdline:'secret'}]);assert.equal(csv.includes('secret'),false);});
test('obsolete inspector response cannot replace selected process',async()=>{
 const source=fs.readFileSync('static/dashboard.js','utf8').match(/    async function openInspector\(pid, name\) \{[\s\S]*?\n    \}/)[0];
 const elements=new Map();const pending=new Map();
 const context={AbortController,document:{activeElement:null},focusBeforeInspector:null,inspectorGeneration:0,inspectorAbort:null,$:id=>{if(!elements.has(id))elements.set(id,{textContent:'',focus(){},classList:{add(){},remove(){}}});return elements.get(id);},fetch:url=>new Promise(resolve=>pending.set(url,resolve)),errorDetail:()=>''};
 vm.createContext(context);vm.runInContext(source,context);
 const first=context.openInspector(1,'first'),second=context.openInspector(2,'second');
 pending.get('/api/process/2/details')({ok:true,json:async()=>({process:{pid:2,name:'second'}})});await second;
 pending.get('/api/process/1/details')({ok:true,json:async()=>({process:{pid:1,name:'first'}})});await first;
 assert.equal(elements.get('inspector-title').textContent,'second');
});
test('watch groups survive PID changes and distinguish absent from truncated',()=>{
 const row={pid:1,application:'Editor',name:'helper',cpu_percent:120,memory_percent:2};
 assert.equal(core.watchSample([row,{...row,pid:2}],'Editor').cpu_percent,240);
 assert.equal(core.watchSample([{...row,pid:999}],'Editor').count,1);
 assert.equal(core.watchSample([],'Editor').cpu_percent,null);
 assert.equal(core.watchSample([],'Editor').status,'not running');
 assert.equal(core.watchSample([],'Editor',true).status,'not sampled');
 assert.equal(core.watchSample([row],'Editor',true).status,'partial');
});
const insights=require('../static/insights.js');
test('assessment requires continuous load, resets gaps, ignores swap and throughput as causes',()=>{
 const now=Date.now();const data={timestamp:new Date(now).toISOString(),cpu:{percent:90},gpu:{percent:10},memory:{pressure:{level:'Normal'},swap:{used:10000000000}},disk_io:{write_bps:1e10}};
 const history=Array.from({length:21},(_,i)=>({timestamp:new Date(now-(20-i)*1000).toISOString(),cpu_percent:90,gpu_percent:10}));
 assert.match(insights.assess(data,history,1,now).title,/Sustained/);
 assert.equal(insights.assess(data,history,1,now).findings.length,1);
 assert.match(insights.assess(data,[history[0],history.at(-1)],1,now).title,/persistence/);
 assert.match(insights.assess(data,history,1,now+10000).title,/fresh/);
});
test('assessment reports OS pressure, unknown GPU and capacity without assuming a culprit',()=>{
 const now=Date.now();const data={timestamp:new Date(now).toISOString(),cpu:{percent:10},gpu:{percent:null},memory:{pressure:{level:'Critical'}},disk:{available:5,total:100},processes:[{name:'A',cpu_percent:150,memory_percent:2},{name:'A',cpu_percent:50,memory_percent:3}],processes_truncated:true};
 const result=insights.assess(data,[],1,now);
 assert.equal(result.findings.length,3);assert.equal(result.leaders[0].cpu,200);assert.equal(result.memoryLeaders[0].memory,5);assert.equal(result.partial,true);
});
