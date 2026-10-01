// Saved recordings remain separate from live history and aggregate exports.
let savedA=null, savedB=null, sessionGeneration=0, investigationGeneration=0;
const sessionChart=timelineChart('session-chart', []);
sessionChart.options.scales.x.ticks.callback=value=>`${Math.round(value)}s`;
sessionChart.options.scales.x.title={display:true,text:'Elapsed seconds from first sample'};
function sessionMessage(message) {$('session-status').textContent=message;}
async function refreshSessions() {
  const body=await apiJSON('/api/sessions');
  for(const [id,placeholder] of [['session-a','Choose a session'],['session-b','No comparison']]) {
    const select=$(id),selected=select.value;
    select.replaceChildren(new Option(placeholder,''));
    for(const item of body.sessions) select.append(new Option(`${item.name} · ${new Date(item.saved_at).toLocaleString()} · ${item.count} samples`,item.id));
    if(body.sessions.some(s=>s.id===selected)) select.value=selected;
  }
  $('session-auto').checked=body.enabled;
  $('session-apps').checked=body.include_apps;
  $('session-auto-save').disabled=false;
  sessionMessage(body.error?`Autosave error: ${body.error}`:`${body.sessions.length}/10 saved sessions · autosave ${body.enabled?'on':'off'}`);
}
$('session-save-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try {
    const result=await apiJSON('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('session-name').value,include_apps:$('session-apps').checked})});
    await refreshSessions();$('session-a').value=result.id;await loadComparison();sessionMessage('Session saved locally');
  } catch(error) {sessionMessage(error.message);} finally {button.disabled=false;}
});
$('session-auto-save').addEventListener('click',async()=>{
  $('session-auto-save').disabled=true;
  try {await apiJSON('/api/sessions/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:$('session-auto').checked,include_apps:$('session-apps').checked})});sessionMessage('Autosave settings saved. Disabling autosave keeps existing saved sessions.');}
  catch(error){sessionMessage(error.message);}finally{$('session-auto-save').disabled=false;}
});
async function loadComparison() {
  const generation=++sessionGeneration;
  $('session-delete').disabled=true;
  try {
    const a=$('session-a').value,b=$('session-b').value;
    const [first,second]=await Promise.all([a?apiJSON(`/api/sessions/${a}`):null,b?apiJSON(`/api/sessions/${b}`):null]);
    if(generation!==sessionGeneration) return;
    savedA=first;savedB=second;renderComparison();$('session-delete').disabled=!first;
  } catch(error) {if(generation===sessionGeneration){savedA=null;savedB=null;renderComparison();sessionMessage(error.message);}}
}
function renderComparison() {
  const key=$('session-metric').value,divisor=key.endsWith('_bps')?1048576:1,unit=divisor===1?'%':'MiB/s';
  const summaries=[];
  sessionChart.data.datasets=[];
  for(const [record,color,label] of [[savedA,'#60a5fa','A'],[savedB,'#c084fc','B']]) {
    if(!record?.samples.length) continue;
    const start=Date.parse(record.samples[0].timestamp);
    const values=record.samples.map(p=>p[key]).filter(Number.isFinite).map(n=>n/divisor);
    const stats=values.length?`sample mean ${(values.reduce((a,b)=>a+b,0)/values.length).toFixed(1)}${unit}, peak ${Math.max(...values).toFixed(1)}${unit}`:'unavailable';
    summaries.push(`${label}: ${record.name} — ${stats} (${record.samples.length} samples)`);
    sessionChart.data.datasets.push({label:`${label}: ${record.name}`,borderColor:color,pointRadius:0,borderWidth:1.5,spanGaps:false,
      data:record.samples.flatMap((p,i)=>{
        const x=(Date.parse(p.timestamp)-start)/1000;
        const point={x,y:Number.isFinite(p[key])?p[key]/divisor:null};
        return i && Date.parse(p.timestamp)-Date.parse(record.samples[i-1].timestamp)>Math.max(15000,(p.interval||1)*3000)?[{x:x-0.001,y:null},point]:[point];
      }),record});
  }
  $('session-comparison').textContent=summaries.length?summaries.join(' · ')+' · Means are sample-weighted; differing intervals and durations affect comparisons. Click a trace or inspect A’s peak for app rankings.':'Select a saved session.';
  sessionChart.options.scales.y.title.text=unit;
  sessionChart.update('none');
}
for(const id of ['session-a','session-b']) $(id).addEventListener('change',loadComparison);
$('session-metric').addEventListener('change',renderComparison);
$('session-refresh').addEventListener('click',()=>refreshSessions().then(loadComparison).catch(error=>sessionMessage(error.message)));
$('session-delete').addEventListener('click',async()=>{
  if(!savedA) return;
  const identity=savedA.id;
  if(!confirm(`Delete saved session “${savedA.name}”? This cannot be undone. If this is the active automatic session, autosave will recreate it unless disabled.`)) return;
  try {await apiJSON(`/api/sessions/${identity}`,{method:'DELETE'});await refreshSessions();await loadComparison();sessionMessage('Saved session deleted');}
  catch(error){sessionMessage(error.message);}
});
function showInvestigation(sample, source) {
  const detail=$('spike-detail');detail.replaceChildren();
  const heading=document.createElement('p');heading.textContent=`${source} · ${new Date(sample.timestamp).toLocaleString()}${sample.partial?' · partial process sample':''}`;detail.append(heading);
  for(const [key,label] of [['cpu_percent','CPU'],['memory_percent','Memory (summed RSS)']]) {
    const line=document.createElement('p');
    line.textContent=label+': '+[...sample.apps].sort((a,b)=>b[key]-a[key]).slice(0,5).map(p=>`${p.name}: ${p[key].toFixed(1)}% (${p.count} processes)`).join(' · ');
    detail.append(line);
  }
  const note=document.createElement('p');note.textContent='Only top groups were retained. CPU uses 100% per core; summed RSS may count shared memory more than once. Per-app GPU activity was not measured.';detail.append(note);
  detail.scrollIntoView({block:'nearest',behavior:'smooth'});
}
async function investigateTime(at,record=null) {
  const generation=++investigationGeneration;
  $('spike-detail').textContent='Loading historical application snapshot…';
  try {
    let sample;
    if(record) {
      if(!record.includes_apps) throw new Error('This saved session excludes application names. Enable inclusion when saving future sessions.');
      sample=(record.app_samples||[]).reduce((best,p)=>!best||Math.abs(Date.parse(p.timestamp)-at)<Math.abs(Date.parse(best.timestamp)-at)?p:best,null);
      if(!sample || Math.abs(Date.parse(sample.timestamp)-at)>Math.max(3000,sample.interval*1500)) throw new Error('No application snapshot near this point (gap or outside retained app history).');
    } else sample=await apiJSON(`/api/investigate?at=${at/1000}`);
    if(generation===investigationGeneration) showInvestigation(sample,record?`Saved: ${record.name}`:'Live session history');
  } catch(error) {if(generation===investigationGeneration)$('spike-detail').textContent=error.message;}
}
correlatedChart.options.onClick=(event,elements,chart)=>{
  const points=chart.getElementsAtEventForMode(event,'nearest',{intersect:false},false);
  if(points.length) {const point=points[0];investigateTime(chart.data.datasets[point.datasetIndex].data[point.index].x);}
};
sessionChart.options.onClick=(event,elements,chart)=>{
  const points=chart.getElementsAtEventForMode(event,'nearest',{intersect:false},false);
  if(points.length) {const p=points[0],dataset=chart.data.datasets[p.datasetIndex];investigateTime(Date.parse(dataset.record.samples[0].timestamp)+dataset.data[p.index].x*1000,dataset.record);}
};
$('investigate-button').addEventListener('click',()=>{const at=Date.parse($('investigate-time').value);if(Number.isFinite(at))investigateTime(at);else $('spike-detail').textContent='Choose a local date and time first.';});
function peak(samples,key) {return samples.filter(p=>Number.isFinite(p[key])).reduce((best,p)=>!best||p[key]>best[key]?p:best,null);}
$('investigate-peak').addEventListener('click',()=>{const end=timelinePausedAt||Date.now();const row=peak(historySamples.filter(p=>Date.parse(p.timestamp)>=end-selectedWindow*1000 && Date.parse(p.timestamp)<=end),'cpu_percent');if(row)investigateTime(Date.parse(row.timestamp));});
$('session-peak').addEventListener('click',()=>{const row=peak(savedA?.samples||[],$('session-metric').value);if(row)investigateTime(Date.parse(row.timestamp),savedA);else sessionMessage('Choose session A with measurements first.');});
let longTasks=0,longTaskMs=0;
if(globalThis.PerformanceObserver?.supportedEntryTypes?.includes('longtask')) {
  new PerformanceObserver(list=>{for(const entry of list.getEntries()){longTasks++;longTaskMs+=entry.duration;}}).observe({type:'longtask',buffered:true});
}
setInterval(()=>{
  if(document.hidden) return;
  const heap=performance.memory?.usedJSHeapSize;
  $('browser-overhead').textContent=`Browser: last telemetry handler ${lastRenderMs==null?'—':lastRenderMs.toFixed(1)} ms · ${globalThis.PerformanceObserver?.supportedEntryTypes?.includes('longtask')?`${longTasks} long tasks (${Math.round(longTaskMs)} ms) this page session`:'long-task measurement unsupported'} · JS heap ${heap?formatBytes(heap):'unavailable'}. Heap is browser-estimated, not total tab RAM. Hidden tabs skip chart rendering.`;
},2000);
refreshSessions().catch(error=>sessionMessage(error.message));
