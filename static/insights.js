/* Evidence rules, shared with tests. These observations do not establish causation. */
(function(root) {
  function assess(data, history, interval=1, now=Date.now()) {
    const at=Date.parse(data?.timestamp);
    if(!Number.isFinite(at) || now-at>Math.max(5000,interval*3000)) return {title:'Waiting for fresh measurements', findings:[], leaders:[]};
    const findings=[];
    const recent=history.filter(p=>Date.parse(p.timestamp)<=at && Date.parse(p.timestamp)>=at-60000).sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
    for(const [name,key,value] of [['CPU','cpu_percent',data.cpu?.percent],['GPU','gpu_percent',data.gpu?.percent]]) {
      if(!Number.isFinite(value)) {findings.push({text:`${name} utilization unavailable.`,severity:'unknown'});continue;}
      if(value<85) continue;
      let start=at,previous=at;
      for(let i=recent.length-1;i>=0;i--) {
        const p=recent[i],time=Date.parse(p.timestamp);
        if(!Number.isFinite(p[key]) || p[key]<85 || previous-time>Math.max(5000,interval*3000)) break;
        start=time;previous=time;
      }
      const seconds=Math.floor((at-start)/1000);
      findings.push({severity:seconds>=15?'load':'brief',text:seconds>=15?`${name} has stayed at or above 85% for ${seconds}s in the last minute (${value.toFixed(1)}% now).`:`${name} is at ${value.toFixed(1)}%; fewer than 15s of continuous high-load samples.`,action:name==='CPU'?'Review CPU leaders below and watch an app to see whether its load persists.':'Compare the GPU timeline with your graphics workload; per-app GPU attribution is unavailable.'});
    }
    const pressure=data.memory?.pressure?.level;
    if(pressure==='Warning' || pressure==='Critical') findings.push({severity:'load',text:`macOS reports ${pressure.toLowerCase()} memory pressure.`,action:'Review memory leaders and reduce an unneeded workload, then check whether pressure improves.'});
    else if(pressure!=='Normal') findings.push({severity:'unknown',text:'OS memory pressure unavailable; RAM usage alone cannot establish memory pressure.'});
    const free=data.disk?.available,total=data.disk?.total;
    if(Number.isFinite(free) && total>0 && free/total<0.1) findings.push({severity:'capacity',text:`Storage available to macOS is ${(free/1e9).toFixed(1)} GB (${(free/total*100).toFixed(1)}% of capacity).`,action:'Review the Storage tab for large directories. Low capacity alone does not prove a slowdown.'});
    const groups=new Map();
    for(const p of data.processes||[]) {
      const name=p.application||p.name;
      if(!groups.has(name)) groups.set(name,{name,cpu:0,memory:0});
      const g=groups.get(name);g.cpu+=Number.isFinite(p.cpu_percent)?p.cpu_percent:0;g.memory+=Number.isFinite(p.memory_percent)?p.memory_percent:0;
    }
    const leaders=[...groups.values()];
    const load=findings.some(f=>f.severity==='load');
    return {title:load?'Sustained load or memory pressure detected':findings.some(f=>f.severity==='brief')?'High utilization now — watching for persistence':findings.some(f=>f.severity==='unknown')?'Some measurements are unavailable':'No sustained CPU/GPU load or memory pressure detected', findings,
      leaders:leaders.sort((a,b)=>b.cpu-a.cpu).slice(0,3),memoryLeaders:[...groups.values()].sort((a,b)=>b.memory-a.memory).slice(0,3),partial:!!data.processes_truncated};
  }
  root.SentinelInsights={assess};
  if(typeof module!=='undefined') module.exports=root.SentinelInsights;
})(globalThis);
