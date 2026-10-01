// History, diagnostics, settings and export controls. Loaded after dashboard.js.
    async function apiJSON(url, options={}) {
      const response = await fetch(url, options);
      const body = await response.json();
      if (!response.ok) throw new Error(errorDetail(body, response.statusText));
      return body;
    }
    function download(name, text, type) {
      const url=URL.createObjectURL(new Blob([text],{type}));
      const anchor=document.createElement('a'); anchor.href=url; anchor.download=name; anchor.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    function recordHistory(data) {
      const sample={timestamp:data.timestamp,cpu_percent:data.cpu.percent,memory_percent:data.memory.percent,gpu_percent:data.gpu?.percent??null,
        download_bps:data.network.download_bps,upload_bps:data.network.upload_bps,
        disk_read_bps:data.disk_io?.read_bps,disk_write_bps:data.disk_io?.write_bps,swap_used:data.memory.swap?.used};
      historySamples=SentinelCore.mergeHistory(historySamples,[sample]);
      recordWatches(data);
      if(currentTab==='overview' && !document.hidden) renderHistory();
    }
    function renderHistory() {
      const now=timelinePausedAt || Date.now();
      const samples=historySamples.filter(p=>Date.parse(p.timestamp)>=now-selectedWindow*1000 && Date.parse(p.timestamp)<=now);
      // Downsample presentation only; exports retain all samples. Preserve gaps.
      const stride=Math.max(1,Math.ceil(samples.length/300));
      const plot=[];
      samples.forEach((sample,index)=>{
        if(index && Date.parse(sample.timestamp)-Date.parse(samples[index-1].timestamp)>Math.max(15000,sampleInterval*3000)) {
          plot.push({timestamp:new Date(Date.parse(sample.timestamp)-1).toISOString()});
        }
        if(index%stride===0 || index===samples.length-1) plot.push(sample);
      });
      renderTimeline(samples, now);
      for(const [chart,keys] of [[cpuChart,['cpu_percent']],[gpuChart,['gpu_percent']],[netChart,['download_bps','upload_bps']]]) {
        chart.data.labels=[];
        chart.options.scales.x.type='linear';
        chart.options.scales.x.min=now-selectedWindow*1000;
        chart.options.scales.x.max=now;
        chart.options.scales.x.ticks.callback=value=>new Date(value).toLocaleTimeString();
        keys.forEach((key,i)=>{chart.data.datasets[i].data=plot.map(p=>({x:Date.parse(p.timestamp),y:p[key]??null}));chart.data.datasets[i].spanGaps=false;});
        chart.update('none');
      }
    }
    async function loadHistory() {
      try {
        const body=await apiJSON(`/api/history?seconds=3600`);
        historySamples=SentinelCore.mergeHistory(historySamples,body.samples);
        renderHistory(); renderEvents(body.events);
      } catch(error) {showToast(error.message);}
    }
    function renderEvents(events) {
      timelineEvents=events;
      $('threshold-events').replaceChildren();
      for(const event of events.slice(-20).reverse()) {
        const item=document.createElement('li');item.textContent=`${new Date(event.timestamp).toLocaleTimeString()} · ${event.metric} exceeded ${event.threshold}% for ${event.duration_seconds}s`;
        $('threshold-events').append(item);
      }
      if(!events.length) $('threshold-events').textContent='No sustained threshold events this session.';
    }
    let insightSample=null;
    function renderInsights() {
      const result=SentinelInsights.assess(insightSample,historySamples,sampleInterval);
      $('insight-title').textContent=result.title;
      $('insight-findings').replaceChildren();
      for(const finding of result.findings) {
        const item=document.createElement('li');item.textContent=finding.text+(finding.action?' '+finding.action:'');
        $('insight-findings').append(item);
      }
      $('insight-leaders').replaceChildren();
      for(const [label,rows,key] of [['CPU leaders',result.leaders,'cpu'],['Memory leaders (summed RSS)',result.memoryLeaders||[],'memory']]) {
        if(!rows.length) continue;
        const line=document.createElement('p');
        line.textContent=label+': '+rows.map(p=>`${p.name} ${p[key].toFixed(1)}%`).join(' · ');
        $('insight-leaders').append(line);
      }
      if(result.leaders.length) {
        const note=document.createElement('p');note.className='text-slate-500';
        note.textContent=(result.partial?'Partial process sample. ':'')+'CPU uses 100% per logical core; summed RSS can double-count shared memory. Leaders are current observations, not confirmed causes.';
        $('insight-leaders').append(note);
      }
    }
    function renderDiagnostics(data) {
      insightSample=data;
      renderInsights();
      const monitor=data.monitor;
      $('collector-profile').textContent=monitor?.collectors?`Collector wall time: memory ${monitor.collectors.memory_ms} ms · processes ${monitor.collectors.processes_ms} ms · GPU ${monitor.collectors.gpu_ms} ms`:'';
      $('monitor-overhead').textContent=monitor?`Monitor server: collection ${monitor.collection_ms} ms / ${Math.round(sampleInterval*1000)} ms target interval · CPU ${monitor.cpu_percent==null?'warming up':monitor.cpu_percent.toFixed(1)+'% of one core'} · RSS ${formatBytes(monitor.rss_bytes)}. Browser and child-process overhead excluded.`:'Monitor overhead unavailable';
      $('collector-freshness').textContent=['disk','battery'].map(key=>{
        const reading=data[key],f=reading?.freshness;
        if(!f) return `${key}: freshness unavailable`;
        return `${key}: ${f.age_seconds==null?'waiting for first reading':`${f.age_seconds}s old`}${f.age_seconds>45?' · stale':''}${f.refreshing?' · refreshing':''}${f.error?' · refresh failed':''} · ${f.duration_ms??'—'} ms last check`;
      }).join(' | ');
      const gpu=data.gpu || {};
      $("gpu-pct").textContent=gpu.percent==null?"—":`${formatPct(gpu.percent)}%`;
      $("gpu-status").textContent=gpu.percent==null?(gpu.reason || "GPU utilization unavailable"):"Live driver counter · selected history window";
      $("gpu-devices").replaceChildren();
      for (const device of gpu.devices || []) {
        const line=document.createElement("p");
        const pct=value=>value==null?"unavailable":`${formatPct(value)}%`;
        line.textContent=`${device.name}: ${pct(device.percent)} · renderer ${pct(device.renderer_percent)} · tiler ${pct(device.tiler_percent)} · shared RAM in use ${formatBytes(device.shared_memory_bytes)}`;
        $("gpu-devices").append(line);
      }
      const swap=data.memory.swap;
      const io=data.disk_io;
      const pressure=data.memory.pressure;
      const rate=v=>v==null?'unavailable':formatRate(v);
      $('diagnostic-metrics').textContent=`OS memory pressure: ${pressure?.level || 'unavailable'} · Swap: ${swap?`${formatBytes(swap.used)} / ${formatBytes(swap.total)}`:'unavailable'} · Disk read ${rate(io?.read_bps)} / write ${rate(io?.write_bps)}`;
      $('diagnostic-metrics').title=pressure?.reason || '';
      $('interface-metrics').replaceChildren();
      for(const iface of data.network.interfaces || []) {
        const line=document.createElement('p');line.textContent=`${iface.name}: ↓ ${rate(iface.download_bps)} ↑ ${rate(iface.upload_bps)}`; $('interface-metrics').append(line);
      }
    }
    function freshness() {
      if(insightSample) renderInsights();
      const age=lastSampleAt?Math.max(0,Math.floor((Date.now()-lastSampleAt)/1000)):null;
      const stale=SentinelCore.stale(lastSampleAt,sampleInterval);
      $('sample-age').textContent=age==null?'Waiting for sample':`${stale?'Stale · ':''}sample ${age}s ago`;
      $('sample-age').classList.toggle('text-amber-300',stale);
      if(ws?.readyState===WebSocket.OPEN) {
        if(stale) { $('conn-label').textContent='Stale telemetry';$('conn-pill').className='chip border-amber-500 text-amber-300'; }
        else setConnection(true);
      }
    }
    $('history-window').addEventListener('change',()=>{selectedWindow=Number($('history-window').value);renderHistory();});
    $('sample-interval').addEventListener('change',async()=>{
      try {const body=await apiJSON('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({interval:Number($('sample-interval').value),adaptive:$('adaptive-sampling').checked})});sampleInterval=body.interval;}
      catch(error){showToast(error.message);}
    });
    $('adaptive-sampling').addEventListener('change',()=>$('sample-interval').dispatchEvent(new Event('change')));
    $('compact').addEventListener('change',()=>document.body.classList.toggle('compact',$('compact').checked));
    $('export-history').addEventListener('click',()=>download('sentinel-history.csv',SentinelCore.csv(historySamples.filter(p=>Date.parse(p.timestamp)>=Date.now()-selectedWindow*1000)),'text/csv'));
    $('export-diagnostics').addEventListener('click',async()=>{try{download('sentinel-redacted.json',JSON.stringify(await apiJSON('/api/export'),null,2),'application/json');}catch(error){showToast(error.message);}});
    $('proc-sort').addEventListener('change',()=>{processPage=0;renderProcesses();});
    $('proc-prev').addEventListener('click',()=>{processPage--;renderProcesses();});
    $('proc-next').addEventListener('click',()=>{processPage++;renderProcesses();});
    $('proc-group').addEventListener('change',()=>{if($('proc-group').checked) setProcessView('flat');renderProcesses();});
    $('proc-body').addEventListener('keydown',event=>{if(event.target.tagName==='TR' && (event.key==='Enter'||event.key===' ')){event.preventDefault();event.target.click();}});
    $('storage-cancel').addEventListener('click',async()=>{try{renderScan(await apiJSON('/api/storage/scans',{method:'DELETE'}));storageLoadedAt=0;}catch(error){showToast(error.message);}});
    $('storage-roots').addEventListener('click',()=>{storagePath=null;loadStorage(true);});
    $('app-search').addEventListener('input',()=>{systemSection='applications';renderSystem();});
    ['app-kind','app-sort','app-order'].forEach((id)=>$(id).addEventListener('change',()=>{systemSection='applications';renderSystem();}));
    $('system-refresh').addEventListener('click',()=>loadSystemInfo(true));
    $('apps-refresh').addEventListener('click',()=>loadInstalledApps(true));
    $('apps-search').addEventListener('input',()=>renderInstalledApps());
    ['apps-store','apps-signed','apps-location','apps-sort','apps-order'].forEach((id)=>$(id).addEventListener('change',()=>renderInstalledApps()));
    document.addEventListener('keydown',event=>{
      if(event.key!=='Tab'||$('modal').classList.contains('hidden')) return;
      const controls=[...$('modal').querySelectorAll('button:not(:disabled)')];
      const first=controls[0],last=controls.at(-1);
      if(event.shiftKey && (document.activeElement===first || !$('modal').contains(document.activeElement))){event.preventDefault();last.focus();}
      else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
    });
    document.querySelectorAll('[data-sys]').forEach(button=>{button.setAttribute('role','tab');button.setAttribute('aria-selected',button.dataset.sys===systemSection);button.addEventListener('click',()=>document.querySelectorAll('[data-sys]').forEach(tab=>tab.setAttribute('aria-selected',tab===button)));});
    setInterval(freshness,1000);
    const historyTimer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const body = await apiJSON('/api/history?seconds=30');
        renderEvents(body.events);
      } catch (error) {
        if (/local session/i.test(error.message || '')) {
          clearInterval(historyTimer);
          if (typeof showSessionExpired === 'function') showSessionExpired();
        }
      }
    }, 10000);

    $("conn-url").textContent = socketUrl;
    refreshIcons();
    connect();
    $("su-status").textContent = "Not checked · use Check updates to query Apple";
  
    function populateAlertRules(rules) {
      for(const [field,key] of [['cpu','CPU'],['gpu','GPU'],['memory','Memory usage']]) {
        $(`alert-${field}-enabled`).checked=rules[key].enabled;
        $(`alert-${field}-threshold`).value=rules[key].threshold;
        $(`alert-${field}-duration`).value=rules[key].duration_seconds;
      }
    }
    async function loadAlertRules() {
      try {populateAlertRules((await apiJSON('/api/alerts')).rules);$('alert-save').disabled=false;$('alert-status').textContent='Rules loaded · session only';}
      catch(error){$('alert-status').textContent=error.message;}
    }
    $('alert-form').addEventListener('submit',async event=>{
      event.preventDefault();$('alert-save').disabled=true;
      const rules={};
      for(const field of ['cpu','gpu','memory']) rules[field]={enabled:$(`alert-${field}-enabled`).checked,threshold:Number($(`alert-${field}-threshold`).value),duration_seconds:Number($(`alert-${field}-duration`).value)};
      try {populateAlertRules((await apiJSON('/api/alerts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(rules)})).rules);$('alert-status').textContent='Saved · duration tracking restarted';}
      catch(error){$('alert-status').textContent=error.message;}
      finally{$('alert-save').disabled=false;}
    });
    loadAlertRules();
