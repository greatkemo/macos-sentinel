// Bounded, tab-local application history. Only watch names persist in the browser.
let timelinePausedAt = null;
let timelineEvents = [];
let watchNames = [];
try {
  const saved = JSON.parse(localStorage.getItem('sentinel-watches') || '[]');
  if (Array.isArray(saved)) watchNames = [...new Set(saved.filter(n => typeof n === 'string' && n.trim() && n.length <= 200))].slice(0, 8);
} catch (_) { /* Storage may be unavailable. */ }
const watchHistory = new Map(watchNames.map(name => [name, []]));
const timelineColors = ['#60a5fa', '#c084fc', '#34d399', '#fbbf24', '#fb7185', '#22d3ee', '#f97316', '#a3e635'];
function timelineChart(id, datasets, disk = false) {
  return new Chart($(id), {type:'line', data:{datasets}, options:{responsive:true, maintainAspectRatio:false, animation:false,
    parsing:false, normalized:true, interaction:{mode:'index', intersect:false},
    plugins:{legend:{display:true}, tooltip:{callbacks:{title:items => items.length ? new Date(items[0].parsed.x).toLocaleTimeString() : ''}}},
    scales:{x:{type:'linear', ticks:{maxTicksLimit:5, callback:value=>new Date(value).toLocaleTimeString()}},
      y:{beginAtZero:true, ...(disk ? {max:100} : {}), title:{display:true,text:'%'}},
      ...(disk ? {disk:{position:'right',beginAtZero:true,grid:{drawOnChartArea:false},title:{display:true,text:'MiB/s'}}} : {})}}});
}
const correlatedChart = timelineChart('correlated-chart', ['CPU','GPU','Memory','Disk read','Disk write'].map((label,i)=>({label,data:[],borderColor:timelineColors[i],borderWidth:1.5,pointRadius:0,spanGaps:false,yAxisID:i<3?'y':'disk'})), true);
const watchChart = timelineChart('watch-chart', []);
function timelinePoints(samples, key, divisor=1) {
  const points=[];
  // At most 3,600 samples: retain short spikes instead of dropping every nth sample.
  samples.forEach((p,i)=>{
    const x=Date.parse(p.timestamp);
    if(i && x-Date.parse(samples[i-1].timestamp)>Math.max(15000,sampleInterval*3000)) points.push({x:x-1,y:null});
    points.push({x,y:p[key]==null?null:p[key]/divisor});
  });
  return points;
}
function renderTimeline(samples, now) {
  const start=now-selectedWindow*1000;
  const visible=samples.filter(p=>Date.parse(p.timestamp)<=now);
  ['cpu_percent','gpu_percent','memory_percent','disk_read_bps','disk_write_bps'].forEach((key,i)=>{
    correlatedChart.data.datasets[i].data=timelinePoints(visible,key,i<3?1:1048576);
  });
  const previousHidden = new Map();
  (watchChart.data.datasets || []).forEach((dataset, index) => {
    previousHidden.set(dataset.label, !watchChart.isDatasetVisible(index));
  });
  watchChart.data.datasets = watchNames.map((name, i) => ({
    label: name,
    borderColor: timelineColors[i],
    borderWidth: 1.5,
    pointRadius: 0,
    spanGaps: false,
    hidden: previousHidden.get(name) === true,
    data: timelinePoints(
      (watchHistory.get(name) || []).filter(
        (p) => Date.parse(p.timestamp) >= start && Date.parse(p.timestamp) <= now
      ),
      "cpu_percent"
    ),
  }));
  for (const chart of [correlatedChart, watchChart]) {
    chart.options.scales.x.min = start;
    chart.options.scales.x.max = now;
  }
  watchChart.data.datasets.forEach((dataset, index) => {
    watchChart.setDatasetVisibility(index, previousHidden.get(dataset.label) !== true);
  });
  correlatedChart.update("none");
  watchChart.update("none");
  const events = timelineEvents.filter(
    (e) => Date.parse(e.timestamp) >= start && Date.parse(e.timestamp) <= now
  );
  $('timeline-events').textContent=events.length?events.map(e=>`${new Date(e.timestamp).toLocaleTimeString()}: ${e.metric} above ${e.threshold}%`).join(' · '):'No sustained threshold events in this window.';
}
function renderWatchList() {
  $('watch-list').replaceChildren();
  if(!watchNames.length) $('watch-list').textContent='Choose an application to start recording its CPU history.';
  for(const name of watchNames) {
    const row=document.createElement('div');row.className='flex flex-wrap items-center gap-3 text-xs';
    const label=document.createElement('span');
    const samples=watchHistory.get(name)||[];
    const latest=timelinePausedAt?samples.findLast(p=>Date.parse(p.timestamp)<=timelinePausedAt):samples.at(-1);
    label.textContent=`${name} · ${latest ? `${latest.status} · ${latest.count} processes · CPU ${latest.cpu_percent==null?'—':latest.cpu_percent.toFixed(1)+'%'} · RSS ${latest.memory_percent==null?'—':latest.memory_percent.toFixed(1)+'% of RAM'}` : 'waiting for sample'}`;
    const remove=document.createElement('button');remove.type='button';remove.className='btn-ghost';remove.textContent='Remove';remove.setAttribute('aria-label',`Remove ${name} from watchlist`);
    remove.addEventListener('click',()=>{watchNames=watchNames.filter(n=>n!==name);watchHistory.delete(name);saveWatches();renderWatchList();renderHistory();});
    row.append(label,remove);$('watch-list').append(row);
  }
}
function saveWatches() {try {localStorage.setItem('sentinel-watches',JSON.stringify(watchNames));} catch (_) {showToast('Watch names cannot be saved in this browser.');}}
let lastWatchOptionsAt=0;
function recordWatches(data) {
  const now=Date.parse(data.timestamp);
  for(const name of watchNames) watchHistory.set(name,SentinelCore.mergeHistory(watchHistory.get(name)||[],[{timestamp:data.timestamp,...SentinelCore.watchSample(data.processes||[],name,data.processes_truncated)}]));
  if(!document.hidden && now-lastWatchOptionsAt>10000) {
    $('watch-options').replaceChildren();
    for(const name of [...new Set((data.processes||[]).map(p=>p.application||p.name))].sort()) {
      const option=document.createElement('option');option.value=name;$('watch-options').append(option);
    }
    lastWatchOptionsAt=now;
  }
  if(!timelinePausedAt && !document.hidden) renderWatchList();
}
$('watch-form').addEventListener('submit',event=>{
  event.preventDefault();const name=$('watch-name').value.trim();
  if(!name || watchNames.includes(name)) return;
  if(watchNames.length>=8) {showToast('Remove an application before adding another (limit 8).');return;}
  watchNames.push(name);watchHistory.set(name,[]);saveWatches();$('watch-name').value='';renderWatchList();renderHistory();
});
$('timeline-pause').addEventListener('click',()=>{
  timelinePausedAt=timelinePausedAt?null:Date.now();
  $('timeline-pause').textContent=timelinePausedAt?'Resume timeline':'Pause timeline';
  $('timeline-pause').setAttribute('aria-pressed',String(Boolean(timelinePausedAt)));
  $('timeline-state').textContent=timelinePausedAt?`Paused at ${new Date(timelinePausedAt).toLocaleTimeString()} · live collection continues`:'Live';
  renderWatchList();renderHistory();
});
renderWatchList();
