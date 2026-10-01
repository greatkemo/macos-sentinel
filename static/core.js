/* Pure bounded-data helpers shared by the browser and Node regression tests. */
(function(root) {
  const api = {
    mergeHistory(old, incoming, now=Date.now()) {
      const samples = new Map([...old,...incoming].filter(p=>Number.isFinite(Date.parse(p.timestamp)) && Date.parse(p.timestamp)>=now-3600000).map(p=>[p.timestamp,p]));
      return [...samples.values()].sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp)).slice(-3600);
    },
    stale(at, interval, now=Date.now()) { return !at || now-at > Math.max(5000,interval*3000); },
    groupProcesses(rows, sort) {
      const groups = new Map();
      for (const row of rows) {
        const key=row.application || row.name;
        if (!groups.has(key)) groups.set(key,{...row,name:key,cpu_percent:0,memory_percent:0,count:0,grouped:true,depth:0});
        const group=groups.get(key); group.cpu_percent+=row.cpu_percent; group.memory_percent+=row.memory_percent; group.count++;
      }
      return [...groups.values()].map(g=>({...g,name:`${g.name} (${g.count})`})).sort((a,b)=>sort==='name'?a.name.localeCompare(b.name):b[sort]-a[sort]);
    },
    watchSample(rows, name, truncated=false) {
      const matches=rows.filter(p=>(p.application || p.name)===name);
      return {count:matches.length, cpu_percent:matches.length?matches.reduce((n,p)=>n+p.cpu_percent,0):null,
        memory_percent:matches.length?matches.reduce((n,p)=>n+p.memory_percent,0):null,
        status:matches.length?(truncated?'partial':'running'):(truncated?'not sampled':'not running')};
    },
    csv(samples) {
      const keys=['timestamp','cpu_percent','memory_percent','gpu_percent','download_bps','upload_bps','disk_read_bps','disk_write_bps','swap_used'];
      const quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';
      return [keys.join(','),...samples.map(s=>keys.map(k=>quote(s[k])).join(','))].join('\n');
    },
  };
  root.SentinelCore=api;
  if(typeof module !== 'undefined') module.exports=api;
})(globalThis);
